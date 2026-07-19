from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil

from pypdf import PdfReader, PdfWriter

from .completion import (
    CompletionBatchResult,
    CompletionItem,
    ProcessingBatchItem,
    _duplicate_manifest_indexes,
    _load_manifest,
    _output_path,
    _relative_path,
    _validate_item,
)
from .completion_matching import plan_completion_matches
from .date_completion import SigningDateResult, SigningDateStatus, prepare_returned_page_with_date
from .pdf_ops import replace_last_page_object, sha256_file
from .processing_report import write_processing_report
from .returned_page_titles import OCRPageFailure, read_returned_page_titles


_REVIEW_PDF_NAME = "dated-returned-pages.pdf"
_REVIEW_MANIFEST_NAME = "review-manifest.json"
_REVIEW_MANIFEST_VERSION = 1


@dataclass(frozen=True)
class DateReviewBatchResult:
    items: tuple[CompletionItem, ...]
    unused_pages: tuple[int, ...]
    review_root: Path
    review_pdf: Path
    manifest_path: Path
    ocr_failures: tuple[OCRPageFailure, ...] = ()

    @property
    def succeeded(self):
        return sum(item.status == "review_ready" for item in self.items)


@dataclass(frozen=True)
class _ValidatedBatch:
    manifest: dict
    records: list
    outcomes: list
    valid_entries: tuple
    converted_paths: dict


def _pending_date_result(signing_date):
    return SigningDateResult(
        SigningDateStatus.NOT_REQUESTED
        if signing_date is None
        else SigningDateStatus.NOT_APPLIED
    )


def _validate_batch(batch_root, output_root, pending_date_result):
    batch_root = Path(batch_root)
    output_root = Path(output_root)
    manifest = _load_manifest(batch_root)
    records = manifest["items"]
    duplicate_id_indexes = _duplicate_manifest_indexes(
        records,
        "working_paper_id",
        lambda value: value,
    )
    duplicate_path_indexes = _duplicate_manifest_indexes(
        records,
        "working_paper_path",
        lambda value: os.path.normcase(str(_output_path(output_root, Path(value)))),
    )
    duplicate_pdf_indexes = _duplicate_manifest_indexes(
        records,
        "converted_pdf",
        lambda value: os.path.normcase(
            str(_relative_path(batch_root, Path(value), "完整底稿 PDF"))
        ),
    )

    outcomes = [None] * len(records)
    valid_entries = []
    converted_paths = {}
    for index, record in enumerate(records):
        working_paper_id = (
            record.get("working_paper_id", "") if isinstance(record, dict) else ""
        )
        try:
            if isinstance(record, dict) and record.get("status") == "failed":
                outcomes[index] = CompletionItem(
                    working_paper_id,
                    "conversion_failed",
                    reason=str(record.get("error") or "底稿文件转换失败"),
                    date_result=pending_date_result,
                )
                continue
            if index in duplicate_id_indexes:
                raise ValueError("底稿文件标识在处理批次中重复")
            if index in duplicate_path_indexes:
                raise ValueError("底稿文件相对位置在处理批次中重复")
            if index in duplicate_pdf_indexes:
                raise ValueError("完整底稿 PDF 路径在处理批次中重复")
            item = ProcessingBatchItem.from_manifest(record)
            working_paper_id = item.working_paper_id
            converted_paths[working_paper_id] = _validate_item(batch_root, item)
            _output_path(output_root, item.working_paper_path)
            valid_entries.append((index, item))
        except (KeyError, OSError, TypeError, ValueError) as error:
            outcomes[index] = CompletionItem(
                working_paper_id,
                "invalid_batch",
                reason=str(error),
                date_result=pending_date_result,
            )
    return _ValidatedBatch(
        manifest,
        records,
        outcomes,
        tuple(valid_entries),
        converted_paths,
    )


def _unmatched_outcome(item, matching, pending_date_result):
    ambiguity = matching.ambiguities.get(item.working_paper_id)
    if ambiguity is not None:
        returned_page, score = ambiguity
        return CompletionItem(
            item.working_paper_id,
            "ambiguous",
            returned_page=returned_page,
            score=score,
            reason="回章页对多个不同标题候选并列达到自动回拼阈值",
            date_result=pending_date_result,
        )
    candidate = matching.low_confidence.get(item.working_paper_id)
    if candidate is None:
        return CompletionItem(
            item.working_paper_id,
            "unmatched",
            reason="没有可匹配的回章页",
            date_result=pending_date_result,
        )
    returned_page, score = candidate
    return CompletionItem(
        item.working_paper_id,
        "low_confidence",
        returned_page=returned_page,
        score=score,
        reason="最佳候选未达到 90 分自动回拼阈值",
        date_result=pending_date_result,
    )


def _write_review_pdf(returned_pdf, review_pdf, matched_templates, signing_date):
    try:
        reader = PdfReader(str(returned_pdf))
        page_count = len(reader.pages)
    except Exception as error:
        raise ValueError(f"无法读取回章页合集 PDF：{returned_pdf}") from error

    writer = PdfWriter()
    date_results = {}
    for page_index, page in enumerate(reader.pages):
        page_number = page_index + 1
        if page_number not in matched_templates:
            writer.add_page(page)
            continue
        try:
            dated_page = prepare_returned_page_with_date(
                returned_pdf,
                page_index,
                signing_date,
                template_pdf=matched_templates[page_number][0],
                template_page_index=matched_templates[page_number][1],
            )
            writer.add_page(dated_page.page)
            date_results[page_number] = dated_page.result
        except Exception as error:
            writer.add_page(page)
            date_results[page_number] = SigningDateResult(
                SigningDateStatus.FAILED,
                f"日期补齐失败：{str(error) or error.__class__.__name__}",
            )

    review_pdf.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = review_pdf.with_suffix(".pdf.tmp")
    try:
        with temporary_path.open("wb") as output:
            writer.write(output)
        temporary_path.replace(review_pdf)
    finally:
        temporary_path.unlink(missing_ok=True)
    return page_count, date_results


def _item_record(item):
    return {
        "working_paper_id": item.working_paper_id,
        "status": item.status,
        "returned_page": item.returned_page,
        "score": item.score,
        "reason": item.reason,
        "date_status": item.date_status,
        "date_reason": item.date_reason,
    }


def _write_review_manifest(
    batch_root,
    returned_pdf,
    review_root,
    page_count,
    items,
    unused_pages,
    ocr_failures,
):
    manifest_path = review_root / _REVIEW_MANIFEST_NAME
    payload = {
        "version": _REVIEW_MANIFEST_VERSION,
        "batch_manifest_sha256": sha256_file(Path(batch_root) / "manifest.json"),
        "source_returned_pdf": str(Path(returned_pdf).resolve()),
        "review_pdf": _REVIEW_PDF_NAME,
        "page_count": page_count,
        "items": [_item_record(item) for item in items],
        "unused_pages": list(unused_pages),
        "ocr_failures": [
            {"page": failure.page, "reason": failure.reason}
            for failure in ocr_failures
        ],
    }
    temporary_path = manifest_path.with_suffix(".json.tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(manifest_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return manifest_path


def create_date_review(
    batch_root,
    returned_pdf,
    review_root,
    ocr_engine=None,
    signing_date=None,
):
    batch_root = Path(batch_root)
    returned_pdf = Path(returned_pdf)
    review_root = Path(review_root)
    review_pdf = review_root / _REVIEW_PDF_NAME
    if returned_pdf.resolve() == review_pdf.resolve():
        raise ValueError("回章页合集不能与日期确认稿使用同一路径")

    pending_date_result = _pending_date_result(signing_date)
    validated = _validate_batch(batch_root, review_root, pending_date_result)
    valid_items = [item for _, item in validated.valid_entries]
    returned_pages = read_returned_page_titles(
        returned_pdf,
        (item.title for item in valid_items),
        ocr_engine,
    )
    matching = plan_completion_matches(valid_items, returned_pages.titles)
    matched_templates = {}
    for _, item in validated.valid_entries:
        match = matching.matches.get(item.working_paper_id)
        if match is None:
            continue
        matched_templates.setdefault(
            match.returned_page,
            (
                validated.converted_paths[item.working_paper_id],
                item.pdf_page_count - 1,
            ),
        )
    page_count, date_results = _write_review_pdf(
        returned_pdf,
        review_pdf,
        matched_templates,
        signing_date,
    )

    outcomes = validated.outcomes
    for index, item in validated.valid_entries:
        match = matching.matches.get(item.working_paper_id)
        if match is None:
            outcomes[index] = _unmatched_outcome(
                item,
                matching,
                pending_date_result,
            )
            continue
        outcomes[index] = CompletionItem(
            item.working_paper_id,
            "review_ready",
            returned_page=match.returned_page,
            score=match.score,
            output_path=review_pdf,
            date_result=date_results[match.returned_page],
        )

    unused_pages = tuple(
        sorted(set(matching.unused_pages) | set(returned_pages.untitled_pages))
    )
    manifest_path = _write_review_manifest(
        batch_root,
        returned_pdf,
        review_root,
        page_count,
        outcomes,
        unused_pages,
        returned_pages.ocr_failures,
    )
    return DateReviewBatchResult(
        items=tuple(outcomes),
        unused_pages=unused_pages,
        review_root=review_root,
        review_pdf=review_pdf,
        manifest_path=manifest_path,
        ocr_failures=returned_pages.ocr_failures,
    )


def _load_review_manifest(review_root):
    manifest_path = Path(review_root) / _REVIEW_MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取日期确认清单：{manifest_path}") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("version") != _REVIEW_MANIFEST_VERSION
        or not isinstance(manifest.get("items"), list)
    ):
        raise ValueError("日期确认清单格式不受支持")
    return manifest


def _stored_date_result(record):
    try:
        status = SigningDateStatus(record["date_status"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("日期确认清单包含无效日期状态") from error
    reason = record.get("date_reason")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("日期确认清单包含无效日期说明")
    return SigningDateResult(status, reason)


def _stored_item(record, expected_id, page_count):
    if not isinstance(record, dict) or record.get("working_paper_id") != expected_id:
        raise ValueError("日期确认清单与处理批次的底稿顺序不一致")
    status = record.get("status")
    if status not in {
        "review_ready",
        "low_confidence",
        "ambiguous",
        "unmatched",
        "conversion_failed",
        "invalid_batch",
    }:
        raise ValueError("日期确认清单包含无效处理状态")
    returned_page = record.get("returned_page")
    if returned_page is not None and (
        not isinstance(returned_page, int)
        or isinstance(returned_page, bool)
        or not 1 <= returned_page <= page_count
    ):
        raise ValueError("日期确认清单包含超出范围的回章页码")
    if status == "review_ready" and returned_page is None:
        raise ValueError("日期确认清单中的待确认项目缺少回章页码")
    score = record.get("score")
    if score is not None and (
        not isinstance(score, int) or isinstance(score, bool)
    ):
        raise ValueError("日期确认清单包含无效相似度")
    reason = record.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("日期确认清单包含无效处理说明")
    return CompletionItem(
        expected_id,
        status,
        returned_page=returned_page,
        score=score,
        reason=reason,
        date_result=_stored_date_result(record),
    )


def _stored_ocr_failures(records):
    failures = []
    for record in records:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("page"), int)
            or isinstance(record.get("page"), bool)
            or not isinstance(record.get("reason"), str)
        ):
            raise ValueError("日期确认清单包含无效 OCR 异常记录")
        failures.append(OCRPageFailure(record["page"], record["reason"]))
    return tuple(failures)


def finalize_date_review(
    batch_root,
    review_root,
    output_root,
    *,
    confirmed=False,
):
    if not confirmed:
        raise PermissionError("尚未取得用户确认，不能开始回拼")

    batch_root = Path(batch_root)
    review_root = Path(review_root)
    output_root = Path(output_root)
    review_manifest = _load_review_manifest(review_root)
    batch_manifest_path = batch_root / "manifest.json"
    try:
        current_batch_hash = sha256_file(batch_manifest_path)
    except OSError as error:
        raise ValueError(f"无法读取处理批次清单：{batch_manifest_path}") from error
    if current_batch_hash != review_manifest.get("batch_manifest_sha256"):
        raise ValueError("日期确认后处理批次清单已变化，请重新生成日期确认稿")

    review_pdf = _relative_path(
        review_root,
        review_manifest.get("review_pdf", ""),
        "日期确认稿",
    )
    try:
        review_reader = PdfReader(str(review_pdf))
        actual_page_count = len(review_reader.pages)
    except Exception as error:
        raise ValueError(f"无法读取日期确认稿 PDF：{review_pdf}") from error
    expected_page_count = review_manifest.get("page_count")
    if (
        not isinstance(expected_page_count, int)
        or isinstance(expected_page_count, bool)
        or expected_page_count < 1
    ):
        raise ValueError("日期确认清单包含无效页数")
    if actual_page_count != expected_page_count:
        raise ValueError(
            f"日期确认稿页数已变化：应为 {expected_page_count} 页，实际为 {actual_page_count} 页"
        )

    validated = _validate_batch(
        batch_root,
        output_root,
        SigningDateResult(SigningDateStatus.NOT_APPLIED),
    )
    stored_records = review_manifest["items"]
    if len(stored_records) != len(validated.records):
        raise ValueError("日期确认清单与处理批次的底稿数量不一致")
    stored_items = []
    for record, batch_record in zip(stored_records, validated.records):
        expected_id = (
            batch_record.get("working_paper_id", "")
            if isinstance(batch_record, dict)
            else ""
        )
        stored_items.append(_stored_item(record, expected_id, actual_page_count))
    unused_pages = review_manifest.get("unused_pages")
    if not isinstance(unused_pages, list) or any(
        not isinstance(page, int) or isinstance(page, bool) for page in unused_pages
    ):
        raise ValueError("日期确认清单包含无效未使用页码")
    ocr_records = review_manifest.get("ocr_failures", [])
    if not isinstance(ocr_records, list):
        raise ValueError("日期确认清单包含无效 OCR 异常记录")
    ocr_failures = _stored_ocr_failures(ocr_records)

    valid_by_index = dict(validated.valid_entries)
    completed_root = output_root / "completed-pdfs"
    if completed_root.exists():
        shutil.rmtree(completed_root)
    completed_root.mkdir(parents=True)

    outcomes = list(validated.outcomes)
    for index, stored_item in enumerate(stored_items):
        if outcomes[index] is not None:
            continue
        if stored_item.status != "review_ready":
            outcomes[index] = stored_item
            continue
        item = valid_by_index[index]
        output_path = None
        try:
            output_path = _output_path(output_root, item.working_paper_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            replace_last_page_object(
                validated.converted_paths[item.working_paper_id],
                review_reader.pages[stored_item.returned_page - 1],
                output_path,
            )
            outcomes[index] = CompletionItem(
                item.working_paper_id,
                "completed",
                returned_page=stored_item.returned_page,
                score=stored_item.score,
                output_path=output_path,
                date_result=stored_item.date_result,
            )
        except Exception as error:
            if output_path is not None:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
            outcomes[index] = CompletionItem(
                item.working_paper_id,
                "failed",
                returned_page=stored_item.returned_page,
                score=stored_item.score,
                reason=str(error) or error.__class__.__name__,
                date_result=stored_item.date_result,
            )

    report_path = write_processing_report(
        batch_root,
        review_pdf,
        output_root,
        validated.records,
        outcomes,
        ocr_failures,
        working_paper_root=validated.manifest.get("working_paper_root"),
        seal_pages_name=validated.manifest.get("seal_pages", "seal-pages.pdf"),
        returned_pdf_label="日期确认稿",
        returned_page_prefix="日期确认稿",
    )
    return CompletionBatchResult(
        items=tuple(outcomes),
        unused_pages=tuple(sorted(set(unused_pages))),
        output_root=output_root,
        report_path=report_path,
        ocr_failures=ocr_failures,
    )

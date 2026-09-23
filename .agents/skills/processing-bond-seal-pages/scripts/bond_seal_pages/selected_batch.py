from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable
import queue
import threading
import uuid

from pypdf import PdfReader, PdfWriter

from .batch_contract import (
    QUICK_BATCH_FORMAT,
    QUICK_BATCH_VERSION,
    QuickBatchRequest,
    SelectedBatchCancelledError,
    SelectedBatchProcessingError,
    parse_quick_batch_request,
)
from .pdf_ops import sha256_file


ProgressCallback = Callable[[dict[str, Any]], None]


def _report_progress(
    progress: ProgressCallback | None,
    *,
    stage: str,
    completed: int,
    total: int,
    path: Path | None = None,
) -> None:
    if progress is None:
        return
    event: dict[str, Any] = {
        "stage": stage,
        "completed": completed,
        "total": total,
    }
    if path is not None:
        event["path"] = str(path)
    progress(event)


def _working_paper_id(path: Path, source_root: Path) -> str:
    return path.relative_to(source_root).as_posix()


def _record_failure(path: Path, error: BaseException | str) -> dict[str, str]:
    if isinstance(error, BaseException):
        reason = str(error) or error.__class__.__name__
    else:
        reason = error
    return {"path": str(path), "reason": reason}


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise SelectedBatchCancelledError("用户已取消快速合集生成")


def _close_pdf_reader(reader: PdfReader) -> None:
    stream = getattr(reader, "stream", None)
    close = getattr(stream, "close", None)
    if close is not None:
        close()


def _convert_with_cancellation(
    converter: Any,
    input_path: Path,
    output_path: Path,
    cancel_event: threading.Event | None,
) -> None:
    if cancel_event is None:
        converter.convert(input_path, output_path)
        return

    results: queue.Queue[tuple[bool, BaseException | None]] = queue.Queue(maxsize=1)

    def run_conversion() -> None:
        try:
            converter.convert(input_path, output_path)
            results.put((True, None))
        except BaseException as error:
            results.put((False, error))

    conversion_thread = threading.Thread(
        target=run_conversion,
        name="bond-seal-word-conversion",
        daemon=True,
    )
    conversion_thread.start()
    while conversion_thread.is_alive():
        if cancel_event.wait(0.05):
            cancel = getattr(converter, "cancel", None)
            if callable(cancel):
                cancel()
            conversion_thread.join()
            raise SelectedBatchCancelledError("用户已取消快速合集生成")
        conversion_thread.join(0.05)

    succeeded, error = results.get_nowait()
    if cancel_event.is_set():
        cancel = getattr(converter, "cancel", None)
        if callable(cancel):
            cancel()
        raise SelectedBatchCancelledError("用户已取消快速合集生成")
    if not succeeded and error is not None:
        raise error


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _output_names(output_directory: Path, number: int) -> tuple[str, Path, Path]:
    stem = "签署页合集" if number == 1 else f"签署页合集 ({number})"
    return (
        stem,
        output_directory / f"{stem}.pdf",
        output_directory / f"{stem}_处理数据_请勿删除",
    )


def _publish_without_overwrite(
    staging_collection: Path,
    staging_batch: Path,
    manifest: dict[str, Any],
    output_directory: Path,
) -> tuple[Path, Path]:
    number = 1
    while True:
        stem, collection_path, batch_dir = _output_names(output_directory, number)
        lock_path = output_directory / f".{stem}.bond-seal-publish.lock"
        try:
            lock_descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            number += 1
            continue

        published_batch: Path | None = None
        published_collection = False
        try:
            if (collection_path.exists() or collection_path.is_symlink()
                    or batch_dir.exists() or batch_dir.is_symlink()):
                number += 1
                continue

            manifest["collection"] = collection_path.name
            _write_json(staging_batch / "quick_batch.json", manifest)

            try:
                os.rename(staging_batch, batch_dir)
                published_batch = batch_dir
            except FileExistsError:
                number += 1
                continue

            try:
                # On Windows os.rename fails atomically if another process has
                # created the destination. On other platforms use a hard link,
                # which is an atomic create-if-absent operation on the same volume.
                if os.name == "nt":
                    os.rename(staging_collection, collection_path)
                else:
                    os.link(staging_collection, collection_path)
                    published_collection = True
                    try:
                        staging_collection.unlink()
                    except OSError:
                        pass
                published_collection = True
                return collection_path, batch_dir
            except FileExistsError:
                # The sidecar belongs to this call, so put it back into staging
                # before retrying the next name. Never discard its cached PDFs.
                try:
                    os.rename(batch_dir, staging_batch)
                    published_batch = None
                except OSError as error:
                    raise RuntimeError(
                        f"合集命名冲突后无法恢复暂存数据：{error}"
                    ) from error
                number += 1
                continue
        except Exception:
            if published_batch is not None and not published_collection:
                shutil.rmtree(published_batch, ignore_errors=True)
            raise
        finally:
            os.close(lock_descriptor)
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def _copy_group_pdf(
    source_pdf: Path,
    *,
    batch_pdf_root: Path,
    members: list[Path],
    source_root: Path,
) -> dict[str, tuple[Path, str]]:
    copied: dict[str, tuple[Path, str]] = {}
    for member in members:
        item_id = _working_paper_id(member, source_root)
        relative_pdf = Path("pdfs") / f"{member.name}.pdf"
        destination = batch_pdf_root / f"{member.name}.pdf"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_pdf, destination)
        copied[item_id] = (relative_pdf, sha256_file(destination))
    return copied


def collect_selected_batch(
    request: dict,
    *,
    converter_factory: Callable[[], Any] | None = None,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Create an all-or-nothing quick signing-page batch from explicit Word paths.

    The request never triggers a directory scan. Repeated source hashes share one
    collection page, while every selected path receives its own manifest item and
    cached full-document PDF.
    """
    parsed: QuickBatchRequest = parse_quick_batch_request(request)
    if cancel_event is not None and not isinstance(cancel_event, threading.Event):
        raise TypeError("cancel_event 必须是 threading.Event")
    _raise_if_cancelled(cancel_event)
    selected_count = len(parsed.files)
    parsed.output_directory.mkdir(parents=True, exist_ok=True)

    hashes: dict[str, str] = {}
    failures: list[dict[str, str]] = []
    for index, path in enumerate(parsed.files, start=1):
        _raise_if_cancelled(cancel_event)
        try:
            hashes[_working_paper_id(path, parsed.source_root)] = sha256_file(path)
        except Exception as error:
            failures.append(_record_failure(path, error))
        _report_progress(
            progress,
            stage="hashing",
            completed=index,
            total=selected_count,
            path=path,
        )
    if failures:
        raise SelectedBatchProcessingError(
            failures,
            selected_count=selected_count,
        )

    groups: OrderedDict[str, list[Path]] = OrderedDict()
    for path in parsed.files:
        groups.setdefault(hashes[_working_paper_id(path, parsed.source_root)], []).append(
            path
        )

    if converter_factory is None:
        from .word_conversion import create_platform_word_pdf_converter

        converter_factory = create_platform_word_pdf_converter

    batch_id = str(uuid.uuid4())
    failures = []
    item_by_id: dict[str, dict[str, Any]] = {}
    unique_count = len(groups)

    with tempfile.TemporaryDirectory(
        prefix=".bond-seal-quick-batch-",
        dir=parsed.output_directory,
    ) as temporary_directory:
        staging_root = Path(temporary_directory)
        staging_batch = staging_root / "sidecar"
        staging_pdf_root = staging_batch / "pdfs"
        staging_pdf_root.mkdir(parents=True)
        staging_collection = staging_root / "collection.pdf"
        collection_writer = PdfWriter()

        converter = converter_factory()
        close_failure: dict[str, str] | None = None
        try:
            for group_index, (source_hash, members) in enumerate(
                groups.items(),
                start=1,
            ):
                _raise_if_cancelled(cancel_event)
                representative: Path | None = None
                representative_pdf: Path | None = None
                representative_reader: PdfReader | None = None
                conversion_errors: list[str] = []

                for attempt_index, candidate in enumerate(members, start=1):
                    _raise_if_cancelled(cancel_event)
                    attempt_pdf = staging_root / (
                        f"conversion-{group_index}-{attempt_index}.pdf"
                    )
                    reader: PdfReader | None = None
                    try:
                        _convert_with_cancellation(
                            converter, candidate, attempt_pdf, cancel_event
                        )
                        if not attempt_pdf.is_file() or attempt_pdf.stat().st_size == 0:
                            raise ValueError("Word 转换器没有生成有效 PDF")
                        reader = PdfReader(str(attempt_pdf))
                        if not reader.pages:
                            raise ValueError("转换后的 PDF 没有页面")
                        representative = candidate
                        representative_pdf = attempt_pdf
                        representative_reader = reader
                        break
                    except SelectedBatchCancelledError:
                        if reader is not None:
                            _close_pdf_reader(reader)
                        raise
                    except Exception as error:
                        if reader is not None:
                            _close_pdf_reader(reader)
                        attempt_pdf.unlink(missing_ok=True)
                        conversion_errors.append(
                            f"{candidate.name}：{str(error) or error.__class__.__name__}"
                        )

                if (
                    representative is None
                    or representative_pdf is None
                    or representative_reader is None
                ):
                    reason = "；".join(conversion_errors) or "Word 转换失败"
                    failures.extend(
                        _record_failure(member, reason) for member in members
                    )
                    _report_progress(
                        progress,
                        stage="converting",
                        completed=group_index,
                        total=unique_count,
                        path=members[0],
                    )
                    continue

                representative_id = _working_paper_id(
                    representative,
                    parsed.source_root,
                )
                page_count = len(representative_reader.pages)
                try:
                    collection_writer.add_page(representative_reader.pages[-1])
                finally:
                    _close_pdf_reader(representative_reader)
                    representative_reader = None
                seal_page = len(collection_writer.pages)
                try:
                    copied_pdfs = _copy_group_pdf(
                        representative_pdf,
                        batch_pdf_root=staging_pdf_root,
                        members=members,
                        source_root=parsed.source_root,
                    )
                except Exception as error:
                    failures.extend(
                        _record_failure(member, error) for member in members
                    )
                    representative_pdf.unlink(missing_ok=True)
                    _report_progress(
                        progress,
                        stage="converting",
                        completed=group_index,
                        total=unique_count,
                        path=members[0],
                    )
                    continue

                for member in members:
                    item_id = _working_paper_id(member, parsed.source_root)
                    relative_pdf, pdf_hash = copied_pdfs[item_id]
                    item_by_id[item_id] = {
                        "working_paper_id": item_id,
                        "working_paper_path": item_id,
                        "working_paper_sha256": source_hash,
                        "converted_pdf": relative_pdf.as_posix(),
                        "pdf_sha256": pdf_hash,
                        "pdf_page_count": page_count,
                        "seal_page": seal_page,
                        "reuse_of": representative_id,
                        "duplicate_group_sha256": source_hash,
                    }

                representative_pdf.unlink(missing_ok=True)
                _report_progress(
                    progress,
                    stage="converting",
                    completed=group_index,
                    total=unique_count,
                    path=members[0],
                )
        finally:
            try:
                converter.close()
            except Exception as error:
                close_failure = _record_failure(
                    parsed.files[0],
                    f"关闭 Word 转换器失败：{str(error) or error.__class__.__name__}",
                )

        if close_failure is not None:
            failures.append(close_failure)

        for path in parsed.files:
            try:
                item_id = _working_paper_id(path, parsed.source_root)
                if sha256_file(path) != hashes[item_id]:
                    failures.append(
                        _record_failure(path, "处理期间源 Word 内容发生变化")
                    )
            except Exception as error:
                failures.append(_record_failure(path, error))

        if failures:
            raise SelectedBatchProcessingError(
                failures,
                selected_count=selected_count,
                page_count=len(collection_writer.pages),
            )

        if len(item_by_id) != selected_count:
            raise SelectedBatchProcessingError(
                [
                    {
                        "path": str(path),
                        "reason": "文件未能写入完整的批次清单",
                    }
                    for path in parsed.files
                    if _working_paper_id(path, parsed.source_root) not in item_by_id
                ],
                selected_count=selected_count,
                page_count=len(collection_writer.pages),
            )

        with staging_collection.open("wb") as output:
            collection_writer.write(output)

        collection_reader: PdfReader | None = None
        try:
            collection_reader = PdfReader(str(staging_collection))
            verified_collection_pages = len(collection_reader.pages)
        except Exception as error:
            raise SelectedBatchProcessingError(
                [_record_failure(staging_collection, f"合集 PDF 校验失败：{error}")],
                selected_count=selected_count,
                page_count=len(collection_writer.pages),
            ) from error
        finally:
            if collection_reader is not None:
                _close_pdf_reader(collection_reader)
        if verified_collection_pages != unique_count:
            raise SelectedBatchProcessingError(
                [
                    {
                        "path": str(staging_collection),
                        "reason": (
                            "合集页数与不同源文件 SHA-256 数量不一致："
                            f"{verified_collection_pages} != {unique_count}"
                        ),
                    }
                ],
                selected_count=selected_count,
                page_count=verified_collection_pages,
            )

        manifest: dict[str, Any] = {
            "format": QUICK_BATCH_FORMAT,
            "version": QUICK_BATCH_VERSION,
            "batch_id": batch_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_root": str(parsed.source_root),
            "collection": "",
            "collection_sha256": sha256_file(staging_collection),
            "selected_count": selected_count,
            "page_count": unique_count,
            "items": [
                item_by_id[_working_paper_id(path, parsed.source_root)]
                for path in parsed.files
            ],
        }
        _raise_if_cancelled(cancel_event)
        _report_progress(
            progress,
            stage="publishing",
            completed=0,
            total=1,
        )
        _raise_if_cancelled(cancel_event)
        collection_path, batch_dir = _publish_without_overwrite(
            staging_collection,
            staging_batch,
            manifest,
            parsed.output_directory,
        )

    manifest_path = batch_dir / "quick_batch.json"
    _report_progress(progress, stage="completed", completed=1, total=1)
    return {
        "status": "completed",
        "collection": str(collection_path.resolve()),
        "batch_dir": str(batch_dir.resolve()),
        "manifest": str(manifest_path.resolve()),
        "selected_count": selected_count,
        "page_count": unique_count,
    }

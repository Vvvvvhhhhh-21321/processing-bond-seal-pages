from dataclasses import dataclass
import json
from pathlib import Path
import shutil

from pypdf import PdfReader

from .completion_matching import plan_completion_matches
from .pdf_ops import replace_last_page, sha256_file
from .seal_page_titles import extract_pdf_page_title
from .titles import ReturnedTitle, normalize_title


@dataclass(frozen=True)
class CompletionItem:
    working_paper_id: str
    status: str
    returned_page: int | None = None
    score: int | None = None
    output_path: Path | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CompletionBatchResult:
    items: tuple[CompletionItem, ...]
    unused_pages: tuple[int, ...]
    output_root: Path

    @property
    def succeeded(self):
        return sum(item.status == "completed" for item in self.items)


@dataclass(frozen=True)
class ProcessingBatchItem:
    working_paper_id: str
    working_paper_path: Path
    status: str
    title: str
    normalized_title: str
    converted_pdf: Path
    pdf_page_count: int
    pdf_sha256: str

    @classmethod
    def from_manifest(cls, record):
        return cls(
            working_paper_id=record["working_paper_id"],
            working_paper_path=Path(record["working_paper_path"]),
            status=record["status"],
            title=record["title"],
            normalized_title=record["normalized_title"],
            converted_pdf=Path(record["converted_pdf"]),
            pdf_page_count=record["pdf_page_count"],
            pdf_sha256=record["pdf_sha256"],
        )


def _relative_path(root, value, role):
    root = root.resolve()
    candidate = (root / value).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError(f"{role}路径超出处理目录：{value}")
    return candidate


def _load_manifest(batch_root):
    manifest_path = batch_root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取处理批次清单：{manifest_path}") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("version") != 1
        or not isinstance(manifest.get("items"), list)
    ):
        raise ValueError("处理批次清单格式不受支持")
    return manifest


def _validate_item(batch_root, item):
    if item.status != "ready":
        raise ValueError("底稿未在第一阶段成功生成")
    converted_path = _relative_path(
        batch_root,
        item.converted_pdf,
        "完整底稿 PDF",
    )
    if sha256_file(converted_path) != item.pdf_sha256:
        raise ValueError("完整底稿 PDF 校验值不一致")
    try:
        reader = PdfReader(str(converted_path))
        page_count = len(reader.pages)
    except Exception as error:
        raise ValueError("无法读取完整底稿 PDF") from error
    if page_count != item.pdf_page_count:
        raise ValueError("完整底稿 PDF 页数不一致")
    if normalize_title(item.title) != item.normalized_title:
        raise ValueError("底稿标题映射不一致")
    return converted_path


def _read_returned_titles(returned_pdf):
    try:
        reader = PdfReader(str(returned_pdf))
        pages = list(reader.pages)
    except Exception as error:
        raise ValueError(f"无法读取回章页合集：{returned_pdf}") from error
    titles = []
    untitled_pages = []
    for page_number, page in enumerate(pages, start=1):
        try:
            title = extract_pdf_page_title(page)
        except Exception:
            title = None
        if title:
            titles.append(ReturnedTitle(page_number, title))
        else:
            untitled_pages.append(page_number)
    return tuple(titles), tuple(untitled_pages)


def _output_path(output_root, working_paper_path):
    relative = Path(working_paper_path)
    return _relative_path(
        output_root,
        Path("completed-pdfs") / relative.parent / f"{relative.name}.pdf",
        "盖章版 PDF",
    )


def complete_processing_batch(batch_root, returned_pdf, output_root):
    batch_root = Path(batch_root)
    returned_pdf = Path(returned_pdf)
    output_root = Path(output_root)
    completed_root = output_root / "completed-pdfs"
    if completed_root.exists():
        shutil.rmtree(completed_root)
    completed_root.mkdir(parents=True)

    manifest = _load_manifest(batch_root)
    outcomes = {}
    valid_items = []
    converted_paths = {}
    item_order = []
    for record in manifest["items"]:
        working_paper_id = record.get("working_paper_id", "")
        item_order.append(working_paper_id)
        try:
            item = ProcessingBatchItem.from_manifest(record)
            working_paper_id = item.working_paper_id
            item_order[-1] = working_paper_id
            converted_paths[working_paper_id] = _validate_item(batch_root, item)
            _output_path(output_root, item.working_paper_path)
            valid_items.append(item)
        except (KeyError, OSError, TypeError, ValueError) as error:
            outcomes[working_paper_id] = CompletionItem(
                working_paper_id,
                "invalid_batch",
                reason=str(error),
            )

    returned_titles, untitled_pages = _read_returned_titles(returned_pdf)
    matching = plan_completion_matches(valid_items, returned_titles)
    for item in valid_items:
        working_paper_id = item.working_paper_id
        match = matching.matches.get(working_paper_id)
        if match is None:
            ambiguity = matching.ambiguities.get(working_paper_id)
            if ambiguity is not None:
                returned_page, score = ambiguity
                outcomes[working_paper_id] = CompletionItem(
                    working_paper_id,
                    "ambiguous",
                    returned_page=returned_page,
                    score=score,
                    reason="回章页对多个不同标题候选并列达到自动回拼阈值",
                )
                continue
            candidate = matching.low_confidence.get(working_paper_id)
            if candidate is None:
                outcomes[working_paper_id] = CompletionItem(
                    working_paper_id,
                    "unmatched",
                    reason="没有可匹配的回章页",
                )
            else:
                returned_page, score = candidate
                outcomes[working_paper_id] = CompletionItem(
                    working_paper_id,
                    "low_confidence",
                    returned_page=returned_page,
                    score=score,
                    reason="最佳候选未达到 90 分自动回拼阈值",
                )
            continue
        output_path = None
        try:
            output_path = _output_path(output_root, item.working_paper_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            replace_last_page(
                converted_paths[working_paper_id],
                returned_pdf,
                output_path,
                returned_page_index=match.returned_page - 1,
            )
            outcomes[working_paper_id] = CompletionItem(
                working_paper_id,
                "completed",
                returned_page=match.returned_page,
                score=match.score,
                output_path=output_path,
            )
        except Exception as error:
            if output_path is not None:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
            outcomes[working_paper_id] = CompletionItem(
                working_paper_id,
                "failed",
                returned_page=match.returned_page,
                score=match.score,
                reason=str(error),
            )

    ordered_outcomes = tuple(outcomes[working_paper_id] for working_paper_id in item_order)
    unused_pages = tuple(sorted(set(matching.unused_pages) | set(untitled_pages)))
    return CompletionBatchResult(ordered_outcomes, unused_pages, output_root)

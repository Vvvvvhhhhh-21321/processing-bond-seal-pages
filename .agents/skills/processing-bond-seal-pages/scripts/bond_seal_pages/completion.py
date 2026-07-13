from dataclasses import dataclass
import json
from pathlib import Path
import shutil

from pypdf import PdfReader

from .pdf_ops import replace_last_page, sha256_file
from .titles import (
    ReturnedTitle,
    SourceTitle,
    TitleMatch,
    extract_bracket_title,
    match_titles,
    normalize_title,
    title_similarity,
)


MATCH_THRESHOLD = 90


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


def _page_title(text):
    bracketed_title = extract_bracket_title(text)
    if bracketed_title:
        return bracketed_title
    title_candidates = [
        line.strip() for line in (text or "").splitlines() if normalize_title(line)
    ]
    return max(
        title_candidates,
        key=lambda line: len(normalize_title(line)),
        default=None,
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
    if manifest.get("version") != 1 or not isinstance(manifest.get("items"), list):
        raise ValueError("处理批次清单格式不受支持")
    return manifest


def _validate_item(batch_root, item):
    if item.get("status") != "ready":
        raise ValueError("底稿未在第一阶段成功生成")
    converted_path = _relative_path(
        batch_root,
        item["converted_pdf"],
        "完整底稿 PDF",
    )
    if sha256_file(converted_path) != item["pdf_sha256"]:
        raise ValueError("完整底稿 PDF 校验值不一致")
    try:
        reader = PdfReader(str(converted_path))
        page_count = len(reader.pages)
    except Exception as error:
        raise ValueError("无法读取完整底稿 PDF") from error
    if page_count != item["pdf_page_count"]:
        raise ValueError("完整底稿 PDF 页数不一致")
    if normalize_title(item["title"]) != item["normalized_title"]:
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
            title = _page_title(page.extract_text() or "")
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


def _find_ambiguities(valid_items, returned_titles):
    groups = {}
    for item in valid_items:
        groups.setdefault(normalize_title(item["title"]), []).append(item)
    ambiguous_pages = set()
    ambiguous_items = {}
    for returned in returned_titles:
        scores = {
            title_key: title_similarity(group[0]["title"], returned.title)
            for title_key, group in groups.items()
        }
        if not scores:
            continue
        best_score = max(scores.values())
        tied_titles = [
            title_key for title_key, score in scores.items() if score == best_score
        ]
        if best_score < MATCH_THRESHOLD or len(tied_titles) < 2:
            continue
        ambiguous_pages.add(returned.page)
        for title_key in tied_titles:
            for item in groups[title_key]:
                working_paper_id = item["working_paper_id"]
                current = ambiguous_items.get(working_paper_id)
                candidate = (returned.page, best_score)
                if current is None or (best_score, -returned.page) > (
                    current[1],
                    -current[0],
                ):
                    ambiguous_items[working_paper_id] = candidate
    return ambiguous_pages, ambiguous_items


def _spread_same_title_pages(valid_items, returned_titles, matches):
    groups = {}
    for item in valid_items:
        groups.setdefault(normalize_title(item["title"]), []).append(item)
    for title_key, group in groups.items():
        group_ids = {item["working_paper_id"] for item in group}
        if not any(working_paper_id in matches for working_paper_id in group_ids):
            continue
        pages_used_by_other_titles = {
            match.returned_page
            for working_paper_id, match in matches.items()
            if working_paper_id not in group_ids
        }
        candidates_by_page = {}
        for returned in returned_titles:
            if (
                returned.page not in pages_used_by_other_titles
                and normalize_title(returned.title) == title_key
            ):
                candidates_by_page.setdefault(returned.page, returned)
        candidates = [candidates_by_page[page] for page in sorted(candidates_by_page)]
        if not candidates:
            continue
        for index, item in enumerate(group):
            returned = candidates[index % len(candidates)]
            matches[item["working_paper_id"]] = TitleMatch(
                item["working_paper_id"],
                returned.page,
                item["title"],
                returned.title,
                100,
            )


def _best_unused_candidate(title, returned_titles, unused_pages):
    unused_pages = set(unused_pages)
    candidates = [
        (title_similarity(title, returned.title), returned.page)
        for returned in returned_titles
        if returned.page in unused_pages
    ]
    if not candidates:
        return None
    score, page = max(candidates, key=lambda candidate: (candidate[0], -candidate[1]))
    if score == 0:
        return None
    return page, score


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
    for item in manifest["items"]:
        working_paper_id = item.get("working_paper_id", "")
        try:
            converted_paths[working_paper_id] = _validate_item(batch_root, item)
            _output_path(output_root, item["working_paper_path"])
            valid_items.append(item)
        except (KeyError, OSError, ValueError) as error:
            outcomes[working_paper_id] = CompletionItem(
                working_paper_id,
                "invalid_batch",
                reason=str(error),
            )

    returned_titles, untitled_pages = _read_returned_titles(returned_pdf)
    ambiguous_pages, ambiguous_items = _find_ambiguities(valid_items, returned_titles)
    matchable_titles = tuple(
        returned
        for returned in returned_titles
        if returned.page not in ambiguous_pages
    )
    matching = match_titles(
        [SourceTitle(item["working_paper_id"], item["title"]) for item in valid_items],
        matchable_titles,
        threshold=MATCH_THRESHOLD,
    )
    matches = {match.source_id: match for match in matching.matches}
    _spread_same_title_pages(valid_items, matchable_titles, matches)
    titled_pages = {returned.page for returned in returned_titles}
    used_pages = {match.returned_page for match in matches.values()}
    unused_titled_pages = titled_pages - used_pages
    low_confidence_pages = unused_titled_pages - ambiguous_pages
    for item in valid_items:
        working_paper_id = item["working_paper_id"]
        match = matches.get(working_paper_id)
        if match is None:
            ambiguity = ambiguous_items.get(working_paper_id)
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
            candidate = _best_unused_candidate(
                item["title"],
                returned_titles,
                low_confidence_pages,
            )
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
        output_path = _output_path(output_root, item["working_paper_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
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
            output_path.unlink(missing_ok=True)
            outcomes[working_paper_id] = CompletionItem(
                working_paper_id,
                "failed",
                returned_page=match.returned_page,
                score=match.score,
                reason=str(error),
            )

    ordered_outcomes = tuple(
        outcomes[item.get("working_paper_id", "")] for item in manifest["items"]
    )
    unused_pages = tuple(sorted(unused_titled_pages | set(untitled_pages)))
    return CompletionBatchResult(ordered_outcomes, unused_pages, output_root)

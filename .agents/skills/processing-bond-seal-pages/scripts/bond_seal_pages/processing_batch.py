from dataclasses import dataclass
import json
from pathlib import Path
import shutil

from pypdf import PdfReader, PdfWriter

from .pdf_ops import sha256_file
from .titles import extract_bracket_title, normalize_title


@dataclass(frozen=True)
class ProcessingBatchResult:
    succeeded: int
    failed: int
    seal_pages_path: Path
    manifest_path: Path


def _working_paper_files(working_paper_root):
    return sorted(
        (
            path
            for path in working_paper_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".doc", ".docx"}
        ),
        key=lambda path: path.relative_to(working_paper_root).as_posix(),
    )


def _converted_path(batch_root, relative_working_paper):
    return (
        batch_root
        / "pdfs"
        / relative_working_paper.parent
        / f"{relative_working_paper.name}.pdf"
    )


def _title_from_last_page(reader, fallback):
    text = reader.pages[-1].extract_text() or ""
    bracketed_title = extract_bracket_title(text)
    if bracketed_title:
        return bracketed_title
    lines = (line.strip() for line in text.splitlines())
    meaningful_lines = [line for line in lines if normalize_title(line)]
    return max(
        meaningful_lines,
        key=lambda line: len(normalize_title(line)),
        default=fallback,
    )


def _validate_directories(working_paper_root, batch_root):
    resolved_working_papers = working_paper_root.resolve()
    resolved_batch = batch_root.resolve()
    if (
        resolved_batch == resolved_working_papers
        or resolved_batch in resolved_working_papers.parents
    ):
        raise ValueError("处理批次目录不得等于或包含底稿目录")


def _remove_generated_path(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _prepare_output_directory(batch_root):
    batch_root.mkdir(parents=True, exist_ok=True)
    for generated_path in (
        batch_root / "pdfs",
        batch_root / "manifest.json",
        batch_root / "seal-pages.pdf",
    ):
        _remove_generated_path(generated_path)
    (batch_root / "pdfs").mkdir()


def _failed_item(item, converted_path, error):
    message = str(error)
    try:
        converted_path.unlink(missing_ok=True)
    except OSError as cleanup_error:
        message = f"{message}；无法清理转换残留：{cleanup_error}"
    item.update({"status": "failed", "error": message})


def prepare_processing_batch(working_paper_root, batch_root, converter=None):
    working_paper_root = Path(working_paper_root)
    batch_root = Path(batch_root)
    _validate_directories(working_paper_root, batch_root)
    _prepare_output_directory(batch_root)

    owned_converter = converter is None
    if owned_converter:
        from .word_conversion import WindowsWordPdfConverter

        converter = WindowsWordPdfConverter()

    items = []
    seal_pages = PdfWriter()
    try:
        for working_paper_path in _working_paper_files(working_paper_root):
            relative_working_paper = working_paper_path.relative_to(working_paper_root)
            converted_path = _converted_path(batch_root, relative_working_paper)
            item = {
                "working_paper_id": relative_working_paper.as_posix(),
                "working_paper_path": relative_working_paper.as_posix(),
            }
            try:
                converted_path.parent.mkdir(parents=True, exist_ok=True)
                working_paper_hash = sha256_file(working_paper_path)
                converter.convert(working_paper_path, converted_path)
                reader = PdfReader(str(converted_path))
                if not reader.pages:
                    raise ValueError("转换后的 PDF 没有页面")
                title = _title_from_last_page(reader, working_paper_path.stem)
                pdf_hash = sha256_file(converted_path)
                seal_pages.add_page(reader.pages[-1])
                item.update(
                    {
                        "status": "ready",
                        "title": title,
                        "normalized_title": normalize_title(title),
                        "converted_pdf": converted_path.relative_to(batch_root).as_posix(),
                        "pdf_page_count": len(reader.pages),
                        "working_paper_sha256": working_paper_hash,
                        "pdf_sha256": pdf_hash,
                        "seal_page": len(seal_pages.pages),
                    }
                )
            except Exception as error:
                _failed_item(item, converted_path, error)
            items.append(item)
    finally:
        if owned_converter:
            converter.close()

    seal_pages_path = batch_root / "seal-pages.pdf"
    with seal_pages_path.open("wb") as output:
        seal_pages.write(output)
    manifest_path = batch_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "working_paper_root": str(working_paper_root.resolve()),
                "seal_pages": seal_pages_path.name,
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return ProcessingBatchResult(
        succeeded=len(seal_pages.pages),
        failed=len(items) - len(seal_pages.pages),
        seal_pages_path=seal_pages_path,
        manifest_path=manifest_path,
    )

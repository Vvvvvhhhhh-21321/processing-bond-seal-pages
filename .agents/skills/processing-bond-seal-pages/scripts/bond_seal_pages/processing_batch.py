from dataclasses import dataclass
import json
from pathlib import Path
import shutil

from pypdf import PdfReader

from .pdf_ops import merge_pdf_pages, sha256_file
from .titles import extract_bracket_title, normalize_title


@dataclass(frozen=True)
class ProcessingBatchResult:
    succeeded: int
    failed: int
    seal_pages_path: Path
    manifest_path: Path


def _word_files(source_root):
    return sorted(
        (
            path
            for path in source_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".doc", ".docx"}
        ),
        key=lambda path: path.relative_to(source_root).as_posix(),
    )


def _converted_path(batch_root, relative_source):
    return batch_root / "pdfs" / relative_source.parent / f"{relative_source.name}.pdf"


def _title_from_last_page(reader, fallback):
    extracted = extract_bracket_title(reader.pages[-1].extract_text() or "")
    return extracted or fallback


def prepare_processing_batch(source_root, batch_root, converter=None):
    source_root = Path(source_root)
    batch_root = Path(batch_root)
    if batch_root.exists():
        shutil.rmtree(batch_root)
    (batch_root / "pdfs").mkdir(parents=True)

    owned_converter = converter is None
    if owned_converter:
        from .word_conversion import WindowsWordPdfConverter

        converter = WindowsWordPdfConverter()

    items = []
    selections = []
    try:
        for source_path in _word_files(source_root):
            relative_source = source_path.relative_to(source_root)
            converted_path = _converted_path(batch_root, relative_source)
            converted_path.parent.mkdir(parents=True, exist_ok=True)
            source_hash = sha256_file(source_path)
            item = {
                "source_id": relative_source.as_posix(),
                "source_path": relative_source.as_posix(),
                "source_sha256": source_hash,
            }
            try:
                converter.convert(source_path, converted_path)
                reader = PdfReader(str(converted_path))
                if not reader.pages:
                    raise ValueError("转换后的 PDF 没有页面")
                title = _title_from_last_page(reader, source_path.stem)
                selections.append((converted_path, len(reader.pages) - 1))
                item.update(
                    {
                        "status": "ready",
                        "title": title,
                        "normalized_title": normalize_title(title),
                        "converted_pdf": converted_path.relative_to(batch_root).as_posix(),
                        "pdf_page_count": len(reader.pages),
                        "pdf_sha256": sha256_file(converted_path),
                        "seal_page": len(selections),
                    }
                )
            except Exception as error:
                converted_path.unlink(missing_ok=True)
                item.update({"status": "failed", "error": str(error)})
            items.append(item)
    finally:
        if owned_converter:
            converter.close()

    seal_pages_path = batch_root / "seal-pages.pdf"
    merge_pdf_pages(selections, seal_pages_path)
    manifest_path = batch_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "source_root": str(source_root.resolve()),
                "seal_pages": seal_pages_path.name,
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return ProcessingBatchResult(
        succeeded=len(selections),
        failed=len(items) - len(selections),
        seal_pages_path=seal_pages_path,
        manifest_path=manifest_path,
    )

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from pypdf import PdfReader, PdfWriter, Transformation

from .date_layout import Box, DateAnchor


_BRACKET_TITLE = re.compile(r"《\s*(.*?)\s*》", re.DOTALL)
_DATE_COMPONENTS = {"年": "year", "月": "month", "日": "day"}


@dataclass(frozen=True)
class TextBox:
    text: str
    box: Box
    confidence: float = 1.0

    @property
    def center(self):
        return ((self.box.x0 + self.box.x1) / 2, (self.box.y0 + self.box.y1) / 2)


def sha256_file(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_pdf_page_count(path):
    return len(PdfReader(str(path)).pages)


def _page(reader, page_index):
    if page_index < 0 or page_index >= len(reader.pages):
        raise IndexError(f"PDF 页码超出范围：{page_index}")
    return reader.pages[page_index]


def extract_last_page(source_path, output_path):
    reader = PdfReader(str(source_path))
    if not reader.pages:
        raise ValueError("PDF 没有可抽取的页面")
    writer = PdfWriter()
    writer.add_page(reader.pages[-1])
    with Path(output_path).open("wb") as output:
        writer.write(output)
    return Path(output_path)


def merge_pdf_pages(selections, output_path):
    writer = PdfWriter()
    for pdf_path, page_index in selections:
        reader = PdfReader(str(pdf_path))
        writer.add_page(_page(reader, page_index))
    with Path(output_path).open("wb") as output:
        writer.write(output)
    return Path(output_path)


def replace_last_page(target_path, returned_path, output_path, returned_page_index=0):
    target_reader = PdfReader(str(target_path))
    if not target_reader.pages:
        raise ValueError("目标 PDF 没有可替换的页面")
    returned_reader = PdfReader(str(returned_path))
    returned_page = _page(returned_reader, returned_page_index)
    target_last = target_reader.pages[-1]

    writer = PdfWriter()
    for page in target_reader.pages[:-1]:
        writer.add_page(page)

    target_box = target_last.mediabox
    result_page = writer.add_blank_page(
        width=float(target_box.width),
        height=float(target_box.height),
    )
    result_page.mediabox.lower_left = target_box.lower_left
    result_page.mediabox.upper_right = target_box.upper_right

    source_box = returned_page.mediabox
    transform = (
        Transformation()
        .translate(tx=-float(source_box.left), ty=-float(source_box.bottom))
        .scale(
            sx=float(target_box.width) / float(source_box.width),
            sy=float(target_box.height) / float(source_box.height),
        )
        .translate(tx=float(target_box.left), ty=float(target_box.bottom))
    )
    result_page.merge_transformed_page(returned_page, transform)

    with Path(output_path).open("wb") as output:
        writer.write(output)
    return Path(output_path)


def render_pdf_page(path, page_index, scale=2.0):
    try:
        import pypdfium2
    except ImportError as error:
        raise RuntimeError("缺少 PDF 渲染依赖 pypdfium2，请先安装后再试") from error

    document = pypdfium2.PdfDocument(str(path))
    try:
        if page_index < 0 or page_index >= len(document):
            raise IndexError(f"PDF 页码超出范围：{page_index}")
        page = document[page_index]
        try:
            bitmap = page.render(scale=scale)
            try:
                return bitmap.to_pil().copy()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()


def _open_plumber_page(path, page_index):
    try:
        import pdfplumber
    except ImportError as error:
        raise RuntimeError("缺少 PDF 文字提取依赖 pdfplumber，请先安装后再试") from error
    document = pdfplumber.open(str(path))
    if page_index < 0 or page_index >= len(document.pages):
        document.close()
        raise IndexError(f"PDF 页码超出范围：{page_index}")
    return document, document.pages[page_index]


def extract_page_text(path, page_index):
    document, page = _open_plumber_page(path, page_index)
    try:
        return page.extract_text() or ""
    finally:
        document.close()


def extract_text_boxes(path, page_index):
    document, page = _open_plumber_page(path, page_index)
    try:
        boxes = []
        for word in page.extract_words() or []:
            boxes.append(
                TextBox(
                    word.get("text", ""),
                    Box(
                        float(word["x0"]),
                        float(page.height - word["bottom"]),
                        float(word["x1"]),
                        float(page.height - word["top"]),
                    ),
                )
            )
        return tuple(boxes)
    finally:
        document.close()


def extract_bracket_titles(path, page_index):
    return tuple(title.strip() for title in _BRACKET_TITLE.findall(extract_page_text(path, page_index)))


def extract_date_anchors(path, page_index):
    document, page = _open_plumber_page(path, page_index)
    try:
        anchors = []
        for character in page.chars:
            component = _DATE_COMPONENTS.get(character.get("text"))
            if component is None:
                continue
            box = Box(
                float(character["x0"]),
                float(character["y0"]),
                float(character["x1"]),
                float(character["y1"]),
            )
            anchors.append(
                DateAnchor(
                    component=component,
                    box=box,
                    baseline=float(character["y0"]),
                    font_size=float(character.get("size") or box.y1 - box.y0),
                )
            )
        order = {"year": 0, "month": 1, "day": 2}
        anchors.sort(key=lambda anchor: (order[anchor.component], anchor.box.x0))
        return tuple(anchors)
    finally:
        document.close()

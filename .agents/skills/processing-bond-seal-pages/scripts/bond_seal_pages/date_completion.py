from dataclasses import dataclass
from io import BytesIO
import re

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas

from .date_layout import plan_date_insertions
from .pdf_ops import extract_date_anchors, extract_text_boxes


_COMPONENT_LABELS = {
    "year": "年",
    "month": "月",
    "day": "日",
}
_TRAILING_DIGITS = re.compile(r"(\d+)\s*$")


@dataclass(frozen=True)
class DatedPage:
    page: object
    status: str
    reason: str | None = None


def _existing_date_components(anchors, text_boxes):
    existing = {}
    for anchor in anchors:
        candidates = []
        for text_box in text_boxes:
            same_line = abs(text_box.box.y0 - anchor.box.y0) <= anchor.font_size
            if not same_line:
                continue
            label = _COMPONENT_LABELS[anchor.component]
            attached = re.search(rf"(\d+)\s*{label}", text_box.text)
            contains_anchor = (
                text_box.box.x0 <= anchor.box.x0
                and text_box.box.x1 >= anchor.box.x1 - 0.5
            )
            if attached is not None and contains_anchor:
                candidates.append((-1.0, text_box.box.x1, attached.group(1)))
                continue
            trailing = _TRAILING_DIGITS.search(text_box.text)
            if trailing is None:
                continue
            gap = anchor.box.x0 - text_box.box.x1
            if -0.5 <= gap <= anchor.font_size * 1.5:
                candidates.append((abs(gap), text_box.box.x1, trailing.group(1)))
        if candidates:
            existing[anchor.component] = min(candidates)[2]
    return existing


def _overlay_date_placements(page, placements):
    overlay_buffer = BytesIO()
    canvas = Canvas(
        overlay_buffer,
        pagesize=(float(page.mediabox.width), float(page.mediabox.height)),
    )
    for placement in placements:
        font_size = placement.box.y1 - placement.box.y0
        canvas.setFont("Helvetica", font_size)
        canvas.drawString(
            placement.box.x0,
            placement.box.y0 + font_size * 0.2,
            placement.value,
        )
    canvas.showPage()
    canvas.save()
    overlay_buffer.seek(0)
    page.merge_page(PdfReader(overlay_buffer).pages[0])


def _skipped_reason(skipped):
    labels = "、".join(_COMPONENT_LABELS[component] for component in skipped)
    return f"无法安全补齐日期组成部分：{labels}"


def _clone_returned_page(returned_pdf, page_index):
    reader = PdfReader(str(returned_pdf))
    writer = PdfWriter()
    writer.add_page(reader.pages[page_index])
    return writer.pages[0]


def prepare_returned_page_with_date(returned_pdf, page_index, signing_date):
    page = _clone_returned_page(returned_pdf, page_index)
    if signing_date is None:
        return DatedPage(page, "not_requested")

    try:
        anchors = extract_date_anchors(returned_pdf, page_index)
        text_boxes = extract_text_boxes(returned_pdf, page_index)
        occupied = tuple(text_box.box for text_box in text_boxes)
        existing = _existing_date_components(anchors, text_boxes)
        plan = plan_date_insertions(
            signing_date,
            anchors,
            existing,
            occupied,
        )
        if plan.placements:
            _overlay_date_placements(page, plan.placements)
    except Exception as error:
        reason = str(error) or error.__class__.__name__
        return DatedPage(
            _clone_returned_page(returned_pdf, page_index),
            "failed",
            f"日期补齐失败：{reason}",
        )

    if not plan.skipped:
        status = "already_present" if not plan.placements else "filled"
        return DatedPage(page, status)
    status = "partial" if plan.placements else "failed"
    return DatedPage(page, status, _skipped_reason(plan.skipped))

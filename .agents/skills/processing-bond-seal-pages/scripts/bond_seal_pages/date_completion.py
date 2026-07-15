from dataclasses import dataclass
from enum import Enum
from io import BytesIO
import re

from pypdf import PdfReader, PdfWriter
from .date_layout import plan_date_insertions
from .pdf_ops import extract_date_anchors, extract_text_boxes


_COMPONENT_LABELS = {
    "year": "年",
    "month": "月",
    "day": "日",
}
_TRAILING_DIGITS = re.compile(r"(\d+)\s*$")


class SigningDateStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    NOT_APPLIED = "not_applied"
    FILLED = "filled"
    ALREADY_PRESENT = "already_present"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class SigningDateResult:
    status: SigningDateStatus
    reason: str | None = None


@dataclass(frozen=True)
class DatedPage:
    page: object
    result: SigningDateResult


def _dated_page(page, status, reason=None):
    return DatedPage(page, SigningDateResult(status, reason))



def _same_date_line(first, second):
    tolerance = max(first.font_size, second.font_size) * 0.5
    return abs(first.box.y0 - second.box.y0) <= tolerance


def _date_anchor_groups(anchors):
    groups = []
    years = sorted(
        (anchor for anchor in anchors if anchor.component == "year"),
        key=lambda anchor: (anchor.box.y0, anchor.box.x0),
    )
    for year in years:
        months = [
            anchor
            for anchor in anchors
            if anchor.component == "month"
            and anchor.box.x0 > year.box.x0
            and _same_date_line(year, anchor)
        ]
        if not months:
            continue
        month = min(months, key=lambda anchor: anchor.box.x0)
        days = [
            anchor
            for anchor in anchors
            if anchor.component == "day"
            and anchor.box.x0 > month.box.x0
            and _same_date_line(month, anchor)
        ]
        if days:
            groups.append((year, month, min(days, key=lambda anchor: anchor.box.x0)))
    return tuple(groups)


def _select_bottom_date_anchor_group(anchors):
    """选择页面最下方的完整日期行；同行多组时安全失败，不猜测。"""
    groups = _date_anchor_groups(anchors)
    if not groups:
        raise ValueError("未找到同一行的年、月、日落款日期区域")

    bottom_y = min(sum(anchor.box.y0 for anchor in group) / 3 for group in groups)
    bottom_groups = [
        group
        for group in groups
        if abs(sum(anchor.box.y0 for anchor in group) / 3 - bottom_y)
        <= max(anchor.font_size for anchor in group) * 0.5
    ]
    if len(bottom_groups) != 1:
        raise ValueError("页面最下方存在多个落款日期候选区域")
    return bottom_groups[0]

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
    try:
        from reportlab.pdfgen.canvas import Canvas
    except ImportError as error:
        raise RuntimeError(
            "缺少日期写入依赖 reportlab，请先安装后再试"
        ) from error

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
        return _dated_page(page, SigningDateStatus.NOT_REQUESTED)

    try:
        anchors = _select_bottom_date_anchor_group(
            extract_date_anchors(returned_pdf, page_index)
        )
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
        return _dated_page(
            _clone_returned_page(returned_pdf, page_index),
            SigningDateStatus.FAILED,
            f"日期补齐失败：{reason}",
        )

    if not plan.skipped:
        status = (
            SigningDateStatus.ALREADY_PRESENT
            if not plan.placements
            else SigningDateStatus.FILLED
        )
        return _dated_page(page, status)
    status = (
        SigningDateStatus.PARTIAL
        if plan.placements
        else SigningDateStatus.FAILED
    )
    return _dated_page(page, status, _skipped_reason(plan.skipped))

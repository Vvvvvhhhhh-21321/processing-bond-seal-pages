from dataclasses import dataclass

from pypdf import PdfReader

from .ocr import RapidOCRTitleEngine, extract_ocr_page_title
from .seal_page_titles import extract_pdf_page_title
from .titles import ReturnedTitle, title_similarity


_MIN_TEXT_TITLE_RELEVANCE = 60


@dataclass(frozen=True)
class OCRPageFailure:
    page: int
    reason: str


@dataclass(frozen=True)
class ReturnedPageTitleResult:
    titles: tuple[ReturnedTitle, ...]
    untitled_pages: tuple[int, ...]
    ocr_failures: tuple[OCRPageFailure, ...]


def _has_relevant_text_title(title, expected_titles):
    if not title:
        return False
    if not expected_titles:
        return True
    return any(
        title_similarity(title, expected_title) >= _MIN_TEXT_TITLE_RELEVANCE
        for expected_title in expected_titles
    )


def _page_has_raster_content(page):
    try:
        return bool(page.images)
    except Exception:
        return True


def read_returned_page_titles(returned_pdf, expected_titles, ocr_engine=None):
    expected_titles = tuple(expected_titles)
    if ocr_engine is None:
        ocr_engine = RapidOCRTitleEngine()
    try:
        reader = PdfReader(str(returned_pdf))
        pages = list(reader.pages)
    except Exception as error:
        raise ValueError(f"无法读取回章页合集：{returned_pdf}") from error

    titles = []
    untitled_pages = []
    ocr_failures = []
    for page_number, page in enumerate(pages, start=1):
        try:
            text_title = extract_pdf_page_title(page)
        except Exception:
            text_title = None
        has_raster_content = _page_has_raster_content(page)
        use_text_title = _has_relevant_text_title(
            text_title,
            expected_titles,
        ) or (text_title is not None and not has_raster_content)
        title = text_title if use_text_title else None

        if title is None and has_raster_content:
            try:
                title = extract_ocr_page_title(
                    returned_pdf,
                    page_number,
                    ocr_engine,
                )
            except Exception as error:
                reason = str(error) or error.__class__.__name__
                ocr_failures.append(OCRPageFailure(page_number, reason))
        if title:
            titles.append(ReturnedTitle(page_number, title))
        else:
            untitled_pages.append(page_number)

    return ReturnedPageTitleResult(
        tuple(titles),
        tuple(untitled_pages),
        tuple(ocr_failures),
    )

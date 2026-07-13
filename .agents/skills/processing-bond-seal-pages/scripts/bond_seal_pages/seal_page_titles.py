from dataclasses import dataclass

from .titles import extract_bracket_title, normalize_title


@dataclass(frozen=True)
class _TextFragment:
    text: str
    font_size: float
    vertical_position: float


def extract_text_title(text, fallback=None):
    bracketed_title = extract_bracket_title(text)
    if bracketed_title:
        return bracketed_title
    title_candidates = [
        line.strip() for line in (text or "").splitlines() if normalize_title(line)
    ]
    return max(
        title_candidates,
        key=lambda line: len(normalize_title(line)),
        default=fallback,
    )


def extract_pdf_page_title(page):
    fragments = []

    def collect_fragment(text, current_matrix, text_matrix, font, font_size):
        vertical_position = float(text_matrix[5]) if len(text_matrix) > 5 else 0.0
        for line in (text or "").splitlines():
            line = line.strip()
            if normalize_title(line):
                fragments.append(
                    _TextFragment(line, float(font_size or 0), vertical_position)
                )

    text = page.extract_text(visitor_text=collect_fragment) or ""
    bracketed_title = extract_bracket_title(text)
    if bracketed_title:
        return bracketed_title
    if not fragments:
        return None
    return max(
        fragments,
        key=lambda fragment: (
            fragment.font_size,
            fragment.vertical_position,
            len(normalize_title(fragment.text)),
        ),
    ).text

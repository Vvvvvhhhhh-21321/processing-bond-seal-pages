from dataclasses import dataclass

import numpy as np

from .titles import extract_bracket_title, normalize_title, title_similarity


@dataclass(frozen=True)
class GridMetrics:
    ink_ratio: float
    horizontal_band_count: int
    vertical_band_count: int


def _count_bands(mask):
    count = 0
    inside = False
    for value in mask:
        if value and not inside:
            count += 1
        inside = bool(value)
    return count


def _dilate_perpendicular(ink, axis, radius):
    dilated = ink.copy()
    for offset in range(1, radius + 1):
        if axis == 0:
            dilated[offset:, :] |= ink[:-offset, :]
            dilated[:-offset, :] |= ink[offset:, :]
        else:
            dilated[:, offset:] |= ink[:, :-offset]
            dilated[:, :-offset] |= ink[:, offset:]
    return dilated


def _long_line_bands(ink, horizontal):
    height, width = ink.shape
    line_length = width if horizontal else height
    tolerance = max(3, round(min(height, width) * 0.012))
    expanded = _dilate_perpendicular(ink, 0 if horizontal else 1, tolerance)
    scan = expanded if horizontal else expanded.T
    window = min(line_length, max(16, round(line_length * 0.30)))
    cumulative = np.pad(
        np.cumsum(scan.astype(np.int32), axis=1),
        ((0, 0), (1, 0)),
        mode="constant",
    )
    window_sums = cumulative[:, window:] - cumulative[:, :-window]
    has_long_line = window_sums.max(axis=1) >= round(window * 0.50)
    return _count_bands(has_long_line)


def grid_metrics(image):
    pixels = np.asarray(image.convert("L"))
    ink = pixels < 200
    return GridMetrics(
        float(ink.mean()),
        _long_line_bands(ink, horizontal=True),
        _long_line_bands(ink, horizontal=False),
    )


def is_table_page(metrics):
    return (
        metrics.ink_ratio >= 0.003
        and metrics.horizontal_band_count >= 4
        and metrics.vertical_band_count >= 3
    )


@dataclass(frozen=True)
class TablePage:
    page: int
    title: str
    page_number: int | None = None


def _table_title(text):
    extracted = extract_bracket_title(text)
    return normalize_title(text if extracted is None else extracted)


def cluster_table_pages(pages, similarity_threshold=75):
    groups = []
    representatives = []
    for page in pages:
        normalized = _table_title(page.title)
        group_index = next(
            (
                index
                for index, representative in enumerate(representatives)
                if title_similarity(normalized, representative) >= similarity_threshold
            ),
            None,
        )
        if group_index is None:
            representatives.append(normalized)
            groups.append([page])
        else:
            groups[group_index].append(page)

    for group in groups:
        if any(page.page_number is not None for page in group):
            group.sort(
                key=lambda page: (
                    page.page_number is None,
                    page.page_number if page.page_number is not None else 0,
                )
            )
    return groups
from dataclasses import dataclass
from datetime import date, datetime
from statistics import median
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    def intersects(self, other):
        return not (
            self.x1 <= other.x0
            or other.x1 <= self.x0
            or self.y1 <= other.y0
            or other.y1 <= self.y0
        )


@dataclass(frozen=True)
class DateAnchor:
    component: str
    box: Box
    baseline: float
    font_size: float = 12.0


@dataclass(frozen=True)
class DatePlacement:
    component: str
    value: str
    box: Box
    baseline: float
    scale: float
    source: str = "requested"


@dataclass(frozen=True)
class DatePlan:
    values: Mapping[str, str]
    sources: Mapping[str, str]
    placements: tuple[DatePlacement, ...]
    skipped: tuple[str, ...]


def _date_parts(requested_date):
    if isinstance(requested_date, datetime):
        requested_date = requested_date.date()
    elif isinstance(requested_date, str):
        requested_date = date.fromisoformat(requested_date[:10])
    return {
        "year": str(requested_date.year),
        "month": str(requested_date.month),
        "day": str(requested_date.day),
    }


def _placement_box(anchor, value, baseline, scale):
    font_size = anchor.font_size * scale
    width = font_size * 0.6 * len(value)
    return Box(
        anchor.box.x0 - width,
        baseline - font_size * 0.8,
        anchor.box.x0,
        baseline + font_size * 0.2,
    )


def _scales_to_try(min_scale):
    scales = []
    scale = 1.0
    while scale > min_scale + 1e-9:
        scales.append(scale)
        scale = round(scale - 0.05, 10)
    if not scales or abs(scales[-1] - min_scale) > 1e-9:
        scales.append(min_scale)
    return scales


def plan_date_insertions(
    requested_date,
    anchors,
    existing,
    occupied,
    min_scale=0.85,
):
    anchors = list(anchors)
    anchor_by_component = {anchor.component: anchor for anchor in anchors}
    requested = _date_parts(requested_date)
    existing = {key: str(value) for key, value in existing.items() if value not in (None, "")}
    values = {}
    sources = {}
    for component in ("year", "month", "day"):
        if component in existing:
            values[component] = existing[component]
            sources[component] = "existing"

    missing = [component for component in ("year", "month", "day") if component not in existing]
    if not missing:
        return DatePlan(
            MappingProxyType(values),
            MappingProxyType(sources),
            (),
            (),
        )

    common_baseline = float(median(anchor.baseline for anchor in anchors)) if anchors else None
    placements = []
    skipped = []
    collision_boxes = list(occupied)

    for component in missing:
        anchor = anchor_by_component.get(component)
        if anchor is None:
            skipped.append(component)
            continue

        value = requested[component]
        placed_box = None
        placed_scale = None
        for scale in _scales_to_try(min_scale):
            candidate = _placement_box(anchor, value, common_baseline, scale)
            if all(not candidate.intersects(box) for box in collision_boxes):
                placed_box = candidate
                placed_scale = scale
                break

        if placed_box is None:
            skipped.append(component)
            continue

        placement = DatePlacement(
            component,
            value,
            placed_box,
            common_baseline,
            placed_scale,
        )
        placements.append(placement)
        collision_boxes.append(placed_box)
        values[component] = value
        sources[component] = "requested"

    return DatePlan(
        MappingProxyType(values),
        MappingProxyType(sources),
        tuple(placements),
        tuple(skipped),
    )
import sys
import unittest
from datetime import date
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.date_layout import (  # noqa: E402
    Box,
    DateAnchor,
    plan_date_insertions,
)


def make_anchors():
    return [
        DateAnchor("year", Box(100, 80, 112, 94), 91.0),
        DateAnchor("month", Box(170, 81, 182, 95), 92.0),
        DateAnchor("day", Box(220, 79, 232, 93), 90.0),
    ]


class DateLayoutTests(unittest.TestCase):
    def test_all_empty_places_requested_components_left_of_anchors(self):
        anchors = make_anchors()
        plan = plan_date_insertions(date(2026, 7, 12), anchors, {}, [])

        self.assertEqual(plan.values, {"year": "2026", "month": "7", "day": "12"})
        self.assertEqual(plan.sources, {"year": "requested", "month": "requested", "day": "requested"})
        self.assertEqual({item.component for item in plan.placements}, {"year", "month", "day"})
        anchor_left = {anchor.component: anchor.box.x0 for anchor in anchors}
        for item in plan.placements:
            self.assertEqual(item.box.x1, anchor_left[item.component])
            self.assertEqual(item.baseline, 91.0)

    def test_existing_components_win_and_only_missing_component_is_inserted(self):
        plan = plan_date_insertions(
            date(2026, 7, 12),
            make_anchors(),
            {"year": "2025", "month": "8"},
            [],
        )

        self.assertEqual(plan.values, {"year": "2025", "month": "8", "day": "12"})
        self.assertEqual(plan.sources, {"year": "existing", "month": "existing", "day": "requested"})
        self.assertEqual([item.component for item in plan.placements], ["day"])

    def test_collision_shrinks_until_clear(self):
        occupied = [Box(71.0, 80.0, 73.0, 94.0)]

        plan = plan_date_insertions(date(2026, 7, 12), make_anchors(), {"month": "7", "day": "12"}, occupied)

        self.assertEqual(len(plan.placements), 1)
        placement = plan.placements[0]
        self.assertEqual(placement.component, "year")
        self.assertLess(placement.scale, 1.0)
        self.assertGreaterEqual(placement.scale, 0.85)
        self.assertFalse(any(placement.box.intersects(box) for box in occupied))

    def test_unavoidable_collision_skips_component(self):
        occupied = [Box(90, 75, 101, 98)]

        plan = plan_date_insertions(date(2026, 7, 12), make_anchors(), {"month": "7", "day": "12"}, occupied)

        self.assertEqual(plan.placements, ())
        self.assertEqual(plan.skipped, ("year",))
        self.assertNotIn("year", plan.values)

    def test_inserted_baselines_match_and_boxes_do_not_overlap_occupied(self):
        occupied = [Box(10, 10, 20, 20)]
        plan = plan_date_insertions("2026-07-12", make_anchors(), {}, occupied)

        baselines = {item.baseline for item in plan.placements}
        self.assertEqual(baselines, {91.0})
        for item in plan.placements:
            self.assertTrue(all(not item.box.intersects(box) for box in occupied))

    def test_complete_existing_date_needs_no_anchors(self):
        plan = plan_date_insertions(
            date(2026, 7, 12),
            [],
            {"year": "2025", "month": "8", "day": "9"},
            [],
        )
        self.assertEqual(plan.values, {"year": "2025", "month": "8", "day": "9"})
        self.assertEqual(plan.placements, ())
        self.assertEqual(plan.skipped, ())

    def test_exact_minimum_scale_is_attempted(self):
        occupied = [Box(74.2, 80, 74.8, 94)]
        plan = plan_date_insertions(
            date(2026, 7, 12),
            make_anchors(),
            {"month": "7", "day": "12"},
            occupied,
            min_scale=0.87,
        )
        self.assertEqual(len(plan.placements), 1)
        self.assertEqual(plan.placements[0].scale, 0.87)

    def test_plan_value_and_source_mappings_are_read_only(self):
        plan = plan_date_insertions(date(2026, 7, 12), make_anchors(), {}, [])
        with self.assertRaises(TypeError):
            plan.values["year"] = "2030"
        with self.assertRaises(TypeError):
            plan.sources["year"] = "existing"


if __name__ == "__main__":
    unittest.main()
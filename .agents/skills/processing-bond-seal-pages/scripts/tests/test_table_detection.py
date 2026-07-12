import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.table_detection import (  # noqa: E402
    TablePage,
    cluster_table_pages,
    grid_metrics,
    is_table_page,
)


class TableDetectionTests(unittest.TestCase):
    def test_dense_grid_is_a_table(self):
        image = Image.new("L", (320, 240), 255)
        draw = ImageDraw.Draw(image)
        for y in range(20, 221, 40):
            draw.line((20, y, 300, y), fill=0, width=2)
        for x in range(20, 301, 70):
            draw.line((x, 20, x, 220), fill=0, width=2)

        metrics = grid_metrics(image)

        self.assertGreaterEqual(metrics.horizontal_band_count, 6)
        self.assertGreaterEqual(metrics.vertical_band_count, 5)
        self.assertTrue(is_table_page(metrics))

    def test_four_signature_lines_are_not_a_table(self):
        pixels = np.full((240, 320), 255, dtype=np.uint8)
        for y in (50, 90, 140, 190):
            pixels[y : y + 2, 100:290] = 0

        metrics = grid_metrics(Image.fromarray(pixels))

        self.assertEqual(metrics.vertical_band_count, 0)
        self.assertFalse(is_table_page(metrics))

    def test_clusters_two_multi_page_tables_and_sorts_numbered_pages(self):
        pages = [
            TablePage(30, "《工程量清单》", 2),
            TablePage(31, "付款申请表", 3),
            TablePage(32, "工程量 清单", 1),
            TablePage(33, "付款申请表", 1),
            TablePage(34, "付款申请 表", 2),
        ]

        groups = cluster_table_pages(pages)

        self.assertEqual([[page.page for page in group] for group in groups], [[32, 30], [33, 34, 31]])

    def test_group_without_page_numbers_keeps_returned_order(self):
        pages = [
            TablePage(7, "设备清单"),
            TablePage(3, "设备 清单"),
        ]
        groups = cluster_table_pages(pages)
        self.assertEqual([page.page for page in groups[0]], [7, 3])

    def test_mixed_page_numbers_sort_numbered_first_and_keep_unnumbered_order(self):
        pages = [
            TablePage(10, "设备清单"),
            TablePage(11, "设备清单", 2),
            TablePage(12, "设备 清单"),
            TablePage(13, "设备清单", 1),
        ]
        groups = cluster_table_pages(pages)
        self.assertEqual([page.page for page in groups[0]], [13, 11, 10, 12])


    def test_partial_broken_noisy_tilted_grid_is_a_table(self):
        image = Image.new("L", (420, 420), 255)
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = 120, 120, 300, 300

        for y in range(top, bottom + 1, 30):
            for x0, x1 in ((left, 190), (198, 250), (258, right)):
                draw.line((x0, y, x1, y), fill=0, width=2)
        for x in range(left, right + 1, 36):
            for y0, y1 in ((top, 185), (193, 245), (253, bottom)):
                draw.line((x, y0, x, y1), fill=0, width=2)

        pixels = np.asarray(image).copy()
        random = np.random.default_rng(42)
        noise_y = random.integers(0, 420, 450)
        noise_x = random.integers(0, 420, 450)
        pixels[noise_y, noise_x] = 0
        noisy = Image.fromarray(pixels)
        tilted = noisy.rotate(1.5, resample=Image.Resampling.BILINEAR, fillcolor=255)

        metrics = grid_metrics(tilted)

        self.assertGreaterEqual(metrics.horizontal_band_count, 6)
        self.assertGreaterEqual(metrics.vertical_band_count, 5)
        self.assertTrue(is_table_page(metrics))
if __name__ == "__main__":
    unittest.main()
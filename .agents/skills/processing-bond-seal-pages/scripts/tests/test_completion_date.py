import builtins
from datetime import date
from io import BytesIO
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pdfplumber
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.completion import complete_processing_batch  # noqa: E402
from completion_test_support import create_processing_batch, write_pdf_pages  # noqa: E402


_REQUESTED_DATE = date(2026, 7, 12)
_DEFAULT_RECORDS = [("确认函.docx", "确认函")]


def write_returned_date_pages(
    path,
    pages,
    obstacles_by_page=None,
    body_dates_by_page=None,
):
    obstacles_by_page = obstacles_by_page or {}
    body_dates_by_page = body_dates_by_page or {}
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    canvas = Canvas(str(path), pagesize=(500, 700))
    seal_buffer = BytesIO()
    Image.new("RGB", (40, 40), "red").save(seal_buffer, format="PNG")
    seal_buffer.seek(0)
    seal_image = ImageReader(seal_buffer)
    anchors = {"year": (100, "年"), "month": (180, "月"), "day": (240, "日")}
    for index, (title, existing) in enumerate(pages):
        if index:
            canvas.setPageSize((500, 700))
        canvas.setFont("STSong-Light", 14)
        canvas.drawString(50, 650, f"《{title}》")
        canvas.drawImage(seal_image, 350, 300, width=40, height=40)
        body_date = body_dates_by_page.get(index)
        if body_date is not None:
            canvas.drawString(100, 500, body_date)
        for component, (x, label) in anchors.items():
            value = existing.get(component)
            if value is not None:
                canvas.drawRightString(x, 100, str(value))
            canvas.drawString(x, 100, label)
        canvas.setFont("Helvetica", 14)
        for x, text in obstacles_by_page.get(index, ()):
            canvas.drawString(x, 100, text)
        canvas.showPage()
    canvas.save()


class CompletionDateTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def _prepare_date_pages(
        self,
        pages,
        records=None,
        obstacles_by_page=None,
        body_dates_by_page=None,
    ):
        batch_root = create_processing_batch(
            self.root,
            records or _DEFAULT_RECORDS,
        )
        returned_pdf = self.root / "回章页合集.pdf"
        write_returned_date_pages(
            returned_pdf,
            pages,
            obstacles_by_page,
            body_dates_by_page,
        )
        return batch_root, returned_pdf

    def _complete_date_pages(
        self,
        pages,
        records=None,
        signing_date=_REQUESTED_DATE,
        obstacles_by_page=None,
        body_dates_by_page=None,
    ):
        batch_root, returned_pdf = self._prepare_date_pages(
            pages,
            records,
            obstacles_by_page,
            body_dates_by_page,
        )
        result = complete_processing_batch(
            batch_root,
            returned_pdf,
            self.root / "回拼结果",
            signing_date=signing_date,
        )
        return returned_pdf, result

    def test_omitted_signing_date_does_not_request_date_writing(self):
        batch_root = create_processing_batch(self.root, _DEFAULT_RECORDS)
        returned_pdf = self.root / "回章页合集.pdf"
        write_pdf_pages(returned_pdf, [("《确认函》", (500, 700))])

        result = complete_processing_batch(
            batch_root,
            returned_pdf,
            self.root / "回拼结果",
            signing_date=None,
        )

        item = result.items[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.date_status, "not_requested")
        self.assertIsNone(item.date_reason)

    def test_empty_date_fields_are_filled_on_the_completed_pdf(self):
        returned_pdf, result = self._complete_date_pages([("确认函", {})])

        item = result.items[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.date_status, "filled")
        self.assertIsNone(item.date_reason)
        returned_page = PdfReader(str(returned_pdf)).pages[0]
        page = PdfReader(str(item.output_path)).pages[-1]
        text = page.extract_text()
        self.assertIn("确认函", text)
        self.assertIn("2026", text)
        self.assertIn("7", text)
        self.assertIn("12", text)
        self.assertEqual(
            (float(page.mediabox.width), float(page.mediabox.height)),
            (500.0, 700.0),
        )
        self.assertEqual(returned_page.images[0].data, page.images[0].data)
        with pdfplumber.open(str(item.output_path)) as document:
            characters = document.pages[-1].chars
            inserted_year = next(
                char
                for char in characters
                if char["text"] == "2"
                and char["x0"] < 100
                and char["y0"] < 150
            )
            year_anchor = next(
                char for char in characters if char["text"] == "年"
            )
        self.assertAlmostEqual(
            float(inserted_year["y0"]),
            float(year_anchor["y0"]),
            delta=1.0,
        )

    def test_existing_year_and_month_are_preserved_while_day_is_filled(self):
        _, result = self._complete_date_pages(
            [("确认函", {"year": 2025, "month": 8})]
        )

        item = result.items[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.date_status, "filled")
        text = PdfReader(str(item.output_path)).pages[-1].extract_text()
        self.assertIn("2025", text)
        self.assertIn("8", text)
        self.assertIn("12", text)
        self.assertNotIn("2026", text)

    def test_complete_existing_date_is_kept_without_requested_values(self):
        _, result = self._complete_date_pages(
            [("确认函", {"year": 2025, "month": 8, "day": 9})]
        )

        item = result.items[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.date_status, "already_present")
        text = PdfReader(str(item.output_path)).pages[-1].extract_text()
        self.assertIn("2025", text)
        self.assertIn("8", text)
        self.assertIn("9", text)
        self.assertNotIn("2026", text)
        self.assertNotIn("12", text)

    def test_collision_shrinks_the_inserted_year_within_the_safe_range(self):
        _, result = self._complete_date_pages(
            [("确认函", {"month": 7, "day": 12})],
            obstacles_by_page={0: ((64, "."),)},
        )

        item = result.items[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.date_status, "filled")
        with pdfplumber.open(str(item.output_path)) as document:
            year_chars = [
                character
                for character in document.pages[-1].chars
                if character["text"] in "2026"
                and character["x0"] < 100
                and character["y0"] < 150
            ]
        self.assertEqual("".join(char["text"] for char in year_chars), "2026")
        self.assertLess(float(year_chars[0]["size"]), 14.0)
        self.assertGreaterEqual(float(year_chars[0]["size"]), 14.0 * 0.85)

    def test_unplaceable_date_does_not_block_page_or_other_working_papers(self):
        _, result = self._complete_date_pages(
            [
                ("甲确认函", {"month": 7, "day": 12}),
                ("乙确认函", {}),
            ],
            records=[("甲.docx", "甲确认函"), ("乙.docx", "乙确认函")],
            obstacles_by_page={0: ((90, "X"),)},
        )

        items = {item.working_paper_id: item for item in result.items}
        self.assertEqual(items["甲.docx"].status, "completed")
        self.assertEqual(items["甲.docx"].date_status, "failed")
        self.assertIn("年", items["甲.docx"].date_reason)
        first_text = PdfReader(
            str(items["甲.docx"].output_path)
        ).pages[-1].extract_text()
        self.assertIn("甲确认函", first_text)
        self.assertNotIn("2026", first_text)
        self.assertEqual(items["乙.docx"].status, "completed")
        self.assertEqual(items["乙.docx"].date_status, "filled")
        second_text = PdfReader(
            str(items["乙.docx"].output_path)
        ).pages[-1].extract_text()
        self.assertIn("2026", second_text)

    def test_date_processing_error_falls_back_to_the_original_returned_page(self):
        _, result = self._complete_date_pages(
            [("确认函", {})],
            signing_date="不是有效日期",
        )

        item = result.items[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.date_status, "failed")
        self.assertIn("日期补齐失败", item.date_reason)
        output_page = PdfReader(str(item.output_path)).pages[-1]
        self.assertIn("确认函", output_page.extract_text())
        self.assertEqual(
            (float(output_page.mediabox.width), float(output_page.mediabox.height)),
            (500.0, 700.0),
        )

    def test_requested_date_is_not_applied_to_an_unmatched_working_paper(self):
        _, result = self._complete_date_pages(
            [("甲确认函", {})],
            records=[("甲.docx", "甲确认函"), ("乙.docx", "乙确认函")],
        )

        items = {item.working_paper_id: item for item in result.items}
        self.assertEqual(items["甲.docx"].date_status, "filled")
        self.assertEqual(items["乙.docx"].status, "unmatched")
        self.assertEqual(items["乙.docx"].date_status, "not_applied")

    def test_missing_reportlab_is_a_date_failure_not_a_batch_failure(self):
        batch_root, returned_pdf = self._prepare_date_pages([("确认函", {})])
        original_import = builtins.__import__

        def reject_reportlab(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "reportlab.pdfgen.canvas":
                raise ImportError("测试环境缺少 reportlab")
            return original_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=reject_reportlab):
            result = complete_processing_batch(
                batch_root,
                returned_pdf,
                self.root / "回拼结果",
                signing_date=_REQUESTED_DATE,
            )

        item = result.items[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.date_status, "failed")
        self.assertIn("reportlab", item.date_reason)
        self.assertTrue(item.output_path.exists())

    def test_body_date_does_not_replace_the_bottom_signing_date_group(self):
        _, result = self._complete_date_pages(
            [("确认函", {})],
            body_dates_by_page={0: "2024年1月2日"},
        )

        item = result.items[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.date_status, "filled")
        with pdfplumber.open(str(item.output_path)) as document:
            characters = document.pages[-1].chars
            bottom_text = "".join(
                char["text"] for char in characters if char["y0"] < 150
            )
            body_text = "".join(
                char["text"]
                for char in characters
                if 450 < char["y0"] < 550
            )
        self.assertIn("2026", bottom_text)
        self.assertIn("2024", body_text)

    def test_partial_date_records_the_blocked_component_and_writes_the_rest(self):
        _, result = self._complete_date_pages(
            [("确认函", {})],
            obstacles_by_page={0: ((90, "X"),)},
        )

        item = result.items[0]
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.date_status, "partial")
        self.assertIn("年", item.date_reason)
        text = PdfReader(str(item.output_path)).pages[-1].extract_text()
        self.assertNotIn("2026", text)
        self.assertIn("7", text)
        self.assertIn("12", text)


if __name__ == "__main__":
    unittest.main()

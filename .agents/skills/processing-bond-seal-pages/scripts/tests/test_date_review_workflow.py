from datetime import date
import json
import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.review_workflow import (  # noqa: E402
    create_date_review,
    finalize_date_review,
)
from completion_test_support import create_processing_batch  # noqa: E402


def write_review_pages(path, pages):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    canvas = Canvas(str(path), pagesize=(500, 700))
    for index, (title, marker) in enumerate(pages):
        if index:
            canvas.setPageSize((500, 700))
        canvas.setFont("STSong-Light", 14)
        canvas.drawString(50, 650, f"《{title}》")
        if marker:
            canvas.drawString(50, 600, marker)
        for x, label in ((100, "年"), (180, "月"), (240, "日")):
            canvas.drawString(x, 100, label)
        canvas.showPage()
    canvas.save()


class DateReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.batch_root = create_processing_batch(
            self.root,
            [("甲.docx", "甲方确认函"), ("乙.docx", "乙方确认函")],
        )
        self.returned_pdf = self.root / "回章页合集.pdf"
        write_review_pages(
            self.returned_pdf,
            [("乙方确认函", None), ("甲方确认函", None)],
        )
        self.review_root = self.root / "日期确认"

    def test_date_review_writes_one_ordered_pdf_and_never_completes_working_papers(self):
        result = create_date_review(
            self.batch_root,
            self.returned_pdf,
            self.review_root,
            signing_date=date(2026, 7, 15),
        )

        self.assertEqual([item.returned_page for item in result.items], [2, 1])
        self.assertEqual([item.status for item in result.items], ["review_ready"] * 2)
        self.assertEqual(len(PdfReader(str(result.review_pdf)).pages), 2)
        review_text = [
            page.extract_text() for page in PdfReader(str(result.review_pdf)).pages
        ]
        self.assertIn("乙方确认函", review_text[0])
        self.assertIn("甲方确认函", review_text[1])
        self.assertIn("2026", review_text[0])
        self.assertFalse((self.review_root / "completed-pdfs").exists())
        self.assertFalse((self.root / "回拼结果").exists())

        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["review_pdf"], "dated-returned-pages.pdf")
        self.assertEqual(manifest["page_count"], 2)

    def test_finalize_uses_the_manually_edited_review_pdf_and_saved_mapping(self):
        review = create_date_review(
            self.batch_root,
            self.returned_pdf,
            self.review_root,
            signing_date=date(2026, 7, 15),
        )
        write_review_pages(
            review.review_pdf,
            [("乙方确认函", "人工修改-乙"), ("甲方确认函", "人工修改-甲")],
        )

        completed = finalize_date_review(
            self.batch_root,
            self.review_root,
            self.root / "回拼结果",
            confirmed=True,
        )

        items = {item.working_paper_id: item for item in completed.items}
        first_text = PdfReader(str(items["甲.docx"].output_path)).pages[-1].extract_text()
        second_text = PdfReader(str(items["乙.docx"].output_path)).pages[-1].extract_text()
        self.assertIn("人工修改-甲", first_text)
        self.assertIn("人工修改-乙", second_text)
        self.assertEqual(items["甲.docx"].returned_page, 2)
        self.assertEqual(items["乙.docx"].returned_page, 1)
        report = completed.report_path.read_text(encoding="utf-8")
        self.assertIn("日期确认稿", report)

    def test_finalize_requires_explicit_confirmation(self):
        create_date_review(self.batch_root, self.returned_pdf, self.review_root)

        with self.assertRaisesRegex(PermissionError, "用户确认"):
            finalize_date_review(
                self.batch_root,
                self.review_root,
                self.root / "回拼结果",
                confirmed=False,
            )

        self.assertFalse((self.root / "回拼结果").exists())

    def test_finalize_rejects_a_review_pdf_with_changed_page_count(self):
        review = create_date_review(self.batch_root, self.returned_pdf, self.review_root)
        write_review_pages(review.review_pdf, [("乙方确认函", "删除了一页")])

        with self.assertRaisesRegex(ValueError, "页数"):
            finalize_date_review(
                self.batch_root,
                self.review_root,
                self.root / "回拼结果",
                confirmed=True,
            )

        self.assertFalse((self.root / "回拼结果").exists())


if __name__ == "__main__":
    unittest.main()

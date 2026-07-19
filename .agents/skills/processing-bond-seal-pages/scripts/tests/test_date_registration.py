
from datetime import date
from io import BytesIO
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw
import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.date_completion import (  # noqa: E402
    SigningDateStatus,
    prepare_returned_page_with_date,
)
from bond_seal_pages.pdf_ops import (  # noqa: E402
    extract_page_text,
    render_pdf_page,
    sha256_file,
)
from bond_seal_pages.review_workflow import create_date_review  # noqa: E402
from completion_test_support import create_processing_batch  # noqa: E402


_PAGE_SIZE = (500, 700)
_DATE_Y = 105

_REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
_REAL_RETURNED_PDF = (
    _REPOSITORY_ROOT / "素材" / "底稿文件" / "说明性文件盖章页.pdf"
)
_REAL_TEMPLATE_PDF = (
    _REPOSITORY_ROOT
    / "素材"
    / "底稿文件"
    / "发行人说明性文件合集-处理批次"
    / "pdfs"
    / "1-4-3 福建省能源集团有限责任公司关于主要业务板块营业收入、毛利率情况的说明.docx.pdf"
)


def _draw_template_page(canvas, title):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    canvas.setFont("STSong-Light", 14)
    canvas.drawString(45, 650, f"《{title}》")
    for index, text in enumerate(
        (
            "本页无正文，为说明性文件之盖章页。",
            "福建省能源集团有限责任公司",
            "债券底稿盖章页批次处理",
            "请按原始页面位置填写落款日期",
        )
    ):
        canvas.drawString(55 + index * 9, 570 - index * 72, text)
    canvas.drawString(285, 155, "福建省能源集团有限责任公司")
    for x, label in ((330, "年"), (390, "月"), (445, "日")):
        canvas.drawString(x, _DATE_Y, label)


def write_template_pdf(path, title="扫描确认函", with_body=False):
    canvas = Canvas(str(path), pagesize=_PAGE_SIZE)
    if with_body:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        canvas.setFont("STSong-Light", 14)
        canvas.drawString(45, 650, "底稿正文")
        canvas.showPage()
    _draw_template_page(canvas, title)
    canvas.showPage()
    canvas.save()


def write_scanned_return(template_pdf, output_pdf, page_index=-1, featureless=False):
    if featureless:
        scanned = Image.new("RGB", (1000, 1400), "white")
    else:
        if page_index < 0:
            page_index = len(PdfReader(str(template_pdf)).pages) + page_index
        original = render_pdf_page(template_pdf, page_index, scale=2.0).convert("RGB")
        width, height = original.size
        scaled = original.resize((round(width * 0.94), round(height * 0.94)))
        scanned = Image.new("RGB", (width, height), "white")
        scanned.paste(scaled, (22, 31))

        draw = ImageDraw.Draw(scanned, "RGBA")
        date_y = 31 + round((height - _DATE_Y * 2) * 0.94)
        date_x = 22 + round(330 * 2 * 0.94)
        draw.ellipse(
            (date_x - 75, date_y - 70, date_x + 145, date_y + 90),
            outline=(210, 0, 0, 225),
            width=14,
        )
        draw.ellipse(
            (date_x - 48, date_y - 45, date_x + 118, date_y + 65),
            outline=(210, 0, 0, 210),
            width=8,
        )

    image_buffer = BytesIO()
    scanned.save(image_buffer, format="PNG")
    image_buffer.seek(0)
    canvas = Canvas(str(output_pdf), pagesize=_PAGE_SIZE)
    canvas.drawImage(
        ImageReader(image_buffer),
        0,
        0,
        width=_PAGE_SIZE[0],
        height=_PAGE_SIZE[1],
    )
    canvas.showPage()
    canvas.save()


def write_dated_page(path, page):
    writer = PdfWriter()
    writer.add_page(page)
    with Path(path).open("wb") as output:
        writer.write(output)


class StubOCREngine:
    def recognize_page(self, pdf_path, page_number):
        return ("《扫描确认函》",)


class DateRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.template_pdf = self.root / "原始底稿.pdf"
        self.returned_pdf = self.root / "扫描回章.pdf"
        write_template_pdf(self.template_pdf)
        write_scanned_return(self.template_pdf, self.returned_pdf)

    def test_scanned_page_uses_registered_template_date_anchors(self):
        self.assertEqual(extract_page_text(self.returned_pdf, 0), "")

        dated = prepare_returned_page_with_date(
            self.returned_pdf,
            0,
            date(2026, 7, 15),
            template_pdf=self.template_pdf,
            template_page_index=0,
        )

        self.assertEqual(
            dated.result.status,
            SigningDateStatus.FILLED_NEEDS_REVIEW,
        )
        self.assertIn("重点核对", dated.result.reason)
        output_pdf = self.root / "已落日期.pdf"
        write_dated_page(output_pdf, dated.page)
        text = PdfReader(str(output_pdf)).pages[0].extract_text()
        self.assertIn("2026", text)
        self.assertIn("7", text)
        self.assertIn("15", text)
        with pdfplumber.open(str(output_pdf)) as document:
            inserted = [
                character
                for character in document.pages[0].chars
                if character["text"].isdigit()
            ]
        self.assertTrue(inserted)
        self.assertGreater(min(float(character["x0"]) for character in inserted), 250)
        self.assertGreater(min(float(character["y0"]) for character in inserted), 90)

    def test_single_available_registration_is_used_and_flagged_for_review(self):
        with patch(
            "bond_seal_pages.date_registration._feature_homography",
            side_effect=ValueError("模拟特征配准误匹配"),
        ):
            dated = prepare_returned_page_with_date(
                self.returned_pdf,
                0,
                date(2026, 7, 15),
                template_pdf=self.template_pdf,
                template_page_index=0,
            )

        self.assertEqual(
            dated.result.status,
            SigningDateStatus.FILLED_NEEDS_REVIEW,
        )
        self.assertIn("重点核对", dated.result.reason)

    def test_disagreement_uses_better_candidate_and_flags_review(self):
        import numpy

        wrong_feature_mapping = numpy.array(
            (
                (0.94, 0.0, -120.0),
                (0.0, 0.94, -80.0),
                (0.0, 0.0, 1.0),
            ),
            dtype=numpy.float64,
        )
        with patch(
            "bond_seal_pages.date_registration._feature_homography",
            return_value=(wrong_feature_mapping, 0.99),
        ):
            dated = prepare_returned_page_with_date(
                self.returned_pdf,
                0,
                date(2026, 7, 15),
                template_pdf=self.template_pdf,
                template_page_index=0,
            )

        self.assertEqual(
            dated.result.status,
            SigningDateStatus.FILLED_NEEDS_REVIEW,
        )
        self.assertIn("定位结果存在分歧", dated.result.reason)
        self.assertIn("灰度相关性", dated.result.reason)

    @unittest.skipUnless(
        _REAL_RETURNED_PDF.exists() and _REAL_TEMPLATE_PDF.exists(),
        "仓库中没有实际第 10 页回归素材",
    )
    def test_real_page_10_does_not_overlap_the_original_date_labels(self):
        dated = prepare_returned_page_with_date(
            _REAL_RETURNED_PDF,
            9,
            date(2026, 7, 15),
            template_pdf=_REAL_TEMPLATE_PDF,
            template_page_index=3,
        )

        self.assertIn(
            dated.result.status,
            {
                SigningDateStatus.FILLED,
                SigningDateStatus.FILLED_NEEDS_REVIEW,
            },
        )
        output_pdf = self.root / "第10页日期确认.pdf"
        write_dated_page(output_pdf, dated.page)
        with pdfplumber.open(str(output_pdf)) as document:
            day = next(
                word
                for word in document.pages[0].extract_words(
                    x_tolerance=1,
                    y_tolerance=1,
                )
                if word["text"] == "15"
            )
        original = render_pdf_page(_REAL_RETURNED_PDF, 9, scale=2.0).convert("RGB")
        scale_x = original.width / 595.44
        scale_y = original.height / 842.4
        region = original.crop(
            (
                int(day["x0"] * scale_x),
                int(day["top"] * scale_y),
                int(day["x1"] * scale_x + 1),
                int(day["bottom"] * scale_y + 1),
            )
        )
        black_fraction = sum(
            max(pixel) < 145 for pixel in region.get_flattened_data()
        ) / (
            region.width * region.height
        )
        self.assertLess(black_fraction, 0.05)

    def test_unregistrable_scan_fails_without_guessing(self):
        write_scanned_return(
            self.template_pdf,
            self.returned_pdf,
            featureless=True,
        )

        dated = prepare_returned_page_with_date(
            self.returned_pdf,
            0,
            date(2026, 7, 15),
            template_pdf=self.template_pdf,
            template_page_index=0,
        )

        self.assertEqual(dated.result.status, SigningDateStatus.FAILED)
        self.assertIn("配准", dated.result.reason)
        output_pdf = self.root / "未猜测写入.pdf"
        write_dated_page(output_pdf, dated.page)
        self.assertNotIn("2026", PdfReader(str(output_pdf)).pages[0].extract_text() or "")

    def test_date_review_passes_each_matched_original_page_as_template(self):
        batch_root = create_processing_batch(
            self.root,
            [("扫描件.docx", "扫描确认函")],
        )
        manifest_path = batch_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        converted_pdf = batch_root / manifest["items"][0]["converted_pdf"]
        write_template_pdf(converted_pdf, with_body=True)
        manifest["items"][0]["pdf_page_count"] = 2
        manifest["items"][0]["pdf_sha256"] = sha256_file(converted_pdf)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        write_scanned_return(converted_pdf, self.returned_pdf)

        review = create_date_review(
            batch_root,
            self.returned_pdf,
            self.root / "日期确认",
            ocr_engine=StubOCREngine(),
            signing_date=date(2026, 7, 15),
        )

        self.assertEqual(review.items[0].status, "review_ready")
        self.assertEqual(
            review.items[0].date_status,
            "filled_needs_review",
        )
        self.assertIn("重点核对", review.items[0].date_reason)
        review_text = PdfReader(str(review.review_pdf)).pages[0].extract_text()
        self.assertIn("2026", review_text)


if __name__ == "__main__":
    unittest.main()

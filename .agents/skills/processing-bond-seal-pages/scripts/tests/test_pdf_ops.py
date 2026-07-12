import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, TextStringObject


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.date_layout import Box  # noqa: E402
from bond_seal_pages.pdf_ops import (  # noqa: E402
    extract_bracket_titles,
    extract_date_anchors,
    extract_last_page,
    extract_page_text,
    get_pdf_page_count,
    merge_pdf_pages,
    replace_last_page,
    sha256_file,
)


def _write_three_page_pdf(path):
    writer = PdfWriter()
    sizes = [(200, 300), (400, 500), (600, 700)]
    for index, (width, height) in enumerate(sizes, start=1):
        page = writer.add_blank_page(width=width, height=height)
        page[NameObject("/PageLabel")] = TextStringObject(f"第{index}页")
    with open(path, "wb") as output:
        writer.write(output)


class PdfOperationTests(unittest.TestCase):
    def test_page_count_hash_extract_merge_and_replace_last_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "三页.pdf"
            returned = root / "返回.pdf"
            last = root / "末页.pdf"
            merged = root / "选页.pdf"
            replaced = root / "替换.pdf"
            _write_three_page_pdf(source)

            returned_writer = PdfWriter()
            returned_page = returned_writer.add_blank_page(width=300, height=350)
            returned_page[NameObject("/PageLabel")] = TextStringObject("返回页")
            with returned.open("wb") as output:
                returned_writer.write(output)

            self.assertEqual(get_pdf_page_count(source), 3)
            expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertEqual(sha256_file(source), expected_hash)
            self.assertEqual(sha256_file(source), sha256_file(source))

            extract_last_page(source, last)
            self.assertEqual(get_pdf_page_count(last), 1)
            self.assertEqual(PdfReader(last).pages[0]["/PageLabel"], "第3页")

            merge_pdf_pages([(source, 1), (source, 0)], merged)
            merged_pages = PdfReader(merged).pages
            self.assertEqual(len(merged_pages), 2)
            self.assertEqual([page["/PageLabel"] for page in merged_pages], ["第2页", "第1页"])

            original = PdfReader(source)
            original_prefix = [page["/PageLabel"] for page in original.pages[:2]]
            original_prefix_sizes = [
                (float(page.mediabox.width), float(page.mediabox.height))
                for page in original.pages[:2]
            ]
            replace_last_page(source, returned, replaced)
            result = PdfReader(replaced)
            self.assertEqual(len(result.pages), 3)
            self.assertEqual([page["/PageLabel"] for page in result.pages[:2]], original_prefix)
            self.assertEqual(
                [(float(page.mediabox.width), float(page.mediabox.height)) for page in result.pages[:2]],
                original_prefix_sizes,
            )
            self.assertEqual(result.pages[-1]["/PageLabel"], "返回页")
            self.assertEqual(
                (float(result.pages[-1].mediabox.width), float(result.pages[-1].mediabox.height)),
                (300.0, 350.0),
            )

    def test_extracts_text_title_and_date_anchors(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "文字层.pdf"
            try:
                from reportlab.pdfbase import pdfmetrics
                from reportlab.pdfbase.cidfonts import UnicodeCIDFont
                from reportlab.pdfgen.canvas import Canvas
            except ImportError:
                self.skipTest("测试环境缺少 reportlab")

            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            canvas = Canvas(str(pdf), pagesize=(500, 700))
            canvas.setFont("STSong-Light", 14)
            canvas.drawString(50, 650, "《履约保函》")
            canvas.drawString(50, 100, "2026 年 7 月 12 日")
            canvas.save()

            text = extract_page_text(pdf, 0)
            self.assertIn("履约保函", text)
            self.assertEqual(extract_bracket_titles(pdf, 0), ("履约保函",))
            anchors = extract_date_anchors(pdf, 0)
            self.assertEqual([anchor.component for anchor in anchors], ["year", "month", "day"])
            self.assertTrue(all(isinstance(anchor.box, Box) for anchor in anchors))
            self.assertTrue(all(anchor.font_size > 0 for anchor in anchors))


if __name__ == "__main__":
    unittest.main()

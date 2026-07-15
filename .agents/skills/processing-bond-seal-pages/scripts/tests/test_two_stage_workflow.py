from collections import Counter
from datetime import date
import json
import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.completion import complete_processing_batch  # noqa: E402
from bond_seal_pages.processing_batch import prepare_processing_batch  # noqa: E402
from completion_test_support import (  # noqa: E402
    write_pdf_pages,
    write_scanned_pdf_pages,
)


class ContractConverter:
    def __init__(self, titles, failing_name):
        self.titles = titles
        self.failing_name = failing_name
        self.calls = []

    def convert(self, input_path, output_path):
        input_path = Path(input_path)
        self.calls.append(input_path.name)
        if input_path.name == self.failing_name:
            raise RuntimeError("固定样本转换失败")
        write_pdf_pages(
            output_path,
            [
                (f"正文-{input_path.name}", (420, 620)),
                (f"《{self.titles[input_path.name]}》", (420, 620)),
            ],
        )


class StubOCREngine:
    def __init__(self, title):
        self.title = title
        self.calls = []

    def recognize_page(self, pdf_path, page_number):
        self.calls.append((Path(pdf_path), page_number))
        return (f"《{self.title}》",)


def write_date_page(path, title, existing=None):
    existing = existing or {}
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    canvas = Canvas(str(path), pagesize=(500, 700))
    canvas.setFont("STSong-Light", 14)
    canvas.drawString(50, 650, f"《{title}》")
    for component, x, label in (
        ("year", 100, "年"),
        ("month", 180, "月"),
        ("day", 240, "日"),
    ):
        value = existing.get(component)
        if value is not None:
            canvas.drawRightString(x, 100, str(value))
        canvas.drawString(x, 100, label)
    canvas.showPage()
    canvas.save()


def combine_first_pages(output_path, *input_paths):
    writer = PdfWriter()
    for input_path in input_paths:
        writer.add_page(PdfReader(str(input_path)).pages[0])
    with Path(output_path).open("wb") as output:
        writer.write(output)


class TwoStageWorkflowTests(unittest.TestCase):
    def test_full_batch_handles_disorder_missing_duplicates_scan_and_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working_papers = root / "底稿文件"
            batch_root = root / "处理批次"
            output_root = root / "回拼结果"
            working_papers.mkdir()
            titles = {
                "01-同标题甲.docx": "同标题确认函",
                "02-同标题乙.doc": "同标题确认函",
                "03-文字页.docx": "文字层确认函",
                "04-扫描页.docx": "扫描识别确认函",
                "05-缺页.docx": "缺页确认函",
                "06-低置信度.docx": "关于长期偿债安排的专项确认函",
                "07-转换失败.docx": "转换失败确认函",
            }
            original_bytes = {}
            for name in titles:
                content = f"word fixture: {name}".encode("utf-8")
                (working_papers / name).write_bytes(content)
                original_bytes[name] = content
            converter = ContractConverter(titles, "07-转换失败.docx")

            prepared = prepare_processing_batch(
                working_papers,
                batch_root,
                converter=converter,
            )

            self.assertEqual((prepared.succeeded, prepared.failed), (6, 1))
            self.assertEqual(len(PdfReader(str(prepared.seal_pages_path)).pages), 6)
            manifest = json.loads(prepared.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["items"]), 7)
            self.assertEqual(
                [item.get("title") for item in manifest["items"]].count("同标题确认函"),
                2,
            )
            for name, content in original_bytes.items():
                self.assertEqual((working_papers / name).read_bytes(), content)

            low_page = root / "low.pdf"
            scan_page = root / "scan.pdf"
            duplicate_page = root / "duplicate.pdf"
            text_page = root / "text.pdf"
            returned_pdf = root / "回章页合集.pdf"
            write_date_page(low_page, "关于短期偿债安排的确认函")
            write_scanned_pdf_pages(scan_page, [("gray", None)])
            write_date_page(
                duplicate_page,
                "同标题确认函",
                {"year": 2025},
            )
            write_date_page(text_page, "文字层确认函")
            combine_first_pages(
                returned_pdf,
                low_page,
                scan_page,
                duplicate_page,
                text_page,
            )
            ocr_engine = StubOCREngine("扫描识别确认函")

            completed = complete_processing_batch(
                batch_root,
                returned_pdf,
                output_root,
                ocr_engine=ocr_engine,
                signing_date=date(2026, 7, 15),
            )

            outcomes = Counter(item.status for item in completed.items)
            self.assertEqual(
                outcomes,
                Counter(
                    {
                        "completed": 4,
                        "low_confidence": 2,
                        "conversion_failed": 1,
                    }
                ),
            )
            by_id = {item.working_paper_id: item for item in completed.items}
            self.assertEqual(by_id["01-同标题甲.docx"].returned_page, 3)
            self.assertEqual(by_id["02-同标题乙.doc"].returned_page, 3)
            self.assertEqual(by_id["06-低置信度.docx"].score, 85)
            self.assertEqual(by_id["05-缺页.docx"].status, "low_confidence")
            self.assertEqual(ocr_engine.calls, [(returned_pdf, 2)])
            self.assertIn(
                by_id["01-同标题甲.docx"].date_status,
                {"filled", "partial"},
            )

            scanned_output = by_id["04-扫描页.docx"].output_path
            returned_image = PdfReader(str(returned_pdf)).pages[1].images[0].data
            output_image = PdfReader(str(scanned_output)).pages[-1].images[0].data
            self.assertEqual(output_image, returned_image)
            self.assertEqual(len(list((output_root / "completed-pdfs").rglob("*.pdf"))), 4)

            report = completed.report_path.read_text(encoding="utf-8")
            self.assertEqual(report.count('class="case-row'), 7)
            self.assertNotIn("<img", report.lower())
            self.assertNotIn("data:image", report.lower())
            self.assertIn("低置信度", report)
            self.assertIn("转换失败", report)


if __name__ == "__main__":
    unittest.main()

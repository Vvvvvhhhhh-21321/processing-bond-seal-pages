import json
import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.completion import complete_processing_batch  # noqa: E402
from bond_seal_pages.pdf_ops import sha256_file  # noqa: E402


def _write_pdf(path, pages):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    canvas = Canvas(str(path), pagesize=pages[0][1])
    for index, (text, size) in enumerate(pages):
        if index:
            canvas.setPageSize(size)
        canvas.setFont("STSong-Light", 14)
        canvas.drawString(50, size[1] - 50, text)
        canvas.showPage()
    canvas.save()


def _create_batch(root, records):
    batch_root = root / "处理批次"
    items = []
    for working_paper_id, title in records:
        converted_pdf = batch_root / "pdfs" / f"{working_paper_id}.pdf"
        converted_pdf.parent.mkdir(parents=True, exist_ok=True)
        _write_pdf(
            converted_pdf,
            [(f"正文-{working_paper_id}", (400, 600)), (f"《{title}》", (400, 600))],
        )
        items.append(
            {
                "working_paper_id": working_paper_id,
                "working_paper_path": working_paper_id,
                "status": "ready",
                "title": title,
                "normalized_title": title,
                "converted_pdf": converted_pdf.relative_to(batch_root).as_posix(),
                "pdf_page_count": 2,
                "pdf_sha256": sha256_file(converted_pdf),
                "seal_page": len(items) + 1,
            }
        )
    (batch_root / "manifest.json").write_text(
        json.dumps({"version": 1, "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    return batch_root


class CompletionBatchTests(unittest.TestCase):
    def test_completes_out_of_order_text_pages_and_leaves_missing_item_unmatched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = _create_batch(
                root,
                [("甲.docx", "甲方确认函"), ("乙.docx", "乙方确认函"), ("丙.docx", "丙方确认函")],
            )
            returned_pdf = root / "回章页合集.pdf"
            _write_pdf(
                returned_pdf,
                [
                    ("《乙方确认函》", (500, 700)),
                    ("完全无关内容", (450, 650)),
                    ("《甲方确认函》", (600, 800)),
                ],
            )

            result = complete_processing_batch(batch_root, returned_pdf, root / "回拼结果")

            items = {item.working_paper_id: item for item in result.items}
            self.assertEqual(items["甲.docx"].status, "completed")
            self.assertEqual(items["甲.docx"].returned_page, 3)
            self.assertEqual(items["乙.docx"].status, "completed")
            self.assertEqual(items["乙.docx"].returned_page, 1)
            self.assertEqual(items["丙.docx"].status, "unmatched")
            self.assertEqual(result.unused_pages, (2,))

            first_output = PdfReader(str(items["甲.docx"].output_path))
            self.assertIn("正文-甲.docx", first_output.pages[0].extract_text())
            self.assertIn("甲方确认函", first_output.pages[-1].extract_text())
            self.assertEqual(
                tuple(map(float, first_output.pages[-1].mediabox[2:])),
                (600.0, 800.0),
            )
            self.assertTrue(items["乙.docx"].output_path.exists())
            self.assertIsNone(items["丙.docx"].output_path)

    def test_marks_a_similar_page_below_90_as_low_confidence_and_ignores_no_title_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = _create_batch(root, [("申请表.docx", "履约保函申请表")])
            returned_pdf = root / "回章页合集.pdf"
            _write_pdf(
                returned_pdf,
                [("《履约保函申请单》", (500, 700)), ("", (500, 700))],
            )

            result = complete_processing_batch(batch_root, returned_pdf, root / "回拼结果")

            item = result.items[0]
            self.assertEqual(item.status, "low_confidence")
            self.assertEqual(item.returned_page, 1)
            self.assertEqual(item.score, 86)
            self.assertIsNone(item.output_path)
            self.assertEqual(result.unused_pages, (1, 2))


if __name__ == "__main__":
    unittest.main()

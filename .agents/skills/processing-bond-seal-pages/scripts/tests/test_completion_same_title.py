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


def _write_pages(path, texts):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    canvas = Canvas(str(path), pagesize=(400, 600))
    for text in texts:
        canvas.setFont("STSong-Light", 14)
        canvas.drawString(50, 550, text)
        canvas.showPage()
    canvas.save()


class CompletionSameTitleTests(unittest.TestCase):
    def test_uses_each_same_title_page_before_reusing_the_first_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = root / "处理批次"
            items = []
            for name in ("一.docx", "二.docx", "三.docx"):
                converted_pdf = batch_root / "pdfs" / f"{name}.pdf"
                converted_pdf.parent.mkdir(parents=True, exist_ok=True)
                _write_pages(converted_pdf, [f"正文-{name}", "《共同确认函》"])
                items.append(
                    {
                        "working_paper_id": name,
                        "working_paper_path": name,
                        "status": "ready",
                        "title": "共同确认函",
                        "normalized_title": "共同确认函",
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
            returned_pdf = root / "回章页合集.pdf"
            _write_pages(
                returned_pdf,
                ["第一张《共同确认函》", "第二张《共同确认函》"],
            )

            result = complete_processing_batch(batch_root, returned_pdf, root / "回拼结果")

            self.assertEqual(
                [item.returned_page for item in result.items],
                [1, 2, 1],
            )
            self.assertEqual(result.unused_pages, ())
            self.assertIn(
                "第一张",
                PdfReader(str(result.items[0].output_path)).pages[-1].extract_text(),
            )
            self.assertIn(
                "第二张",
                PdfReader(str(result.items[1].output_path)).pages[-1].extract_text(),
            )


if __name__ == "__main__":
    unittest.main()

import json
import sys
import tempfile
import unittest
from pathlib import Path

from reportlab.pdfgen.canvas import Canvas


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.completion import complete_processing_batch  # noqa: E402
from bond_seal_pages.pdf_ops import sha256_file  # noqa: E402


def _write_pages(path, texts):
    canvas = Canvas(str(path), pagesize=(400, 600))
    for text in texts:
        canvas.drawString(50, 550, text)
        canvas.showPage()
    canvas.save()


class CompletionValidationTests(unittest.TestCase):
    def test_invalid_pdf_item_does_not_block_another_working_paper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = root / "处理批次"
            broken_pdf = batch_root / "pdfs" / "损坏.docx.pdf"
            broken_pdf.parent.mkdir(parents=True)
            broken_pdf.write_bytes(b"not a pdf")
            valid_pdf = batch_root / "pdfs" / "有效.docx.pdf"
            _write_pages(valid_pdf, ["body", "Valid Title"])
            items = [
                {
                    "working_paper_id": "损坏.docx",
                    "working_paper_path": "损坏.docx",
                    "status": "ready",
                    "title": "Broken Title",
                    "normalized_title": "BrokenTitle",
                    "converted_pdf": broken_pdf.relative_to(batch_root).as_posix(),
                    "pdf_page_count": 2,
                    "pdf_sha256": sha256_file(broken_pdf),
                    "seal_page": 1,
                },
                {
                    "working_paper_id": "有效.docx",
                    "working_paper_path": "有效.docx",
                    "status": "ready",
                    "title": "Valid Title",
                    "normalized_title": "ValidTitle",
                    "converted_pdf": valid_pdf.relative_to(batch_root).as_posix(),
                    "pdf_page_count": 2,
                    "pdf_sha256": sha256_file(valid_pdf),
                    "seal_page": 2,
                },
            ]
            (batch_root / "manifest.json").write_text(
                json.dumps({"version": 1, "items": items}),
                encoding="utf-8",
            )
            returned_pdf = root / "returned.pdf"
            _write_pages(returned_pdf, ["Valid Title"])

            result = complete_processing_batch(batch_root, returned_pdf, root / "回拼结果")

            self.assertEqual(result.items[0].status, "invalid_batch")
            self.assertIn("PDF", result.items[0].reason)
            self.assertEqual(result.items[1].status, "completed")
            self.assertTrue(result.items[1].output_path.exists())


if __name__ == "__main__":
    unittest.main()

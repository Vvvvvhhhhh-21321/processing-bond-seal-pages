import sys
import tempfile
import unittest
from pathlib import Path

from reportlab.pdfgen.canvas import Canvas


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.completion import complete_processing_batch  # noqa: E402
from completion_test_support import create_processing_batch  # noqa: E402


class CompletionTitleExtractionTests(unittest.TestCase):
    def test_uses_prominent_main_title_instead_of_longer_body_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(
                root,
                [("different.docx", "Payment Confirmation")],
            )
            returned_pdf = root / "returned.pdf"
            canvas = Canvas(str(returned_pdf), pagesize=(500, 700))
            canvas.setFont("Helvetica", 20)
            canvas.drawString(50, 650, "Payment Confirmation")
            canvas.setFont("Helvetica", 10)
            canvas.drawString(50, 610, "This is ordinary body text that is much longer than the title above.")
            canvas.showPage()
            canvas.setFont("Helvetica", 10)
            canvas.drawString(50, 650, "This page only contains unrelated ordinary body text.")
            canvas.save()

            result = complete_processing_batch(batch_root, returned_pdf, root / "回拼结果")

            self.assertEqual(result.items[0].status, "completed")
            self.assertEqual(result.items[0].returned_page, 1)
            self.assertEqual(result.unused_pages, (2,))


if __name__ == "__main__":
    unittest.main()

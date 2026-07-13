import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.completion import complete_processing_batch  # noqa: E402
from completion_test_support import create_processing_batch, write_pdf_pages  # noqa: E402


class CompletionSameTitleTests(unittest.TestCase):
    def test_uses_each_same_title_page_when_counts_are_equal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(
                root,
                [("一.docx", "共同确认函"), ("二.docx", "共同确认函")],
            )
            returned_pdf = root / "回章页合集.pdf"
            write_pdf_pages(
                returned_pdf,
                [("第一张《共同确认函》", (400, 600)), ("第二张《共同确认函》", (400, 600))],
            )

            result = complete_processing_batch(batch_root, returned_pdf, root / "回拼结果")

            self.assertEqual([item.returned_page for item in result.items], [1, 2])
            self.assertEqual(result.unused_pages, ())

    def test_uses_each_same_title_page_before_reusing_the_first_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(
                root,
                [
                    ("一.docx", "共同确认函"),
                    ("二.docx", "共同确认函"),
                    ("三.docx", "共同确认函"),
                ],
            )
            returned_pdf = root / "回章页合集.pdf"
            write_pdf_pages(
                returned_pdf,
                [("第一张《共同确认函》", (400, 600)), ("第二张《共同确认函》", (400, 600))],
            )

            result = complete_processing_batch(batch_root, returned_pdf, root / "回拼结果")

            self.assertEqual([item.returned_page for item in result.items], [1, 2, 1])
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

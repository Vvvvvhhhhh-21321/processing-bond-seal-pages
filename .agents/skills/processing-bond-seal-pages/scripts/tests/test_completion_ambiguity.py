import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.completion import complete_processing_batch  # noqa: E402
from completion_test_support import create_processing_batch, write_pdf_pages  # noqa: E402


class CompletionAmbiguityTests(unittest.TestCase):
    def test_skips_a_returned_page_tied_between_distinct_titles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(
                root,
                [("甲.docx", "abcdefghijA"), ("乙.docx", "abcdefghijB")],
            )
            returned_pdf = root / "returned.pdf"
            write_pdf_pages(returned_pdf, [("abcdefghijC", (400, 600))])

            result = complete_processing_batch(batch_root, returned_pdf, root / "回拼结果")

            self.assertEqual([item.status for item in result.items], ["ambiguous", "ambiguous"])
            self.assertTrue(all(item.returned_page == 1 for item in result.items))
            self.assertTrue(all(item.score == 91 for item in result.items))
            self.assertTrue(all(item.output_path is None for item in result.items))
            self.assertEqual(result.unused_pages, (1,))


if __name__ == "__main__":
    unittest.main()

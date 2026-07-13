import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.completion import complete_processing_batch  # noqa: E402
from bond_seal_pages.pdf_ops import sha256_file  # noqa: E402
from completion_test_support import create_processing_batch, write_pdf_pages  # noqa: E402


class CompletionValidationTests(unittest.TestCase):
    def test_invalid_pdf_item_does_not_block_another_working_paper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(
                root,
                [("损坏.docx", "Broken Title"), ("有效.docx", "Valid Title")],
            )
            broken_pdf = batch_root / "pdfs" / "损坏.docx.pdf"
            broken_pdf.write_bytes(b"not a pdf")
            manifest_path = batch_root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["items"][0]["pdf_sha256"] = sha256_file(broken_pdf)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            returned_pdf = root / "returned.pdf"
            write_pdf_pages(returned_pdf, [("Valid Title", (400, 600))])

            result = complete_processing_batch(batch_root, returned_pdf, root / "回拼结果")

            self.assertEqual(result.items[0].status, "invalid_batch")
            self.assertIn("PDF", result.items[0].reason)
            self.assertEqual(result.items[1].status, "completed")
            self.assertTrue(result.items[1].output_path.exists())


if __name__ == "__main__":
    unittest.main()

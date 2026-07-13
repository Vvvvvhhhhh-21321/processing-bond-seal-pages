import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from reportlab.pdfgen.canvas import Canvas


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.processing_batch import prepare_processing_batch  # noqa: E402


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class SuccessfulConverter:
    def convert(self, working_paper_path, output_path):
        canvas = Canvas(str(output_path), pagesize=(500, 700))
        canvas.drawString(50, 650, "last page")
        canvas.save()


class ProcessingBatchOverwriteTests(unittest.TestCase):
    def test_overwrites_generated_results_without_changing_word_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working_paper_root = root / "底稿"
            batch = root / "处理批次"
            working_paper_root.mkdir()
            working_paper_path = working_paper_root / "底稿.docx"
            working_paper_path.write_bytes(b"original word file")
            original_hash = _sha256(working_paper_path)
            (batch / "pdfs").mkdir(parents=True)
            stale_pdf = batch / "pdfs" / "旧结果.pdf"
            stale_pdf.write_bytes(b"stale")

            result = prepare_processing_batch(
                working_paper_root,
                batch,
                converter=SuccessfulConverter(),
            )

            self.assertFalse(stale_pdf.exists())
            self.assertEqual(result.succeeded, 1)
            self.assertEqual(_sha256(working_paper_path), original_hash)


if __name__ == "__main__":
    unittest.main()

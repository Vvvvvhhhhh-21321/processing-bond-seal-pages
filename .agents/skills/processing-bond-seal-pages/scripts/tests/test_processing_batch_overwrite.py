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
    def convert(self, source_path, output_path):
        canvas = Canvas(str(output_path), pagesize=(500, 700))
        canvas.drawString(50, 650, "last page")
        canvas.save()


class ProcessingBatchOverwriteTests(unittest.TestCase):
    def test_overwrites_an_existing_batch_without_changing_word_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "底稿"
            batch = root / "处理批次"
            source.mkdir()
            word_path = source / "底稿.docx"
            word_path.write_bytes(b"original word file")
            original_hash = _sha256(word_path)
            batch.mkdir()
            (batch / "旧结果.txt").write_text("stale", encoding="utf-8")

            result = prepare_processing_batch(source, batch, converter=SuccessfulConverter())

            self.assertFalse((batch / "旧结果.txt").exists())
            self.assertEqual(result.succeeded, 1)
            self.assertEqual(_sha256(word_path), original_hash)


if __name__ == "__main__":
    unittest.main()

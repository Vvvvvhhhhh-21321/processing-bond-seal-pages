import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.processing_batch import prepare_processing_batch  # noqa: E402


class ProcessingBatchSafetyTests(unittest.TestCase):
    def test_rejects_a_batch_directory_that_would_delete_working_papers(self):
        with tempfile.TemporaryDirectory() as directory:
            working_paper_root = Path(directory) / "底稿"
            working_paper_root.mkdir()
            working_paper = working_paper_root / "底稿.docx"
            working_paper.write_bytes(b"original word file")

            with self.assertRaisesRegex(ValueError, "处理批次目录不得等于或包含底稿目录"):
                prepare_processing_batch(working_paper_root, working_paper_root)

            self.assertEqual(working_paper.read_bytes(), b"original word file")

    def test_preserves_unrelated_files_when_overwriting_generated_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working_paper_root = root / "底稿"
            batch_root = root / "处理批次"
            working_paper_root.mkdir()
            batch_root.mkdir()
            (batch_root / "pdfs").mkdir()
            (batch_root / "pdfs" / "旧结果.pdf").write_bytes(b"stale")
            unrelated = batch_root / "用户备注.txt"
            unrelated.write_text("保留", encoding="utf-8")

            result = prepare_processing_batch(working_paper_root, batch_root, converter=object())

            self.assertEqual(result.succeeded, 0)
            self.assertFalse((batch_root / "pdfs" / "旧结果.pdf").exists())
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "保留")


if __name__ == "__main__":
    unittest.main()

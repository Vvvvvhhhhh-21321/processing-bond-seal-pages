import json
import sys
import tempfile
import unittest
from pathlib import Path

from reportlab.pdfgen.canvas import Canvas


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.processing_batch import prepare_processing_batch  # noqa: E402


class SameTitleConverter:
    def convert(self, working_paper_path, output_path):
        canvas = Canvas(str(output_path), pagesize=(500, 700))
        canvas.drawString(50, 650, "same title")
        canvas.save()


class ProcessingBatchIdentityTests(unittest.TestCase):
    def test_identical_word_files_keep_distinct_working_paper_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working_paper_root = root / "底稿"
            working_paper_root.mkdir()
            (working_paper_root / "甲.docx").write_bytes(b"identical content")
            (working_paper_root / "乙.docx").write_bytes(b"identical content")

            result = prepare_processing_batch(
                working_paper_root,
                root / "处理批次",
                converter=SameTitleConverter(),
            )

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            working_paper_ids = [
                item["working_paper_id"] for item in manifest["items"]
            ]
            self.assertEqual(len(working_paper_ids), 2)
            self.assertEqual(len(set(working_paper_ids)), 2)


if __name__ == "__main__":
    unittest.main()

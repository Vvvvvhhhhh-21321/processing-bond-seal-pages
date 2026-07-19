import json
import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.processing_batch import prepare_processing_batch  # noqa: E402


class SameTitleConverter:
    def __init__(self):
        self.calls = []

    def convert(self, working_paper_path, output_path):
        self.calls.append(Path(working_paper_path).name)
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

    def test_deduplicate_excludes_repeated_word_files_from_the_entire_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working_paper_root = root / "底稿"
            working_paper_root.mkdir()
            (working_paper_root / "01-保留.docx").write_bytes(b"identical content")
            (working_paper_root / "02-排除.docx").write_bytes(b"identical content")
            (working_paper_root / "03-不同.docx").write_bytes(b"different content")
            converter = SameTitleConverter()

            result = prepare_processing_batch(
                working_paper_root,
                root / "处理批次",
                converter=converter,
                duplicate_policy="deduplicate",
            )

            self.assertEqual(result.succeeded, 2)
            self.assertEqual(result.failed, 0)
            self.assertEqual(result.excluded_duplicates, 1)
            self.assertEqual(converter.calls, ["01-保留.docx", "03-不同.docx"])
            self.assertEqual(PdfReader(str(result.seal_pages_path)).get_num_pages(), 2)

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["duplicate_policy"], "deduplicate")
            self.assertEqual(
                [item["working_paper_path"] for item in manifest["items"]],
                ["01-保留.docx", "03-不同.docx"],
            )
            self.assertEqual(
                manifest["excluded_duplicates"],
                [
                    {
                        "working_paper_id": "02-排除.docx",
                        "working_paper_path": "02-排除.docx",
                        "working_paper_sha256": manifest["items"][0][
                            "working_paper_sha256"
                        ],
                        "duplicate_of": "01-保留.docx",
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()

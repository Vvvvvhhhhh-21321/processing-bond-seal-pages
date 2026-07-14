import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.completion import complete_processing_batch  # noqa: E402
from completion_test_support import create_processing_batch, write_pdf_pages  # noqa: E402


class CompletionDuplicateMappingTests(unittest.TestCase):
    def _run_with_duplicate(self, field):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        batch_root = create_processing_batch(
            root,
            [("甲.docx", "甲方确认函"), ("乙.docx", "乙方确认函"), ("有效.docx", "有效确认函")],
        )
        manifest_path = batch_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["items"][1][field] = manifest["items"][0][field]
        if field == "converted_pdf":
            manifest["items"][1]["pdf_page_count"] = manifest["items"][0]["pdf_page_count"]
            manifest["items"][1]["pdf_sha256"] = manifest["items"][0]["pdf_sha256"]
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        returned_pdf = root / "returned.pdf"
        write_pdf_pages(returned_pdf, [("《有效确认函》", (400, 600))])
        return complete_processing_batch(batch_root, returned_pdf, root / "回拼结果")

    def test_duplicate_working_paper_ids_invalidate_both_records_only(self):
        result = self._run_with_duplicate("working_paper_id")

        self.assertEqual(
            [item.status for item in result.items],
            ["invalid_batch", "invalid_batch", "completed"],
        )
        self.assertTrue(all("重复" in item.reason for item in result.items[:2]))

    def test_duplicate_working_paper_paths_invalidate_both_records_only(self):
        result = self._run_with_duplicate("working_paper_path")

        self.assertEqual(
            [item.status for item in result.items],
            ["invalid_batch", "invalid_batch", "completed"],
        )
        self.assertTrue(all("重复" in item.reason for item in result.items[:2]))

    def test_duplicate_converted_pdf_paths_invalidate_both_records_only(self):
        result = self._run_with_duplicate("converted_pdf")

        self.assertEqual(
            [item.status for item in result.items],
            ["invalid_batch", "invalid_batch", "completed"],
        )
        self.assertTrue(all("重复" in item.reason for item in result.items[:2]))


if __name__ == "__main__":
    unittest.main()

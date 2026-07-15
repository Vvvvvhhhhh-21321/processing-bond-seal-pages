import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.completion import complete_processing_batch  # noqa: E402
from bond_seal_pages.processing_batch import prepare_processing_batch  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "仅在 Windows 上运行真实两阶段验收")
class TwoStageRealSmokeTests(unittest.TestCase):
    def test_real_working_papers_and_returned_pdf_complete_both_stages(self):
        working_paper_root = os.environ.get(
            "BOND_SEAL_PAGES_E2E_WORKING_PAPER_ROOT"
        )
        returned_pdf = os.environ.get("BOND_SEAL_PAGES_E2E_RETURNED_PDF")
        if not working_paper_root or not returned_pdf:
            self.skipTest(
                "请同时设置 BOND_SEAL_PAGES_E2E_WORKING_PAPER_ROOT 和 "
                "BOND_SEAL_PAGES_E2E_RETURNED_PDF"
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepare_processing_batch(
                working_paper_root,
                root / "batch",
            )
            completed = complete_processing_batch(
                root / "batch",
                returned_pdf,
                root / "output",
            )

            self.assertGreater(prepared.succeeded, 0)
            self.assertEqual(prepared.failed, 0)
            self.assertEqual(completed.succeeded, prepared.succeeded)
            self.assertTrue(completed.report_path.is_file())
            report = completed.report_path.read_text(encoding="utf-8").lower()
            self.assertNotIn("<img", report)
            self.assertNotIn("data:image", report)


if __name__ == "__main__":
    unittest.main()

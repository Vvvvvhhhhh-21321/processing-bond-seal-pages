from datetime import date
import os
import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.processing_batch import prepare_processing_batch  # noqa: E402
from bond_seal_pages.review_workflow import (  # noqa: E402
    create_date_review,
    finalize_date_review,
)


@unittest.skipUnless(sys.platform == "win32", "仅在 Windows 上运行真实两阶段验收")
class TwoStageRealSmokeTests(unittest.TestCase):
    def test_real_working_papers_pause_for_date_review_before_finalization(self):
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
            review = create_date_review(
                root / "batch",
                returned_pdf,
                root / "date-review",
                signing_date=date(2026, 7, 15),
            )

            self.assertGreater(prepared.succeeded, 0)
            self.assertEqual(prepared.failed, 0)
            self.assertEqual(review.succeeded, prepared.succeeded)
            self.assertTrue(review.review_pdf.is_file())
            self.assertEqual(
                len(PdfReader(str(review.review_pdf)).pages),
                len(PdfReader(str(returned_pdf)).pages),
            )
            self.assertFalse((root / "output").exists())

            completed = finalize_date_review(
                root / "batch",
                root / "date-review",
                root / "output",
                confirmed=True,
            )

            self.assertEqual(completed.succeeded, prepared.succeeded)
            self.assertTrue(completed.report_path.is_file())
            report = completed.report_path.read_text(encoding="utf-8").lower()
            self.assertNotIn("<img", report)
            self.assertNotIn("data:image", report)


if __name__ == "__main__":
    unittest.main()

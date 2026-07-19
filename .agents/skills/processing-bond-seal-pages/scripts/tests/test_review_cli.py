from datetime import date
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.cli import main  # noqa: E402
from test_cli import ready_preflight  # noqa: E402


class ReviewCliTests(unittest.TestCase):
    def test_date_review_pauses_with_one_pdf_and_machine_readable_mapping(self):
        calls = []

        def runner(batch_root, returned_pdf, review_root, **options):
            calls.append((Path(batch_root), Path(returned_pdf), Path(review_root), options))
            return SimpleNamespace(
                items=(
                    SimpleNamespace(
                        working_paper_id="甲.docx",
                        status="review_ready",
                        returned_page=2,
                        score=100,
                        output_path=Path(review_root) / "dated-returned-pages.pdf",
                        reason=None,
                        date_status="filled_needs_review",
                        date_reason="两套定位结果存在分歧，已采用较可靠结果，请重点核对",
                    ),
                ),
                succeeded=1,
                unused_pages=(),
                review_root=Path(review_root),
                review_pdf=Path(review_root) / "dated-returned-pages.pdf",
                manifest_path=Path(review_root) / "review-manifest.json",
                ocr_failures=(),
            )

        output = StringIO()
        code = main(
            [
                "date-review",
                "处理批次",
                "回章页合集.pdf",
                "日期确认",
                "--signing-date",
                "2026-07-15",
            ],
            preflight_runner=lambda **options: ready_preflight(),
            date_review_runner=runner,
            output=output,
        )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["command"], "date-review")
        self.assertEqual(payload["status"], "awaiting_confirmation")
        self.assertTrue(payload["requires_user_confirmation"])
        self.assertEqual(payload["review_pdf"], str(Path("日期确认") / "dated-returned-pages.pdf"))
        self.assertEqual(payload["items"][0]["returned_page"], 2)
        self.assertEqual(payload["review_pages_requiring_attention"], [2])
        self.assertIn("第 2 页", payload["confirmation_message"])
        self.assertIn("不要增删页面", payload["confirmation_message"])
        self.assertIn("不要改变页面顺序", payload["confirmation_message"])
        self.assertEqual(calls[0][3]["signing_date"], date(2026, 7, 15))

    def test_finalize_only_runs_with_explicit_confirmation(self):
        calls = []

        def runner(batch_root, review_root, output_root, **options):
            calls.append((Path(batch_root), Path(review_root), Path(output_root), options))
            return SimpleNamespace(
                items=(
                    SimpleNamespace(
                        working_paper_id="甲.docx",
                        status="completed",
                        returned_page=2,
                        score=100,
                        output_path=Path(output_root) / "completed-pdfs" / "甲.docx.pdf",
                        reason=None,
                        date_status="filled",
                        date_reason=None,
                    ),
                ),
                succeeded=1,
                unused_pages=(),
                output_root=Path(output_root),
                report_path=Path(output_root) / "processing-report.html",
                ocr_failures=(),
            )

        output = StringIO()
        code = main(
            ["finalize", "处理批次", "日期确认", "回拼结果", "--confirmed"],
            preflight_runner=lambda **options: ready_preflight(),
            finalize_runner=runner,
            output=output,
        )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["command"], "finalize")
        self.assertEqual(payload["status"], "completed")
        self.assertTrue(calls[0][3]["confirmed"])


if __name__ == "__main__":
    unittest.main()

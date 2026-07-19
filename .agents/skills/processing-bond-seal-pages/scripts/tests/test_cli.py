from datetime import date
from io import BytesIO, StringIO, TextIOWrapper
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.cli import main  # noqa: E402
from bond_seal_pages.preflight import (  # noqa: E402
    PreflightCheck,
    PreflightResult,
    PythonCandidate,
    PythonRuntimeInfo,
)


def ready_preflight():
    runtime = PythonRuntimeInfo(sys.executable, (3, 12, 0), "64bit")
    selected = PythonCandidate(
        (sys.executable,),
        "current-environment",
        1,
        runtime=runtime,
    )
    return PreflightResult("win32", (selected,), selected, (), ())


class CliTests(unittest.TestCase):
    def test_prepare_runs_preflight_then_reports_partial_batch(self):
        preflight = ready_preflight()
        calls = []

        def preflight_runner(**options):
            calls.append(("preflight", options))
            return preflight

        def prepare_runner(working_papers, batch_root, **options):
            calls.append(
                (
                    "prepare",
                    Path(working_papers),
                    Path(batch_root),
                    options,
                )
            )
            return SimpleNamespace(
                succeeded=3,
                failed=1,
                seal_pages_path=Path(batch_root) / "seal-pages.pdf",
                manifest_path=Path(batch_root) / "manifest.json",
            )

        output = StringIO()
        code = main(
            [
                "prepare",
                "底稿文件",
                "处理批次",
                "--duplicate-policy",
                "keep",
            ],
            preflight_runner=preflight_runner,
            prepare_runner=prepare_runner,
            output=output,
        )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["succeeded"], 3)
        self.assertEqual(payload["failed"], 1)
        self.assertEqual(calls[0][1]["require_converter"], True)
        self.assertEqual(calls[1][1:3], (Path("底稿文件"), Path("处理批次")))
        self.assertIs(calls[1][3]["preflight_result"], preflight)
        self.assertEqual(calls[1][3]["duplicate_policy"], "keep")

    def test_prepare_requires_an_explicit_duplicate_policy(self):
        with patch("sys.stderr", new=StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main(
                    ["prepare", "底稿文件", "处理批次"],
                    preflight_runner=lambda **options: ready_preflight(),
                    prepare_runner=lambda *args, **kwargs: self.fail(
                        "未选择去重策略时不应启动业务处理"
                    ),
                    output=StringIO(),
                )

        self.assertEqual(raised.exception.code, 2)

    def test_complete_skips_converter_check_and_reports_all_outcomes(self):
        preflight = ready_preflight()
        calls = []

        def preflight_runner(**options):
            calls.append(("preflight", options))
            return preflight

        def complete_runner(batch_root, returned_pdf, output_root, **options):
            calls.append(
                (
                    "complete",
                    Path(batch_root),
                    Path(returned_pdf),
                    Path(output_root),
                    options,
                )
            )
            return SimpleNamespace(
                items=(
                    SimpleNamespace(
                        working_paper_id="paper-1",
                        status="completed",
                        returned_page=3,
                        score=100,
                        output_path=Path(output_root) / "completed-pdfs" / "a.pdf",
                        reason=None,
                        date_status="completed",
                        date_reason=None,
                    ),
                    SimpleNamespace(
                        working_paper_id="paper-2",
                        status="low_confidence",
                        returned_page=4,
                        score=85,
                        output_path=None,
                        reason="最佳候选未达到 90 分自动回拼阈值",
                        date_status="not_applied",
                        date_reason=None,
                    ),
                ),
                succeeded=1,
                unused_pages=(4,),
                output_root=Path(output_root),
                report_path=Path(output_root) / "processing-report.html",
                ocr_failures=(),
            )

        output = StringIO()
        code = main(
            [
                "complete",
                "处理批次",
                "回章页合集.pdf",
                "回拼结果",
                "--signing-date",
                "2026-07-15",
            ],
            preflight_runner=preflight_runner,
            complete_runner=complete_runner,
            output=output,
        )

        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["outcomes"], {"completed": 1, "low_confidence": 1})
        self.assertEqual(payload["unused_pages"], [4])
        self.assertEqual(len(payload["items"]), payload["total"])
        self.assertEqual(payload["items"][0]["working_paper_id"], "paper-1")
        self.assertEqual(
            payload["items"][0]["output_path"],
            str(Path("回拼结果") / "completed-pdfs" / "a.pdf"),
        )
        self.assertEqual(payload["items"][1]["score"], 85)
        self.assertEqual(calls[0][1]["require_converter"], False)
        self.assertEqual(calls[1][4]["signing_date"], date(2026, 7, 15))

    def test_default_standard_output_is_always_utf8(self):
        raw_output = BytesIO()
        system_output = TextIOWrapper(raw_output, encoding="gbk")

        with patch.object(sys, "stdout", system_output):
            code = main(
                ["preflight", "--stage", "complete"],
                preflight_runner=lambda **options: ready_preflight(),
            )
            system_output.flush()

        payload = json.loads(raw_output.getvalue().decode("utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(system_output.encoding.lower().replace("-", ""), "utf8")

    def test_failed_preflight_never_calls_business_entry(self):
        preflight = PreflightResult(
            "win32",
            (),
            None,
            (),
            (
                PreflightCheck(
                    "python-discovery",
                    "Python",
                    False,
                    "没有发现可用 Python",
                ),
            ),
        )

        with tempfile.TemporaryDirectory() as directory:
            batch_root = Path(directory) / "处理批次"
            output = StringIO()
            code = main(
                [
                    "prepare",
                    "底稿文件",
                    str(batch_root),
                    "--duplicate-policy",
                    "keep",
                ],
                preflight_runner=lambda **options: preflight,
                prepare_runner=lambda *args, **kwargs: self.fail("不应启动业务处理"),
                output=output,
            )

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(payload["status"], "preflight_failed")
            self.assertIn("没有发现可用 Python", payload["report"])
            self.assertFalse(batch_root.exists())


if __name__ == "__main__":
    unittest.main()

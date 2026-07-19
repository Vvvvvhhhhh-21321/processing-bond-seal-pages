import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.preflight import (  # noqa: E402
    PythonRuntimeInfo,
    build_installation_plan,
    format_preflight_report,
    run_preflight,
)
from bond_seal_pages.processing_batch import prepare_processing_batch  # noqa: E402


REQUIRED_PACKAGES = {
    "rapidocr": True,
    "onnxruntime": True,
    "numpy": True,
    "cv2": True,
    "pypdf": True,
    "pdfplumber": True,
    "pypdfium2": True,
    "reportlab": True,
    "pymupdf": True,
    "win32com.client": True,
}


def runtime(executable, version=(3, 12, 2), architecture="64bit"):
    return PythonRuntimeInfo(str(executable), version, architecture)


class PreflightTests(unittest.TestCase):
    def test_active_conda_wins_over_current_and_system_python(self):
        env = {"CONDA_PREFIX": "C:/envs/bond"}
        current = "C:/Python312/python.exe"
        paths = {
            "py": "C:/Windows/py.exe",
            "python": "C:/Python311/python.exe",
            "python3": None,
        }
        probed = {
            os.path.normcase("C:/envs/bond/python.exe"): runtime(
                "C:/envs/bond/python.exe"
            ),
            os.path.normcase(current): runtime(current),
            os.path.normcase("C:/Windows/py.exe -3"): runtime(
                "C:/Python310/python.exe", (3, 10, 12)
            ),
            os.path.normcase("C:/Python311/python.exe"): runtime(
                "C:/Python311/python.exe", (3, 11, 9)
            ),
        }

        result = run_preflight(
            platform_name="win32",
            env=env,
            current_executable=current,
            which=lambda name: paths.get(name),
            python_probe=lambda command: probed[os.path.normcase(" ".join(command))],
            package_probe=lambda candidate: REQUIRED_PACKAGES,
            model_cache_ready=lambda: True,
            windows_word_locator=lambda: Path("C:/Program Files/Word/WINWORD.EXE"),
        )

        self.assertFalse(result.ready)
        self.assertIn("请使用 C:/envs/bond/python.exe", format_preflight_report(result))
        self.assertEqual(result.selected.source, "active-conda")
        self.assertEqual(
            result.selected.runtime.executable,
            "C:/envs/bond/python.exe",
        )
        self.assertGreaterEqual(len(result.candidates), 3)

    def test_current_environment_wins_when_conda_is_not_active(self):
        current = "/opt/custom/python"

        result = run_preflight(
            platform_name="darwin",
            env={},
            current_executable=current,
            which=lambda name: {"python3": "/usr/local/bin/python3"}.get(name),
            python_probe=lambda command: runtime(command[0]),
            package_probe=lambda candidate: {
                key: value
                for key, value in REQUIRED_PACKAGES.items()
                if key != "win32com.client"
            },
            model_cache_ready=lambda: True,
            mac_libreoffice_locator=lambda: Path("/Applications/LibreOffice/soffice"),
        )

        self.assertTrue(result.ready)
        self.assertEqual(result.selected.source, "current-environment")
        self.assertEqual(result.selected.command, (current,))

    def test_equal_priority_system_candidates_are_reported_as_ambiguous(self):
        result = run_preflight(
            platform_name="darwin",
            env={},
            current_executable=None,
            which=lambda name: {
                "python": "/one/python",
                "python3": "/two/python3",
                "libreoffice": "/usr/local/bin/libreoffice",
            }.get(name),
            python_probe=lambda command: runtime(command[0]),
            package_probe=lambda candidate: {},
            model_cache_ready=lambda: True,
            mac_libreoffice_locator=lambda: Path("/usr/local/bin/libreoffice"),
        )

        self.assertFalse(result.ready)
        self.assertIsNone(result.selected)
        self.assertEqual(len(result.ambiguous_candidates), 2)
        report = format_preflight_report(result)
        self.assertIn("需要选择", report)
        self.assertIn("/one/python", report)
        self.assertIn("/two/python3", report)

        resolved = run_preflight(
            platform_name="darwin",
            env={},
            current_executable=None,
            selected_python="/two/python3",
            which=lambda name: {
                "python": "/one/python",
                "python3": "/two/python3",
            }.get(name),
            python_probe=lambda command: runtime(command[0]),
            package_probe=lambda candidate: {
                key: value
                for key, value in REQUIRED_PACKAGES.items()
                if key != "win32com.client"
            },
            model_cache_ready=lambda: True,
            mac_libreoffice_locator=lambda: Path("/usr/local/bin/libreoffice"),
        )

        self.assertTrue(resolved.ready)
        self.assertEqual(resolved.selected.runtime.executable, "/two/python3")

    def test_missing_python_has_consent_gated_system_install_help(self):
        result = run_preflight(
            platform_name="win32",
            env={},
            current_executable=None,
            which=lambda name: None,
            python_probe=lambda command: (_ for _ in ()).throw(AssertionError()),
            package_probe=lambda candidate: {},
            model_cache_ready=lambda: False,
            windows_word_locator=lambda: None,
        )

        self.assertFalse(result.ready)
        self.assertIn("没有发现可用 Python", format_preflight_report(result))
        plan = build_installation_plan(
            result,
            approved=True,
            which=lambda name: "C:/Windows/winget.exe"
            if name == "winget"
            else None,
        )

        self.assertIsNone(plan.target)
        self.assertEqual(
            plan.commands,
            (
                (
                    "C:/Windows/winget.exe",
                    "install",
                    "--exact",
                    "--id",
                    "Python.Python.3.12",
                ),
            ),
        )
        self.assertIn("重新运行前置检查", plan.manual_steps[0])

    def test_install_plan_requires_consent_and_targets_only_selected_python(self):
        current = "C:/Python/python.exe"
        packages = dict(REQUIRED_PACKAGES)
        packages["rapidocr"] = False
        packages["onnxruntime"] = False
        packages["numpy"] = False
        packages["cv2"] = False

        result = run_preflight(
            platform_name="win32",
            env={},
            current_executable=current,
            which=lambda name: None,
            python_probe=lambda command: runtime(current),
            package_probe=lambda candidate: packages,
            model_cache_ready=lambda: False,
            windows_word_locator=lambda: Path("C:/Word/WINWORD.EXE"),
        )

        self.assertFalse(result.ready)
        report = format_preflight_report(result)
        self.assertIn("rapidocr", report)
        self.assertIn("onnxruntime", report)
        self.assertIn("numpy", report)
        self.assertIn("cv2", report)
        self.assertIn("首次模型准备需要联网", report)
        self.assertIn("未执行任何安装", report)
        with self.assertRaisesRegex(PermissionError, "用户同意"):
            build_installation_plan(result, approved=False)

        plan = build_installation_plan(result, approved=True)

        self.assertEqual(plan.target.command, (current,))
        self.assertEqual(len(plan.commands), 2)
        for command in plan.commands:
            self.assertEqual(command[:1], (current,))
        joined = " ".join(plan.commands[0])
        self.assertIn("rapidocr>=3.9.0", joined)
        self.assertIn("onnxruntime", joined)
        self.assertIn("numpy", joined)
        self.assertIn("opencv-python-headless", joined)
        model_setup = plan.commands[1][-1]
        path_setup = model_setup.split("from bond_seal_pages", 1)[0]
        path_setup += "print(sys.path[0])"
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, "-c", path_setup],
                cwd=directory,
                check=True,
                capture_output=True,
                text=True,
            )
        self.assertEqual(
            completed.stdout.strip(),
            str(Path(__file__).resolve().parents[1]),
        )

    def test_failed_preflight_creates_no_batch_and_starts_no_converter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working_papers = root / "底稿文件"
            working_papers.mkdir()
            batch_root = root / "处理批次"
            result = run_preflight(
                platform_name="darwin",
                env={},
                current_executable=None,
                which=lambda name: None,
                python_probe=lambda command: (_ for _ in ()).throw(AssertionError()),
                package_probe=lambda candidate: {},
                model_cache_ready=lambda: False,
                mac_libreoffice_locator=lambda: None,
            )

            with self.assertRaisesRegex(RuntimeError, "前置检查未通过"):
                prepare_processing_batch(
                    working_papers,
                    batch_root,
                    preflight_result=result,
                )

            self.assertFalse(batch_root.exists())

    def test_completion_preflight_does_not_require_word_converter_or_pywin32(self):
        current = "C:/Python/python.exe"
        result = run_preflight(
            platform_name="win32",
            env={},
            current_executable=current,
            require_converter=False,
            which=lambda name: None,
            python_probe=lambda command: runtime(current),
            package_probe=lambda candidate: REQUIRED_PACKAGES,
            model_cache_ready=lambda: True,
            windows_word_locator=lambda: (_ for _ in ()).throw(
                AssertionError("第二阶段不应检查 Word")
            ),
        )

        self.assertTrue(result.ready)
        self.assertNotIn(
            "dependency:win32com.client",
            {check.key for check in result.checks},
        )

    def test_unsupported_platform_is_not_ready(self):
        result = run_preflight(
            platform_name="linux",
            env={},
            current_executable="/usr/bin/python3",
            which=lambda name: None,
            python_probe=lambda command: runtime(command[0]),
            package_probe=lambda candidate: REQUIRED_PACKAGES,
            model_cache_ready=lambda: True,
        )

        self.assertFalse(result.ready)
        self.assertIn("仅支持 Windows 和 macOS", format_preflight_report(result))


if __name__ == "__main__":
    unittest.main()

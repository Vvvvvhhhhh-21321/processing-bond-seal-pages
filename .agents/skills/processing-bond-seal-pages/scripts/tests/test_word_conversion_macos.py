import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.word_conversion import (  # noqa: E402
    MacOSLibreOfficePdfConverter,
    create_platform_word_pdf_converter,
    discover_macos_libreoffice,
)


class LibreOfficeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, command, **options):
        self.calls.append((tuple(command), options))
        output_dir = Path(command[command.index("--outdir") + 1])
        input_path = Path(command[-1])
        (output_dir / f"{input_path.stem}.pdf").write_bytes(b"converted-pdf")
        return subprocess.CompletedProcess(command, 0, "convert ok", "")


class MacOSWordConversionTests(unittest.TestCase):
    def test_discovers_path_command_before_spotlight_application(self):
        runner_calls = []

        def runner(command, **options):
            runner_calls.append(command)
            return subprocess.CompletedProcess(command, 0, "/Applications/LibreOffice.app\n", "")

        result = discover_macos_libreoffice(
            which=lambda name: "/usr/local/bin/soffice" if name == "soffice" else None,
            runner=runner,
        )

        self.assertEqual(result, Path("/usr/local/bin/soffice"))
        self.assertEqual(runner_calls, [])

    def test_discovers_libreoffice_application_with_spotlight(self):
        def runner(command, **options):
            return subprocess.CompletedProcess(
                command,
                0,
                "/Applications/LibreOffice.app\n",
                "",
            )

        result = discover_macos_libreoffice(
            which=lambda name: "/usr/bin/mdfind" if name == "mdfind" else None,
            runner=runner,
            is_file=lambda path: str(path)
            .replace("\\", "/")
            .endswith("Contents/MacOS/soffice"),
        )

        self.assertEqual(
            result,
            Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
        )

    def test_headless_converter_writes_exact_requested_pdf_and_is_reusable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_input = root / "中文底稿.docx"
            second_input = root / "历史底稿.doc"
            first_input.write_bytes(b"docx")
            second_input.write_bytes(b"doc")
            first_output = root / "输出/第一份.pdf"
            second_output = root / "输出/第二份.pdf"
            first_output.parent.mkdir()
            runner = LibreOfficeRunner()
            converter = MacOSLibreOfficePdfConverter(
                executable="/Applications/LibreOffice.app/Contents/MacOS/soffice",
                runner=runner,
            )

            converter.convert(first_input, first_output)
            converter.convert(second_input, second_output)
            converter.close()

            self.assertEqual(first_output.read_bytes(), b"converted-pdf")
            self.assertEqual(second_output.read_bytes(), b"converted-pdf")
            self.assertEqual(len(runner.calls), 2)
            for command, options in runner.calls:
                self.assertIn("--headless", command)
                self.assertIn("--convert-to", command)
                self.assertIn("pdf:writer_pdf_Export", command)
                self.assertTrue(any(part.startswith("-env:UserInstallation=") for part in command))
                self.assertTrue(options["check"])

    def test_missing_libreoffice_has_chinese_actionable_error(self):
        with self.assertRaisesRegex(RuntimeError, "LibreOffice"):
            MacOSLibreOfficePdfConverter(
                locator=lambda: None,
                runner=lambda *args, **kwargs: None,
            )

    def test_platform_factory_rejects_linux(self):
        with self.assertRaisesRegex(RuntimeError, "仅支持 Windows 和 macOS"):
            create_platform_word_pdf_converter(platform_name="linux")


if __name__ == "__main__":
    unittest.main()

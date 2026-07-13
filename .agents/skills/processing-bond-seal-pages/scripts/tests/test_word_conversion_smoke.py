import os
import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.word_conversion import WindowsWordPdfConverter  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "仅在 Windows 上运行真实 Word 冒烟测试")
class WindowsWordSmokeTests(unittest.TestCase):
    def test_converts_a_real_working_paper_with_microsoft_word(self):
        configured_path = os.environ.get("BOND_SEAL_PAGES_WORD_SMOKE_WORKING_PAPER")
        if not configured_path:
            self.skipTest(
                "请设置 BOND_SEAL_PAGES_WORD_SMOKE_WORKING_PAPER 指向真实 .doc 或 .docx"
            )
        working_paper_path = Path(configured_path)
        if working_paper_path.suffix.lower() not in {".doc", ".docx"}:
            self.fail("真实 Word 冒烟样本必须是 .doc 或 .docx")

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "word-smoke.pdf"
            converter = WindowsWordPdfConverter()
            try:
                converter.convert(working_paper_path, output_path)
            finally:
                converter.close()

            self.assertGreater(len(PdfReader(str(output_path)).pages), 0)


if __name__ == "__main__":
    unittest.main()

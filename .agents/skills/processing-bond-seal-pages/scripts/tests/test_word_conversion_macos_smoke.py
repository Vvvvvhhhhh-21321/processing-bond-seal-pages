import os
import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.word_conversion import (  # noqa: E402
    MacOSLibreOfficePdfConverter,
)


@unittest.skipUnless(sys.platform == "darwin", "仅在 macOS 上运行真实 LibreOffice 冒烟测试")
class MacOSLibreOfficeSmokeTests(unittest.TestCase):
    def test_converts_a_real_working_paper_with_libreoffice(self):
        configured_path = os.environ.get(
            "BOND_SEAL_PAGES_LIBREOFFICE_SMOKE_WORKING_PAPER"
        )
        if not configured_path:
            self.skipTest(
                "请设置 BOND_SEAL_PAGES_LIBREOFFICE_SMOKE_WORKING_PAPER "
                "指向真实 .doc 或 .docx"
            )
        working_paper_path = Path(configured_path)
        if working_paper_path.suffix.lower() not in {".doc", ".docx"}:
            self.fail("真实 LibreOffice 冒烟样本必须是 .doc 或 .docx")

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "libreoffice-smoke.pdf"
            converter = MacOSLibreOfficePdfConverter()
            try:
                converter.convert(working_paper_path, output_path)
            finally:
                converter.close()

            self.assertGreater(len(PdfReader(str(output_path)).pages), 0)


if __name__ == "__main__":
    unittest.main()

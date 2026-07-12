import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.pdf_ops import replace_last_page  # noqa: E402


def _write_one_page_pdf(path):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    with Path(path).open("wb") as output:
        writer.write(output)


class PdfReplacementErrorTests(unittest.TestCase):
    def test_unreadable_target_reports_chinese_error_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "损坏底稿.pdf"
            returned = root / "回章页.pdf"
            output = root / "不应生成.pdf"
            target.write_bytes(b"not a pdf")
            _write_one_page_pdf(returned)

            with self.assertRaisesRegex(ValueError, "无法读取目标 PDF"):
                replace_last_page(target, returned, output)

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

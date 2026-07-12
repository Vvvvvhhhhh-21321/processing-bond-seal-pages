import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.pdf_ops import replace_last_page  # noqa: E402


def _write_pdf(path, page_count):
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=300)
    with Path(path).open("wb") as output:
        writer.write(output)


class PdfReplacementValidationTests(unittest.TestCase):
    def test_empty_target_reports_chinese_error_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "空底稿.pdf"
            returned = root / "回章页.pdf"
            output = root / "不应生成.pdf"
            _write_pdf(target, 0)
            _write_pdf(returned, 1)

            with self.assertRaisesRegex(ValueError, "目标 PDF 没有可替换的页面"):
                replace_last_page(target, returned, output)

            self.assertFalse(output.exists())

    def test_unreadable_returned_pdf_reports_chinese_error_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "底稿.pdf"
            returned = root / "损坏回章页.pdf"
            output = root / "不应生成.pdf"
            _write_pdf(target, 1)
            returned.write_bytes(b"not a pdf")

            with self.assertRaisesRegex(ValueError, "无法读取回章页 PDF"):
                replace_last_page(target, returned, output)

            self.assertFalse(output.exists())

    def test_invalid_returned_page_reports_chinese_error_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "底稿.pdf"
            returned = root / "回章页.pdf"
            output = root / "不应生成.pdf"
            _write_pdf(target, 1)
            _write_pdf(returned, 1)

            with self.assertRaisesRegex(IndexError, "PDF 页码超出范围：2"):
                replace_last_page(target, returned, output, returned_page_index=2)

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

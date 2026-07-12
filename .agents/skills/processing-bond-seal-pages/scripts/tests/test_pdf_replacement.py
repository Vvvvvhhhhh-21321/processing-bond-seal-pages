import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, TextStringObject


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.pdf_ops import replace_last_page  # noqa: E402


def _write_pdf(path, pages=(), password=None):
    writer = PdfWriter()
    for width, height, label in pages:
        page = writer.add_blank_page(width=width, height=height)
        page[NameObject("/PageLabel")] = TextStringObject(label)
    if password is not None:
        writer.encrypt(password)
    with Path(path).open("wb") as output:
        writer.write(output)


class PdfReplacementTests(unittest.TestCase):
    def test_preserves_prefix_and_returned_page_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "底稿.pdf"
            returned = root / "回章页.pdf"
            output = root / "盖章版.pdf"
            _write_pdf(target, [(200, 300, "正文"), (600, 700, "原末页")])

            writer = PdfWriter()
            returned_page = writer.add_blank_page(width=333, height=444)
            returned_page.cropbox.lower_left = (11, 22)
            returned_page.cropbox.upper_right = (300, 400)
            returned_page.rotate(90)
            returned_page[NameObject("/PageLabel")] = TextStringObject("客户回章页")
            with returned.open("wb") as stream:
                writer.write(stream)

            replace_last_page(target, returned, output)

            result = PdfReader(output)
            self.assertEqual(len(result.pages), 2)
            self.assertEqual(result.pages[0]["/PageLabel"], "正文")
            self.assertEqual(
                (float(result.pages[0].mediabox.width), float(result.pages[0].mediabox.height)),
                (200.0, 300.0),
            )
            replacement = result.pages[1]
            self.assertEqual(replacement["/PageLabel"], "客户回章页")
            self.assertEqual(
                (float(replacement.mediabox.width), float(replacement.mediabox.height)),
                (333.0, 444.0),
            )
            self.assertEqual(tuple(float(value) for value in replacement.cropbox), (11.0, 22.0, 300.0, 400.0))
            self.assertEqual(replacement.rotation, 90)

    def test_unreadable_target_reports_chinese_error_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "损坏底稿.pdf"
            returned = root / "回章页.pdf"
            output = root / "不应生成.pdf"
            target.write_bytes(b"not a pdf")
            _write_pdf(returned, [(200, 300, "回章页")])

            with self.assertRaisesRegex(ValueError, "无法读取目标 PDF"):
                replace_last_page(target, returned, output)

            self.assertFalse(output.exists())

    def test_encrypted_target_reports_chinese_error_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "加密底稿.pdf"
            returned = root / "回章页.pdf"
            output = root / "不应生成.pdf"
            _write_pdf(target, [(200, 300, "加密页")], password="secret")
            _write_pdf(returned, [(200, 300, "回章页")])

            with self.assertRaisesRegex(ValueError, "无法读取目标 PDF"):
                replace_last_page(target, returned, output)

            self.assertFalse(output.exists())

    def test_empty_target_reports_chinese_error_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "空底稿.pdf"
            returned = root / "回章页.pdf"
            output = root / "不应生成.pdf"
            _write_pdf(target)
            _write_pdf(returned, [(200, 300, "回章页")])

            with self.assertRaisesRegex(ValueError, "目标 PDF 没有可替换的页面"):
                replace_last_page(target, returned, output)

            self.assertFalse(output.exists())

    def test_unreadable_returned_pdf_reports_chinese_error_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "底稿.pdf"
            returned = root / "损坏回章页.pdf"
            output = root / "不应生成.pdf"
            _write_pdf(target, [(200, 300, "底稿")])
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
            _write_pdf(target, [(200, 300, "底稿")])
            _write_pdf(returned, [(200, 300, "回章页")])

            with self.assertRaisesRegex(IndexError, "PDF 页码超出范围：2"):
                replace_last_page(target, returned, output, returned_page_index=2)

            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

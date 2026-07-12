import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, TextStringObject


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.pdf_ops import replace_last_page  # noqa: E402


def _write_pdf(path, pages):
    writer = PdfWriter()
    for width, height, label in pages:
        page = writer.add_blank_page(width=width, height=height)
        page[NameObject("/PageLabel")] = TextStringObject(label)
    with Path(path).open("wb") as output:
        writer.write(output)


class PdfReplacementContractTests(unittest.TestCase):
    def test_replacement_preserves_prefix_and_returned_page_geometry(self):
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


if __name__ == "__main__":
    unittest.main()

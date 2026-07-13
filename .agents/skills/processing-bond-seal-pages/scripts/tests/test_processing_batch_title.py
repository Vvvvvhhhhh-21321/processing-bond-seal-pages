import json
import sys
import tempfile
import unittest
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.processing_batch import prepare_processing_batch  # noqa: E402


class UnbracketedTitleConverter:
    def convert(self, working_paper_path, output_path):
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        canvas = Canvas(str(output_path), pagesize=(500, 700))
        canvas.setFont("STSong-Light", 14)
        canvas.drawString(50, 650, "短行")
        canvas.drawString(50, 620, "这是没有书名号的主要标题")
        canvas.drawString(50, 590, "2026年7月13日")
        canvas.save()


class ProcessingBatchTitleTests(unittest.TestCase):
    def test_uses_the_main_page_title_when_book_title_marks_are_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working_paper_root = root / "底稿"
            working_paper_root.mkdir()
            (working_paper_root / "文件名不同.docx").write_bytes(b"word")

            result = prepare_processing_batch(
                working_paper_root,
                root / "处理批次",
                converter=UnbracketedTitleConverter(),
            )

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["items"][0]["title"], "这是没有书名号的主要标题")


if __name__ == "__main__":
    unittest.main()

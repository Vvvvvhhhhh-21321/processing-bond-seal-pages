import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.processing_batch import prepare_processing_batch  # noqa: E402


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_two_page_pdf(path, title):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    canvas = Canvas(str(path), pagesize=(500, 700))
    canvas.setFont("STSong-Light", 14)
    canvas.drawString(50, 650, "正文")
    canvas.showPage()
    canvas.setFont("STSong-Light", 14)
    canvas.drawString(50, 650, f"《{title}》")
    canvas.save()


class FakeWordConverter:
    def __init__(self, failed_name):
        self.failed_name = failed_name
        self.calls = []

    def convert(self, source_path, output_path):
        source_path = Path(source_path)
        output_path = Path(output_path)
        self.calls.append(source_path.name)
        if source_path.name == self.failed_name:
            raise RuntimeError("模拟转换失败")
        _write_two_page_pdf(output_path, "共同标题")


class ProcessingBatchTests(unittest.TestCase):
    def test_prepares_batch_without_deduplicating_and_continues_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "底稿"
            batch = root / "处理批次"
            source.mkdir()
            files = {
                "01-甲.docx": b"first word file",
                "02-乙.doc": b"second word file",
                "03-失败.docx": b"broken word file",
            }
            for name, content in files.items():
                (source / name).write_bytes(content)
            (source / "忽略.txt").write_text("不是 Word", encoding="utf-8")
            source_hashes = {name: _sha256(source / name) for name in files}
            converter = FakeWordConverter("03-失败.docx")

            result = prepare_processing_batch(source, batch, converter=converter)

            self.assertEqual(result.succeeded, 2)
            self.assertEqual(result.failed, 1)
            self.assertEqual(converter.calls, ["01-甲.docx", "02-乙.doc", "03-失败.docx"])
            self.assertEqual(PdfReader(result.seal_pages_path).get_num_pages(), 2)
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], 1)
            self.assertEqual(len(manifest["items"]), 3)

            ready = [item for item in manifest["items"] if item["status"] == "ready"]
            self.assertEqual([item["source_path"] for item in ready], ["01-甲.docx", "02-乙.doc"])
            self.assertEqual([item["seal_page"] for item in ready], [1, 2])
            self.assertEqual([item["title"] for item in ready], ["共同标题", "共同标题"])
            self.assertTrue(all(item["pdf_page_count"] == 2 for item in ready))
            self.assertTrue(all(len(item["source_sha256"]) == 64 for item in ready))
            self.assertTrue(all(len(item["pdf_sha256"]) == 64 for item in ready))
            self.assertTrue((batch / ready[0]["converted_pdf"]).exists())
            self.assertTrue((batch / ready[1]["converted_pdf"]).exists())

            failed = [item for item in manifest["items"] if item["status"] == "failed"]
            self.assertEqual(failed[0]["source_path"], "03-失败.docx")
            self.assertIn("模拟转换失败", failed[0]["error"])
            self.assertEqual({_sha256(source / name) for name in files}, set(source_hashes.values()))


if __name__ == "__main__":
    unittest.main()

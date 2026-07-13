import json
import sys
import tempfile
import unittest
from pathlib import Path

from reportlab.pdfgen.canvas import Canvas


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.completion import complete_processing_batch  # noqa: E402
from bond_seal_pages.pdf_ops import sha256_file  # noqa: E402


def _write_pages(path, texts):
    canvas = Canvas(str(path), pagesize=(400, 600))
    for text in texts:
        canvas.drawString(50, 550, text)
        canvas.showPage()
    canvas.save()


class CompletionAmbiguityTests(unittest.TestCase):
    def test_skips_a_returned_page_tied_between_distinct_titles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = root / "处理批次"
            items = []
            for name, title in (
                ("甲.docx", "abcdefghijA"),
                ("乙.docx", "abcdefghijB"),
            ):
                converted_pdf = batch_root / "pdfs" / f"{name}.pdf"
                converted_pdf.parent.mkdir(parents=True, exist_ok=True)
                _write_pages(converted_pdf, [f"body-{name}", title])
                items.append(
                    {
                        "working_paper_id": name,
                        "working_paper_path": name,
                        "status": "ready",
                        "title": title,
                        "normalized_title": title,
                        "converted_pdf": converted_pdf.relative_to(batch_root).as_posix(),
                        "pdf_page_count": 2,
                        "pdf_sha256": sha256_file(converted_pdf),
                        "seal_page": len(items) + 1,
                    }
                )
            (batch_root / "manifest.json").write_text(
                json.dumps({"version": 1, "items": items}),
                encoding="utf-8",
            )
            returned_pdf = root / "returned.pdf"
            _write_pages(returned_pdf, ["abcdefghijC"])

            result = complete_processing_batch(batch_root, returned_pdf, root / "回拼结果")

            self.assertEqual([item.status for item in result.items], ["ambiguous", "ambiguous"])
            self.assertTrue(all(item.returned_page == 1 for item in result.items))
            self.assertTrue(all(item.score == 91 for item in result.items))
            self.assertTrue(all(item.output_path is None for item in result.items))
            self.assertEqual(result.unused_pages, (1,))


if __name__ == "__main__":
    unittest.main()

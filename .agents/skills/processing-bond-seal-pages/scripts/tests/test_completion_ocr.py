import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.completion import complete_processing_batch  # noqa: E402
from completion_test_support import (  # noqa: E402
    create_processing_batch,
    make_fake_pymupdf_module,
    make_fake_rapidocr_module,
    write_pdf_pages,
    write_scanned_pdf_pages,
)


class StubOCREngine:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def recognize_page(self, pdf_path, page_number):
        self.calls.append((Path(pdf_path), page_number))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class CompletionOCRTests(unittest.TestCase):
    def test_text_title_does_not_call_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(root, [("文字型.docx", "文字型确认函")])
            returned_pdf = root / "returned.pdf"
            write_pdf_pages(returned_pdf, [("《文字型确认函》", (400, 600))])
            ocr_engine = StubOCREngine(AssertionError("不应调用 OCR"))

            result = complete_processing_batch(
                batch_root,
                returned_pdf,
                root / "回拼结果",
                ocr_engine=ocr_engine,
            )

            self.assertEqual(result.items[0].status, "completed")
            self.assertEqual(ocr_engine.calls, [])

    def test_scanned_title_uses_ocr_and_preserves_returned_pdf_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(root, [("扫描型.docx", "扫描型确认函")])
            returned_pdf = root / "returned.pdf"
            write_scanned_pdf_pages(returned_pdf, [("white", None)])
            ocr_engine = StubOCREngine(("《扫描型确认函》",))

            result = complete_processing_batch(
                batch_root,
                returned_pdf,
                root / "回拼结果",
                ocr_engine=ocr_engine,
            )

            item = result.items[0]
            self.assertEqual(item.status, "completed")
            self.assertEqual(item.returned_page, 1)
            self.assertEqual(len(ocr_engine.calls), 1)
            returned_image = PdfReader(str(returned_pdf)).pages[0].images[0].data
            output_image = PdfReader(str(item.output_path)).pages[-1].images[0].data
            self.assertEqual(output_image, returned_image)

    def test_insufficient_text_layer_falls_back_to_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(root, [("扫描型.docx", "扫描型确认函")])
            returned_pdf = root / "returned.pdf"
            write_scanned_pdf_pages(returned_pdf, [("white", "1")])
            ocr_engine = StubOCREngine(("《扫描型确认函》",))

            result = complete_processing_batch(
                batch_root,
                returned_pdf,
                root / "回拼结果",
                ocr_engine=ocr_engine,
            )

            self.assertEqual(result.items[0].status, "completed")
            self.assertEqual(len(ocr_engine.calls), 1)

    def test_unrelated_residual_text_layer_falls_back_to_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(root, [("扫描型.docx", "扫描型确认函")])
            returned_pdf = root / "returned.pdf"
            write_scanned_pdf_pages(returned_pdf, [("white", "扫描全能王")])
            ocr_engine = StubOCREngine(("《扫描型确认函》",))

            result = complete_processing_batch(
                batch_root,
                returned_pdf,
                root / "回拼结果",
                ocr_engine=ocr_engine,
            )

            self.assertEqual(result.items[0].status, "completed")
            self.assertEqual(len(ocr_engine.calls), 1)

    def test_scanned_page_automatically_uses_rapidocr_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(root, [("扫描型.docx", "扫描型确认函")])
            returned_pdf = root / "returned.pdf"
            write_scanned_pdf_pages(returned_pdf, [("white", None)])

            rapidocr_module = make_fake_rapidocr_module(("《扫描型确认函》",))
            pymupdf_module = make_fake_pymupdf_module()

            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": str(root / "cache")},
            ), patch.dict(
                sys.modules,
                {
                    "onnxruntime": object(),
                    "pymupdf": pymupdf_module,
                    "rapidocr": rapidocr_module,
                },
            ):
                result = complete_processing_batch(
                    batch_root,
                    returned_pdf,
                    root / "回拼结果",
                )

            self.assertEqual(result.items[0].status, "completed")

    def test_mixed_text_and_scanned_pages_only_ocr_the_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(
                root,
                [("文字型.docx", "文字型确认函"), ("扫描型.docx", "扫描型确认函")],
            )
            returned_pdf = root / "returned.pdf"
            write_scanned_pdf_pages(
                returned_pdf,
                [
                    ("white", "《文字型确认函》"),
                    ("gray", None),
                ],
            )
            ocr_engine = StubOCREngine(("《扫描型确认函》",))

            result = complete_processing_batch(
                batch_root,
                returned_pdf,
                root / "回拼结果",
                ocr_engine=ocr_engine,
            )

            self.assertEqual(
                [item.status for item in result.items],
                ["completed", "completed"],
            )
            self.assertEqual(
                [item.returned_page for item in result.items],
                [1, 2],
            )
            self.assertEqual(len(ocr_engine.calls), 1)

    def test_ocr_single_character_error_can_still_reach_auto_match_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_title = "关于申请开立履约保函的确认函"
            batch_root = create_processing_batch(root, [("长标题.docx", source_title)])
            returned_pdf = root / "returned.pdf"
            write_scanned_pdf_pages(returned_pdf, [("white", None)])
            ocr_engine = StubOCREngine(("《关于申请开立履约保函的确讣函》",))

            result = complete_processing_batch(
                batch_root,
                returned_pdf,
                root / "回拼结果",
                ocr_engine=ocr_engine,
            )

            self.assertEqual(result.items[0].status, "completed")
            self.assertGreaterEqual(result.items[0].score, 90)

    def test_one_page_ocr_failure_does_not_block_other_scanned_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(
                root,
                [("失败页.docx", "失败页确认函"), ("成功页.docx", "成功页确认函")],
            )
            returned_pdf = root / "returned.pdf"
            write_scanned_pdf_pages(
                returned_pdf,
                [("white", None), ("gray", None)],
            )
            ocr_engine = StubOCREngine(
                RuntimeError("单页 OCR 失败"),
                ("《成功页确认函》",),
            )

            result = complete_processing_batch(
                batch_root,
                returned_pdf,
                root / "回拼结果",
                ocr_engine=ocr_engine,
            )

            self.assertEqual(result.items[0].status, "unmatched")
            self.assertEqual(result.items[1].status, "completed")
            self.assertEqual(result.items[1].returned_page, 2)
            self.assertEqual(result.unused_pages, (1,))
            self.assertEqual(len(result.ocr_failures), 1)
            self.assertEqual(result.ocr_failures[0].page, 1)
            self.assertIn("单页 OCR 失败", result.ocr_failures[0].reason)

    def test_multiple_short_exact_text_titles_do_not_call_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(
                root,
                [("保函.docx", "保函"), ("函证.docx", "函证")],
            )
            returned_pdf = root / "returned.pdf"
            write_pdf_pages(
                returned_pdf,
                [("《保函》", (400, 600)), ("《函证》", (400, 600))],
            )
            ocr_engine = StubOCREngine(AssertionError("不应调用 OCR"))

            result = complete_processing_batch(
                batch_root,
                returned_pdf,
                root / "回拼结果",
                ocr_engine=ocr_engine,
            )

            self.assertEqual(
                [item.status for item in result.items],
                ["completed", "completed"],
            )
            self.assertEqual(ocr_engine.calls, [])

if __name__ == "__main__":
    unittest.main()

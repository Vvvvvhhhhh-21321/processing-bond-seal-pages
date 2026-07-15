import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.ocr import RapidOCRTitleEngine, prepare_ocr_models  # noqa: E402
from completion_test_support import (  # noqa: E402
    make_fake_pymupdf_module,
    make_fake_rapidocr_module,
)


class RapidOCRAdapterTests(unittest.TestCase):
    def test_uses_ppocrv6_small_onnxruntime_and_reuses_cached_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory) / "models"
            created_params = []
            calls = []

            render_events = []
            rapidocr_module = make_fake_rapidocr_module(
                ("《扫描型确认函》",),
                created_params,
                calls,
            )
            pymupdf_module = make_fake_pymupdf_module(
                b"rendered-page",
                render_events,
            )

            with patch.dict(
                sys.modules,
                {
                    "onnxruntime": object(),
                    "pymupdf": pymupdf_module,
                    "rapidocr": rapidocr_module,
                },
            ):
                engine = RapidOCRTitleEngine(cache_dir=cache_dir)
                pdf_path = Path(directory) / "returned.pdf"
                first = engine.recognize_page(pdf_path, 2)
                second = engine.recognize_page(pdf_path, 1)

            self.assertEqual(first, ("《扫描型确认函》",))
            self.assertEqual(second, ("《扫描型确认函》",))
            self.assertEqual(len(created_params), 1)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][0], b"rendered-page")
            self.assertEqual(
                render_events[:4],
                [
                    ("open", str(Path(directory) / "returned.pdf")),
                    ("load_page", 1),
                    ("get_pixmap", {"dpi": 200, "alpha": False}),
                    ("tobytes", "png"),
                ],
            )
            self.assertTrue(cache_dir.is_dir())
            params = created_params[0]
            self.assertEqual(params["Det.engine_type"], "onnxruntime")
            self.assertEqual(params["Det.lang_type"], "ch-det")
            self.assertEqual(params["Det.model_type"], "small")
            self.assertEqual(params["Det.ocr_version"], "PP-OCRv6")
            self.assertEqual(params["Rec.engine_type"], "onnxruntime")
            self.assertEqual(params["Rec.lang_type"], "ch-rec")
            self.assertEqual(params["Rec.model_type"], "small")
            self.assertEqual(params["Rec.ocr_version"], "PP-OCRv6")
            self.assertEqual(params["Global.model_root_dir"], str(cache_dir))
            self.assertEqual(
                calls[0][1],
                {"use_det": True, "use_cls": True, "use_rec": True},
            )

    def test_model_preparation_writes_offline_ready_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory) / "models"
            rapidocr_module = make_fake_rapidocr_module((), [], [])

            with patch.dict(
                sys.modules,
                {
                    "onnxruntime": object(),
                    "rapidocr": rapidocr_module,
                },
            ):
                prepared_dir = prepare_ocr_models(cache_dir)

            self.assertEqual(prepared_dir, cache_dir)
            self.assertEqual(
                (cache_dir / ".ppocrv6-small-ready").read_text(encoding="utf-8"),
                "PP-OCRv6 small\n",
            )


if __name__ == "__main__":
    unittest.main()

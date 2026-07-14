import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.ocr import RapidOCRTitleEngine  # noqa: E402


class RapidOCRAdapterTests(unittest.TestCase):
    def test_uses_ppocrv6_small_onnxruntime_and_reuses_cached_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory) / "models"
            created_params = []
            calls = []

            class FakeRapidOCR:
                def __init__(self, params):
                    created_params.append(params)

                def __call__(self, image_bytes, **options):
                    calls.append((image_bytes, options))
                    return SimpleNamespace(txts=("《扫描型确认函》",))

            rapidocr_module = SimpleNamespace(
                RapidOCR=FakeRapidOCR,
                EngineType=SimpleNamespace(ONNXRUNTIME="onnxruntime"),
                LangDet=SimpleNamespace(CH="ch-det"),
                LangRec=SimpleNamespace(CH="ch-rec"),
                ModelType=SimpleNamespace(SMALL="small"),
                OCRVersion=SimpleNamespace(PPOCRV6="PP-OCRv6"),
            )

            with patch.dict(
                sys.modules,
                {
                    "onnxruntime": SimpleNamespace(),
                    "rapidocr": rapidocr_module,
                },
            ):
                engine = RapidOCRTitleEngine(cache_dir=cache_dir)
                first = engine.recognize(b"first-image")
                second = engine.recognize(b"second-image")

            self.assertEqual(first, ("《扫描型确认函》",))
            self.assertEqual(second, ("《扫描型确认函》",))
            self.assertEqual(len(created_params), 1)
            self.assertEqual(len(calls), 2)
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


if __name__ == "__main__":
    unittest.main()

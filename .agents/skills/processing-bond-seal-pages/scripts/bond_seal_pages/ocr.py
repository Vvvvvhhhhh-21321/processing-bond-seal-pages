from importlib import import_module
import os
from pathlib import Path
import sys

from .seal_page_titles import extract_text_title


class OCRUnavailableError(RuntimeError):
    pass


def default_ocr_cache_dir():
    if os.name == "nt":
        cache_root = Path(
            os.environ.get(
                "LOCALAPPDATA",
                Path.home() / "AppData" / "Local",
            )
        )
    elif sys.platform == "darwin":
        cache_root = Path.home() / "Library" / "Caches"
    else:
        cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_root / "bond-seal-pages" / "rapidocr"


class RapidOCRTitleEngine:
    def __init__(self, cache_dir=None, render_dpi=200):
        self.cache_dir = Path(cache_dir or default_ocr_cache_dir())
        self.render_dpi = render_dpi
        self._engine = None
        self._initialization_error = None

    def _create_engine(self):
        import_module("onnxruntime")
        rapidocr = import_module("rapidocr")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        params = {
            "Global.log_level": "critical",
            "Global.model_root_dir": str(self.cache_dir),
            "Det.engine_type": rapidocr.EngineType.ONNXRUNTIME,
            "Det.lang_type": rapidocr.LangDet.CH,
            "Det.model_type": rapidocr.ModelType.SMALL,
            "Det.ocr_version": rapidocr.OCRVersion.PPOCRV6,
            "Cls.engine_type": rapidocr.EngineType.ONNXRUNTIME,
            "Rec.engine_type": rapidocr.EngineType.ONNXRUNTIME,
            "Rec.lang_type": rapidocr.LangRec.CH,
            "Rec.model_type": rapidocr.ModelType.SMALL,
            "Rec.ocr_version": rapidocr.OCRVersion.PPOCRV6,
        }
        return rapidocr.RapidOCR(params=params)

    def _get_engine(self):
        if self._initialization_error is not None:
            raise self._initialization_error
        if self._engine is None:
            try:
                self._engine = self._create_engine()
            except Exception as error:
                self._initialization_error = OCRUnavailableError(
                    "无法初始化 RapidOCR PP-OCRv6 small；"
                    "请确认 rapidocr>=3.9.0、onnxruntime 和模型缓存已准备"
                )
                raise self._initialization_error from error
        return self._engine

    def recognize_page(self, pdf_path, page_number):
        try:
            pymupdf = import_module("pymupdf")
        except ImportError as error:
            raise OCRUnavailableError(
                "无法渲染回章页；请确认 pymupdf 已安装"
            ) from error
        with pymupdf.open(str(pdf_path)) as document:
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(dpi=self.render_dpi, alpha=False)
            image_bytes = pixmap.tobytes("png")
        result = self._get_engine()(
            image_bytes,
            use_det=True,
            use_cls=True,
            use_rec=True,
        )
        return tuple(result.txts or ())


def prepare_ocr_models(cache_dir=None):
    engine = RapidOCRTitleEngine(cache_dir=cache_dir)
    engine._get_engine()
    engine.cache_dir.mkdir(parents=True, exist_ok=True)
    marker = engine.cache_dir / ".ppocrv6-small-ready"
    marker.write_text("PP-OCRv6 small\n", encoding="utf-8")
    return engine.cache_dir


def extract_ocr_page_title(pdf_path, page_number, engine):
    recognized_lines = engine.recognize_page(pdf_path, page_number)
    return extract_text_title("\n".join(recognized_lines))

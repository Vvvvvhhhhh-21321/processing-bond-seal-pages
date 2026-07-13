from pathlib import Path


PDF_FORMAT_CODE = 17


def _create_word_application():
    try:
        from win32com.client import DispatchEx
    except ImportError as error:
        raise RuntimeError("缺少 Windows Word 转换依赖 pywin32，请先安装后再试") from error
    return DispatchEx("Word.Application")


class WindowsWordPdfConverter:
    def __init__(self, application_factory=None):
        factory = application_factory or _create_word_application
        self._application = factory()
        self._application.Visible = False
        self._application.DisplayAlerts = 0

    def convert(self, source_path, output_path):
        source_path = Path(source_path).resolve()
        output_path = Path(output_path).resolve()
        document = self._application.Documents.Open(
            str(source_path),
            ReadOnly=True,
            AddToRecentFiles=False,
        )
        try:
            document.ExportAsFixedFormat(str(output_path), PDF_FORMAT_CODE)
        finally:
            document.Close(0)

    def close(self):
        if self._application is not None:
            self._application.Quit()
            self._application = None

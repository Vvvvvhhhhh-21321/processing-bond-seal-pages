import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bond_seal_pages.word_conversion import WindowsWordPdfConverter  # noqa: E402


class FakeDocument:
    def __init__(self):
        self.export_call = None
        self.close_call = None

    def ExportAsFixedFormat(self, output_path, format_code):
        self.export_call = (output_path, format_code)

    def Close(self, save_changes):
        self.close_call = save_changes


class FakeDocuments:
    def __init__(self, document):
        self.document = document
        self.open_call = None

    def Open(self, input_path, **options):
        self.open_call = (input_path, options)
        return self.document


class FakeWordApplication:
    def __init__(self):
        self.document = FakeDocument()
        self.Documents = FakeDocuments(self.document)
        self.Visible = None
        self.DisplayAlerts = None
        self.quit_called = False

    def Quit(self):
        self.quit_called = True


class WindowsWordPdfConverterTests(unittest.TestCase):
    def test_converts_in_background_without_saving_the_word_document(self):
        application = FakeWordApplication()
        converter = WindowsWordPdfConverter(application_factory=lambda: application)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working_paper_path = root / "底稿.docx"
            output = root / "输出.pdf"
            working_paper_path.write_bytes(b"word")

            converter.convert(working_paper_path, output)
            converter.close()

            self.assertFalse(application.Visible)
            self.assertEqual(application.DisplayAlerts, 0)
            self.assertEqual(
                application.Documents.open_call,
                (
                    str(working_paper_path.resolve()),
                    {"ReadOnly": True, "AddToRecentFiles": False},
                ),
            )
            self.assertEqual(application.document.export_call, (str(output.resolve()), 17))
            self.assertEqual(application.document.close_call, 0)
            self.assertTrue(application.quit_called)


if __name__ == "__main__":
    unittest.main()

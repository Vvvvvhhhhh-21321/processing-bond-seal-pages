from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import zipfile

import pytest
from pypdf import PdfReader, PdfWriter

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPOSITORY_ROOT / ".agents" / "skills" / "processing-bond-seal-pages" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from bond_seal_pages.batch_contract import (  # noqa: E402
    QuickBatchContractError,
    SelectedBatchCancelledError,
    SelectedBatchProcessingError,
    validate_quick_batch_manifest,
)
from bond_seal_pages.selected_batch import collect_selected_batch  # noqa: E402
from bond_seal_pages.word_conversion import WindowsWordPdfConverter  # noqa: E402
from bond_seal_pages.word_worker import serve  # noqa: E402


def _write_pdf(path: Path, page_count: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        writer.write(output)


def _write_minimal_docx(path: Path, text: str = "Signing page test") -> None:
    document = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:sectPr/></w:body>
</w:document>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''
    relationships = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)


class FakeConverter:
    def __init__(self, *, fail_names: set[str] | None = None) -> None:
        self.fail_names = fail_names or set()
        self.calls: list[str] = []
        self.closed = False

    def convert(self, source: Path, destination: Path) -> None:
        self.calls.append(source.name)
        if source.name in self.fail_names:
            raise RuntimeError("fixture conversion error")
        _write_pdf(destination, 2)

    def close(self) -> None:
        self.closed = True


class CancellableConverter(FakeConverter):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.stop = threading.Event()

    def convert(self, source: Path, destination: Path) -> None:
        self.started.set()
        self.stop.wait(timeout=10)
        if self.stop.is_set():
            raise RuntimeError("conversion cancelled")
        _write_pdf(destination)

    def cancel(self) -> None:
        self.stop.set()


def _request(*paths: Path, output: Path | None = None) -> dict:
    value = {"version": 1, "files": [str(path.resolve()) for path in paths]}
    if output is not None:
        value["output_directory"] = str(output.resolve())
    return value


def test_explicit_selection_deduplicates_and_validates_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    first = source / "Alpha.docx"
    duplicate = source / "z-copy.docx"
    excluded = source / "not-selected.docx"
    _write_minimal_docx(first, "same content")
    duplicate.write_bytes(first.read_bytes())
    _write_minimal_docx(excluded, "not selected")
    converter = FakeConverter()
    events: list[dict] = []

    result = collect_selected_batch(
        _request(duplicate, first),
        converter_factory=lambda: converter,
        progress=events.append,
    )

    assert result["status"] == "completed"
    assert result["selected_count"] == 2
    assert result["page_count"] == 1
    assert converter.calls == ["Alpha.docx"]
    assert converter.closed
    assert any(event["stage"] == "completed" for event in events)
    assert all(event.get("path") != str(excluded) for event in events)
    manifest = validate_quick_batch_manifest(result["batch_dir"])
    assert manifest["collection"] == Path(result["collection"]).name
    assert [item["working_paper_id"] for item in manifest["items"]] == [
        "Alpha.docx", "z-copy.docx"
    ]
    assert {item["seal_page"] for item in manifest["items"]} == {1}
    assert {item["reuse_of"] for item in manifest["items"]} == {"Alpha.docx"}
    assert all((Path(result["batch_dir"]) / item["converted_pdf"]).is_file() for item in manifest["items"])


def test_duplicate_representative_can_fall_back_to_later_file(tmp_path: Path) -> None:
    first = tmp_path / "src" / "a.docx"
    second = tmp_path / "src" / "b.docx"
    _write_minimal_docx(first, "same")
    second.write_bytes(first.read_bytes())
    converter = FakeConverter(fail_names={"a.docx"})

    result = collect_selected_batch(
        _request(first, second), converter_factory=lambda: converter
    )

    manifest = validate_quick_batch_manifest(result["batch_dir"])
    assert converter.calls == ["a.docx", "b.docx"]
    assert {item["reuse_of"] for item in manifest["items"]} == {"b.docx"}


def test_collision_preserves_old_result_and_uses_next_name(tmp_path: Path) -> None:
    doc = tmp_path / "source" / "one.docx"
    _write_minimal_docx(doc)
    old_pdf = doc.parent / "签署页合集.pdf"
    old_pdf.write_bytes(b"old result")
    old_sidecar = doc.parent / "签署页合集_处理数据_请勿删除"
    old_sidecar.mkdir()
    (old_sidecar / "user-file.txt").write_text("preserve", encoding="utf-8")

    result = collect_selected_batch(_request(doc), converter_factory=lambda: FakeConverter())

    assert Path(result["collection"]).name == "签署页合集 (2).pdf"
    assert old_pdf.read_bytes() == b"old result"
    assert (old_sidecar / "user-file.txt").read_text(encoding="utf-8") == "preserve"
    validate_quick_batch_manifest(result["batch_dir"])


def test_failure_leaves_no_published_output_or_staging(tmp_path: Path) -> None:
    doc = tmp_path / "source" / "broken.docx"
    _write_minimal_docx(doc)
    converter = FakeConverter(fail_names={doc.name})

    with pytest.raises(SelectedBatchProcessingError) as raised:
        collect_selected_batch(_request(doc), converter_factory=lambda: converter)

    assert raised.value.selected_count == 1
    assert raised.value.failures[0]["path"] == str(doc.resolve())
    assert converter.closed
    assert not (doc.parent / "签署页合集.pdf").exists()
    assert not (doc.parent / "签署页合集_处理数据_请勿删除").exists()
    assert not list(doc.parent.glob(".bond-seal-quick-batch-*"))
    assert not list(doc.parent.glob(".*.bond-seal-publish.lock"))


def test_request_rejects_mixed_directories_and_duplicate_paths(tmp_path: Path) -> None:
    first = tmp_path / "a" / "one.docx"
    second = tmp_path / "b" / "two.docx"
    _write_minimal_docx(first)
    _write_minimal_docx(second)

    with pytest.raises(QuickBatchContractError, match="同一个文件夹"):
        collect_selected_batch(_request(first, second), converter_factory=lambda: FakeConverter())
    with pytest.raises(QuickBatchContractError, match="重复路径"):
        collect_selected_batch(_request(first, first), converter_factory=lambda: FakeConverter())


def test_cancel_during_conversion_stops_worker_and_cleans_staging(tmp_path: Path) -> None:
    doc = tmp_path / "source" / "cancel.docx"
    _write_minimal_docx(doc)
    converter = CancellableConverter()
    cancel_event = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            collect_selected_batch(
                _request(doc),
                converter_factory=lambda: converter,
                cancel_event=cancel_event,
            )
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert converter.started.wait(timeout=3)
    cancel_event.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], SelectedBatchCancelledError)
    assert converter.stop.is_set() and converter.closed
    assert not (doc.parent / "签署页合集.pdf").exists()
    assert not (doc.parent / "签署页合集_处理数据_请勿删除").exists()
    assert not list(doc.parent.glob(".bond-seal-quick-batch-*"))


def test_worker_protocol_uses_safe_open_flags_and_quits() -> None:
    class Document:
        closed = False

        def ExportAsFixedFormat(self, output: str, _format: int) -> None:
            _write_pdf(Path(output))

        def Close(self, _save: int) -> None:
            self.closed = True

    class Documents:
        def __init__(self) -> None:
            self.args: dict = {}
            self.document = Document()

        def Open(self, path: str, **kwargs: object) -> Document:
            self.args = {"path": path, **kwargs}
            return self.document

    class Options:
        UpdateLinksAtOpen = True

    class Application:
        def __init__(self) -> None:
            self.Documents = Documents()
            self.Options = Options()
            self.Quit_called = False

        def Quit(self) -> None:
            self.Quit_called = True

    class Output:
        def __init__(self) -> None:
            self.value = ""

        def write(self, value: str) -> int:
            self.value += value
            return len(value)

        def flush(self) -> None:
            pass

    class Input:
        def __init__(self, lines: list[str]) -> None:
            self.lines = iter(lines)

        def __iter__(self):
            return self

        def __next__(self) -> str:
            return next(self.lines)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "source.docx"
        output = root / "source.pdf"
        _write_minimal_docx(source)
        requests = [
            {"request_id": "convert", "operation": "convert", "input_path": str(source), "output_path": str(output)},
            {"request_id": "shutdown", "operation": "shutdown"},
        ]
        app = Application()
        stream = Output()
        result = serve(
            Input([json.dumps(item) + "\n" for item in requests]),
            stream,
            application_factory=lambda: app,
        )

    messages = [json.loads(line) for line in stream.value.splitlines()]
    assert result == 0
    assert messages[0]["event"] == "word_ready"
    assert [message["request_id"] for message in messages if "ok" in message] == ["convert", "shutdown"]
    assert app.AutomationSecurity == 3 and app.Visible is False and app.DisplayAlerts == 0
    assert app.Options.UpdateLinksAtOpen is False
    assert app.Documents.args["ReadOnly"] is True
    assert app.Documents.args["AddToRecentFiles"] is False
    assert app.Documents.document.closed and app.Quit_called


def test_word_worker_timeout_stops_only_its_worker_process(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "source.pdf"
    _write_minimal_docx(source)
    converter = WindowsWordPdfConverter(
        timeout_seconds=0.2,
        shutdown_timeout_seconds=0.2,
        worker_command_factory=lambda: [
            sys.executable, "-c", "import sys,time; sys.stdin.readline(); time.sleep(20)"
        ],
    )
    with pytest.raises(TimeoutError, match="超时"):
        converter.convert(source, output)
    converter.close()
    assert not output.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Microsoft Word worker is Windows-only")
def test_real_word_worker_converts_a_docx(tmp_path: Path) -> None:
    pytest.importorskip("win32com.client")
    from bond_seal_pages.preflight import discover_windows_word
    if discover_windows_word() is None:
        pytest.skip("Microsoft Word is not installed on this runner")
    source = tmp_path / "real-word-worker.docx"
    output = tmp_path / "real-word-worker.pdf"
    _write_minimal_docx(source, "Word worker integration check")
    converter = WindowsWordPdfConverter(timeout_seconds=60)
    try:
        converted = converter.convert(source, output)
    finally:
        converter.close()
    assert converted == output.resolve()
    reader = PdfReader(str(output))
    try:
        assert len(reader.pages) >= 1
    finally:
        reader.stream.close()

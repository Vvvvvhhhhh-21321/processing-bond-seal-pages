from __future__ import annotations

from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest
from pypdf import PdfWriter

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents" / "skills" / "processing-bond-seal-pages" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from bond_seal_pages.batch_contract import (  # noqa: E402
    QuickBatchContractError,
    SelectedBatchProcessingError,
)
import bond_seal_pages.batch_import as batch_import_module  # noqa: E402
from bond_seal_pages.batch_import import import_quick_batch  # noqa: E402
from bond_seal_pages.cli import main  # noqa: E402
from bond_seal_pages.pdf_ops import sha256_file  # noqa: E402
import bond_seal_pages.preflight as preflight_module  # noqa: E402
from bond_seal_pages.preflight import (  # noqa: E402
    PythonRuntimeInfo,
    run_preflight,
)
from bond_seal_pages.selected_batch import collect_selected_batch  # noqa: E402
from bond_seal_pages.project_workflow import (  # noqa: E402
    inspect_signing_project,
    load_signing_project,
)


def _pdf_bytes(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _write_quick_batch(root: Path, label: str) -> tuple[Path, Path, list[Path]]:
    source_root = root / f"{label}-source"
    source_root.mkdir(parents=True)
    documents = {
        "A-duplicate.docx": b"identical Word contents",
        "B-duplicate.docx": b"identical Word contents",
        "C-unique.doc": b"different Word contents",
    }
    source_paths = []
    hashes = {}
    for name, content in documents.items():
        path = source_root / name
        path.write_bytes(content)
        source_paths.append(path)
        hashes[name] = sha256(content).hexdigest()

    batch_dir = root / f"{label}-collection_处理数据_请勿删除"
    pdf_root = batch_dir / "pdfs"
    pdf_root.mkdir(parents=True)
    per_document_pdf = _pdf_bytes(1)
    pdf_hash = sha256(per_document_pdf).hexdigest()
    items = []
    for name in sorted(documents, key=lambda value: (value.casefold(), value)):
        (pdf_root / f"{name}.pdf").write_bytes(per_document_pdf)
        duplicate_hash = hashes[name]
        representative = (
            "A-duplicate.docx"
            if name in {"A-duplicate.docx", "B-duplicate.docx"}
            else name
        )
        items.append(
            {
                "working_paper_id": name,
                "working_paper_path": name,
                "working_paper_sha256": duplicate_hash,
                "converted_pdf": f"pdfs/{name}.pdf",
                "pdf_sha256": pdf_hash,
                "pdf_page_count": 1,
                "seal_page": 1 if name != "C-unique.doc" else 2,
                "reuse_of": representative,
                "duplicate_group_sha256": duplicate_hash,
            }
        )

    collection_name = f"{label} collection.pdf"
    collection = root / collection_name
    collection.write_bytes(_pdf_bytes(2))
    manifest = {
        "format": "bond-seal-quick-batch",
        "version": 1,
        "batch_id": str(uuid.uuid4()),
        "source_root": str(source_root.resolve()),
        "collection": collection_name,
        "collection_sha256": sha256_file(collection),
        "selected_count": len(items),
        "page_count": 2,
        "items": items,
    }
    (batch_dir / "quick_batch.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return batch_dir, collection, source_paths


def _project_root(parent: Path, name: str = "Example") -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    return parent / f"{name}_签署页处理"


def test_import_creates_prepared_group_by_copy_and_reuses_duplicate_page(tmp_path):
    batch_dir, collection, source_paths = _write_quick_batch(tmp_path, "issuer")
    project_root = _project_root(tmp_path / "projects")

    result = import_quick_batch(batch_dir, project_root, "issuer")

    manifest = load_signing_project(project_root)
    group = manifest["groups"]["issuer"]
    batch_manifest_path = project_root / group["batch_manifest"]
    batch_manifest = json.loads(batch_manifest_path.read_text(encoding="utf-8"))
    assert result.succeeded == 2
    assert result.failed == 0
    assert group["state"] == "prepared"
    assert result.collection_path.read_bytes() == collection.read_bytes()
    assert len(batch_manifest["items"]) == 3
    duplicate_items = [
        item for item in batch_manifest["items"]
        if item["working_paper_id"].startswith(("A-", "B-"))
    ]
    assert len(duplicate_items) == 2
    assert {item["seal_page"] for item in duplicate_items} == {1}
    assert {item["reuse_of"] for item in duplicate_items} == {"A-duplicate.docx"}
    assert all(path.is_file() for path in source_paths)
    assert all(
        (project_root / record["path"]).read_bytes() == source.read_bytes()
        for record, source in zip(group["words"], sorted(source_paths))
    )
    status = inspect_signing_project(project_root)
    assert status.requires_confirmation is False
    assert status.groups["issuer"]["state"] == "prepared"


def test_import_accepts_manifest_from_shared_collection_core(tmp_path):
    source_root = tmp_path / "actual-core-source"
    source_root.mkdir()
    first = source_root / "first.docx"
    duplicate = source_root / "duplicate.docx"
    unique = source_root / "unique.doc"
    first.write_bytes(b"same Word bytes")
    duplicate.write_bytes(first.read_bytes())
    unique.write_bytes(b"another Word")

    class FakeConverter:
        def convert(self, _source, output):
            output.write_bytes(_pdf_bytes(2))

        def close(self):
            pass

    result = collect_selected_batch(
        {
            "version": 1,
            "files": [str(first.resolve()), str(duplicate.resolve()), str(unique.resolve())],
        },
        converter_factory=FakeConverter,
    )
    project_root = _project_root(tmp_path / "projects", "Generated")

    imported = import_quick_batch(result["batch_dir"], project_root, "issuer")

    project_manifest = load_signing_project(project_root)
    group = project_manifest["groups"]["issuer"]
    batch_manifest = json.loads(
        (project_root / group["batch_manifest"]).read_text(encoding="utf-8")
    )
    assert imported.succeeded == 2
    assert len(batch_manifest["items"]) == 3
    assert result["selected_count"] == 3
    assert result["page_count"] == 2


def test_two_quick_batches_can_fill_separate_groups_in_one_project(tmp_path):
    issuer_batch, _, _ = _write_quick_batch(tmp_path, "issuer")
    team_batch, _, _ = _write_quick_batch(tmp_path, "team")
    project_root = _project_root(tmp_path / "projects", "Two Groups")

    import_quick_batch(issuer_batch, project_root, "issuer")
    import_quick_batch(team_batch, project_root, "project_team")

    manifest = load_signing_project(project_root)
    assert set(manifest["groups"]) == {"issuer", "project_team"}
    assert all(group["state"] == "prepared" for group in manifest["groups"].values())
    assert inspect_signing_project(project_root).requires_confirmation is False
    assert (project_root / manifest["groups"]["issuer"]["collection"]).is_file()
    assert (project_root / manifest["groups"]["project_team"]["collection"]).is_file()


@pytest.mark.parametrize("changed_file", ["source", "cache"])
def test_hash_mismatch_does_not_modify_existing_project(tmp_path, changed_file):
    initial_batch, _, _ = _write_quick_batch(tmp_path, "initial")
    next_batch, _, source_paths = _write_quick_batch(tmp_path, "next")
    project_root = _project_root(tmp_path / "projects", "Protected")
    import_quick_batch(initial_batch, project_root, "issuer")
    manifest_path = project_root / "项目状态_project.json"
    before_manifest = manifest_path.read_bytes()

    if changed_file == "source":
        source_paths[0].write_bytes(b"changed after collection")
    else:
        cache_pdf = next_batch / "pdfs" / "A-duplicate.docx.pdf"
        cache_pdf.write_bytes(cache_pdf.read_bytes() + b"tampered")

    with pytest.raises(QuickBatchContractError):
        import_quick_batch(next_batch, project_root, "project_team")

    assert manifest_path.read_bytes() == before_manifest
    assert "project_team" not in load_signing_project(project_root)["groups"]
    assert not (project_root / "项目组分析文件").exists()
    assert not (project_root / "处理数据_请勿删除" / "项目组分析文件").exists()


def test_existing_project_rolls_back_group_if_second_directory_move_fails(
    tmp_path, monkeypatch
):
    initial_batch, _, _ = _write_quick_batch(tmp_path, "initial")
    next_batch, _, _ = _write_quick_batch(tmp_path, "next")
    project_root = _project_root(tmp_path / "projects", "Rollback")
    import_quick_batch(initial_batch, project_root, "issuer")
    manifest_path = project_root / "项目状态_project.json"
    before_manifest = manifest_path.read_bytes()
    original_replace = Path.replace

    def fail_second_group_move(path, target):
        if (
            path.name == "项目组分析文件"
            and path.parent.name == "处理数据_请勿删除"
        ):
            raise OSError("injected process-directory failure")
        return original_replace(path, target)

    monkeypatch.setattr(batch_import_module.Path, "replace", fail_second_group_move)
    with pytest.raises(OSError, match="injected process-directory failure"):
        import_quick_batch(next_batch, project_root, "project_team")

    assert manifest_path.read_bytes() == before_manifest
    assert not (project_root / "项目组分析文件").exists()
    assert not (project_root / "处理数据_请勿删除" / "项目组分析文件").exists()
    assert not list(project_root.glob(".bond-seal-import-*"))


def _ready_preflight():
    selected = SimpleNamespace(
        source="bundled-runtime",
        command=("bondseal.exe",),
        runtime=SimpleNamespace(executable="bondseal.exe"),
    )
    return SimpleNamespace(
        ready=True,
        selected=selected,
        candidates=(selected,),
        failed_checks=(),
        platform_name="win32",
    )


def test_cli_collect_emits_one_json_object_and_passes_lightweight_preflight(tmp_path):
    request_file = tmp_path / "selection.json"
    request = {"version": 1, "files": [r"C:\work\paper.docx"]}
    request_file.write_text(json.dumps(request), encoding="utf-8")
    output = StringIO()
    received = []

    def preflight_runner(**kwargs):
        assert kwargs == {
            "selected_python": None,
            "require_converter": True,
            "lightweight": True,
        }
        return _ready_preflight()

    def collect_runner(value):
        received.append(value)
        return {
            "status": "completed",
            "collection": r"C:\work\签署页合集.pdf",
            "batch_dir": r"C:\work\签署页合集_处理数据_请勿删除",
            "manifest": r"C:\work\签署页合集_处理数据_请勿删除\quick_batch.json",
            "selected_count": 1,
            "page_count": 1,
        }

    code = main(
        ["collect", "--request-file", str(request_file), "--json"],
        preflight_runner=preflight_runner,
        collect_runner=collect_runner,
        output=output,
    )

    assert code == 0
    assert received == [request]
    assert json.loads(output.getvalue())["status"] == "completed"
    assert output.getvalue().lstrip().startswith("{")


def test_cli_collect_failure_includes_machine_readable_failures(tmp_path):
    request_file = tmp_path / "selection.json"
    request_file.write_text(json.dumps({"version": 1, "files": ["x"]}), encoding="utf-8")
    output = StringIO()

    def fail(_request):
        raise SelectedBatchProcessingError(
            [{"path": "x", "reason": "conversion failed"}],
            selected_count=1,
            page_count=0,
        )

    code = main(
        ["collect", "--request-file", str(request_file), "--json"],
        preflight_runner=lambda **_: _ready_preflight(),
        collect_runner=fail,
        output=output,
    )

    payload = json.loads(output.getvalue())
    assert code == 1
    assert payload["failures"] == [{"path": "x", "reason": "conversion failed"}]
    assert payload["selected_count"] == 1
    assert payload["page_count"] == 0


def test_cli_collect_environment_failure_returns_two_without_processing(tmp_path):
    request_file = tmp_path / "selection.json"
    request_file.write_text(json.dumps({"version": 1, "files": ["x"]}), encoding="utf-8")
    output = StringIO()
    called = []
    failed = SimpleNamespace(
        ready=False,
        selected=None,
        candidates=(),
        failed_checks=(),
        platform_name="win32",
    )

    code = main(
        ["collect", "--request-file", str(request_file), "--json"],
        preflight_runner=lambda **_: failed,
        collect_runner=lambda _request: called.append(True),
        output=output,
    )

    assert code == 2
    assert called == []
    assert json.loads(output.getvalue())["status"] == "preflight_failed"


def test_cli_project_import_skips_preflight_and_returns_json(tmp_path):
    batch_dir, collection, _ = _write_quick_batch(tmp_path, "cli")
    project_root = _project_root(tmp_path / "projects")
    output = StringIO()
    calls = []
    result = SimpleNamespace(
        group_key="issuer",
        project_root=project_root,
        collection_path=collection,
        batch_manifest_path=batch_dir / "quick_batch.json",
        succeeded=2,
        failed=0,
    )

    def import_runner(batch, project, group):
        calls.append((batch, project, group))
        return result

    code = main(
        [
            "project-import-batch",
            str(batch_dir),
            str(project_root),
            "--group",
            "issuer",
        ],
        preflight_runner=lambda **_: pytest.fail("import must not run OCR preflight"),
        import_batch_runner=import_runner,
        output=output,
    )

    assert code == 0
    assert calls == [(batch_dir, project_root, "issuer")]
    payload = json.loads(output.getvalue())
    assert payload["status"] == "prepared"
    assert payload["group"] == "issuer"


def test_lightweight_preflight_checks_only_converter_dependencies(monkeypatch):
    monkeypatch.setattr(preflight_module.sys, "frozen", True, raising=False)
    imported = []

    def fake_import(module):
        imported.append(module)
        if module == "win32com.client":
            raise ImportError("not packaged")
        return object()

    monkeypatch.setattr(preflight_module.importlib, "import_module", fake_import)
    result = run_preflight(
        platform_name="win32",
        current_executable="bondseal.exe",
        require_converter=True,
        lightweight=True,
        windows_word_locator=lambda: Path(r"C:\Program Files\Word\WINWORD.EXE"),
    )

    dependencies = {
        check.key: check.ok for check in result.checks if check.key.startswith("dependency:")
    }
    assert imported == ["pypdf", "win32com.client"]
    assert dependencies["dependency:pypdf"] is True
    assert dependencies["dependency:win32com.client"] is False
    assert not any(check.key == "ocr-model" for check in result.checks)
    assert result.ready is False

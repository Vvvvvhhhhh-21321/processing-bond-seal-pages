"""Copy a verified quick batch into an existing signing project.

This module adapts the quick-batch contract to the existing project v1
manifest without scanning directories, reconverting Word files, or moving
source documents.
"""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile


@contextmanager
def _project_import_lock(project_root):
    """Serialize imports targeting the same project across CLI processes."""
    project_key = os.path.normcase(str(project_root.resolve())).casefold()
    digest = hashlib.sha256(project_key.encode("utf-8")).hexdigest()
    lock_path = Path(tempfile.gettempdir()) / f"bond-seal-import-{digest}.lock"
    lock_file = lock_path.open("a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("另一个批次正在导入此签署项目") from error
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError("另一个批次正在导入此签署项目") from error
        locked = True
        yield
    finally:
        try:
            if locked:
                if os.name == "nt":
                    import msvcrt

                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


def import_quick_batch(batch_dir, project_root, group_key):
    """Validate a quick batch, then atomically add one prepared project group."""
    project_root = Path(project_root).resolve()
    with _project_import_lock(project_root):
        return _import_quick_batch_locked(batch_dir, project_root, group_key)


def _import_quick_batch_locked(batch_dir, project_root, group_key):
    from pypdf import PdfReader

    from .batch_contract import validate_quick_batch_manifest
    from .pdf_ops import sha256_file
    from .seal_page_titles import extract_text_title
    from .titles import normalize_title
    from .project_workflow import (
        GROUP_LAYOUTS,
        ITEM_STATUS_READY,
        PROCESS_DATA_NAME,
        PROJECT_MANIFEST_NAME,
        PROJECT_SUFFIX,
        GroupPreparationResult,
        _manifest_path,
        _utc_now,
        _validate_project_name,
        _write_manifest,
        inspect_signing_project,
        load_signing_project,
    )

    if group_key not in GROUP_LAYOUTS:
        raise ValueError(f"不支持的签署文件组：{group_key}")
    if not project_root.name.endswith(PROJECT_SUFFIX):
        raise ValueError(f"项目目录必须以 {PROJECT_SUFFIX} 结尾")
    project_name = _validate_project_name(
        project_root.name[: -len(PROJECT_SUFFIX)]
    )
    if not project_root.parent.is_dir():
        raise ValueError(f"项目父目录不存在：{project_root.parent}")

    # Validate all source and sidecar inputs before creating project files.
    batch_dir = Path(batch_dir).resolve()
    quick_manifest = validate_quick_batch_manifest(
        batch_dir,
        verify_source_hashes=True,
    )
    source_root = Path(quick_manifest["source_root"]).resolve(strict=True)
    collection_source = (batch_dir.parent / quick_manifest["collection"]).resolve(
        strict=True
    )
    if collection_source.parent != batch_dir.parent.resolve():
        raise ValueError("合集 PDF 必须与批次数据目录同级")

    is_new_project = not project_root.exists()
    initial_manifest_hash = None
    if is_new_project:
        manifest = {
            "version": 1,
            "project_name": project_name,
            "status": "prepared",
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "groups": {},
        }
    else:
        if not project_root.is_dir():
            raise ValueError(f"项目路径不是目录：{project_root}")
        manifest_path = _manifest_path(project_root)
        manifest_hash_before = sha256_file(manifest_path)
        manifest = load_signing_project(project_root)
        initial_manifest_hash = sha256_file(manifest_path)
        if manifest_hash_before != initial_manifest_hash:
            raise RuntimeError("项目清单正在变化，请稍后重新导入")
        if group_key in manifest["groups"]:
            raise ValueError(
                f"项目已经存在签署文件组：{GROUP_LAYOUTS[group_key]['label']}"
            )
        if str(manifest.get("status", "")).startswith("cleaned"):
            raise ValueError("项目过程数据已经清理，不能导入新签署文件组")
        project_status = inspect_signing_project(project_root)
        if project_status.requires_confirmation:
            raise ValueError("现有项目有文件或成果变化，检查并处理后才能导入新组")

    layout = GROUP_LAYOUTS[group_key]
    target_group_root = project_root / layout["label"]
    target_process_group_root = project_root / PROCESS_DATA_NAME / layout["label"]
    if not is_new_project and (
        target_group_root.exists() or target_process_group_root.exists()
    ):
        raise FileExistsError("目标签署文件组路径已存在；为避免覆盖，停止导入")

    source_items = quick_manifest["items"]
    source_by_id = {
        item["working_paper_id"]: source_root.joinpath(
            *PurePosixPath(item["working_paper_path"]).parts
        )
        for item in source_items
    }
    cache_by_id = {
        item["working_paper_id"]: batch_dir.joinpath(
            *PurePosixPath(item["converted_pdf"]).parts
        )
        for item in source_items
    }
    project_id_by_source_id = {
        item["working_paper_id"]: PurePosixPath(
            item["working_paper_path"]
        ).as_posix()
        for item in source_items
    }
    title_by_id = {}
    for item in source_items:
        representative_id = item["reuse_of"]
        representative_pdf = cache_by_id[representative_id]
        reader = PdfReader(str(representative_pdf))
        title_by_id[item["working_paper_id"]] = extract_text_title(
            reader.pages[-1].extract_text() or "",
            fallback=source_by_id[representative_id].stem,
        )

    staging_parent = project_root.parent if is_new_project else project_root
    staging_root = Path(
        tempfile.mkdtemp(prefix=".bond-seal-import-", dir=staging_parent)
    )
    staged_group_root = staging_root / layout["label"]
    staged_word_root = staged_group_root / "原始Word"
    staged_result_root = staged_group_root / (
        f"{project_name}_{layout['result_role']}"
    )
    staged_process_root = staging_root / PROCESS_DATA_NAME / layout["label"]
    staged_pdf_root = staged_process_root / "完整底稿PDF"
    staged_collection = staged_group_root / (
        f"{project_name}_{layout['collection_role']}.pdf"
    )
    staged_batch_manifest = staged_process_root / "处理批次清单_manifest.json"
    final_collection_relative = (
        Path(layout["label"])
        / f"{project_name}_{layout['collection_role']}.pdf"
    ).as_posix()
    final_batch_relative = (
        Path(PROCESS_DATA_NAME)
        / layout["label"]
        / "处理批次清单_manifest.json"
    ).as_posix()
    moved_paths = []
    created_process_parent = False
    new_project_installed = False
    manifest_committed = False
    try:
        staged_word_root.mkdir(parents=True)
        staged_result_root.mkdir(parents=True)
        staged_pdf_root.mkdir(parents=True)
        shutil.copy2(collection_source, staged_collection)
        if sha256_file(staged_collection) != quick_manifest["collection_sha256"]:
            raise ValueError("复制后的合集 PDF 哈希不一致")

        project_items = []
        reuse_groups = {}
        project_words = []
        for item in source_items:
            relative = PurePosixPath(item["working_paper_path"])
            source_path = source_by_id[item["working_paper_id"]]
            staged_word = staged_word_root.joinpath(*relative.parts)
            staged_word.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, staged_word)
            source_hash = item["working_paper_sha256"]
            if sha256_file(staged_word) != source_hash:
                raise ValueError(
                    f"复制后的 Word 哈希不一致：{item['working_paper_id']}"
                )

            staged_pdf = staged_pdf_root.joinpath(
                *relative.parent.parts,
                f"{relative.name}.pdf",
            )
            staged_pdf.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cache_by_id[item["working_paper_id"]], staged_pdf)
            pdf_hash = item["pdf_sha256"]
            if sha256_file(staged_pdf) != pdf_hash:
                raise ValueError(
                    f"复制后的转换 PDF 哈希不一致：{item['working_paper_id']}"
                )
            if len(PdfReader(str(staged_pdf)).pages) != item["pdf_page_count"]:
                raise ValueError(
                    f"复制后的转换 PDF 页数不一致：{item['working_paper_id']}"
                )

            source_id = item["working_paper_id"]
            project_id = project_id_by_source_id[source_id]
            project_representative_id = project_id_by_source_id[item["reuse_of"]]
            project_pdf_relative = (
                Path("完整底稿PDF")
                / relative.parent
                / f"{relative.name}.pdf"
            ).as_posix()
            project_items.append(
                {
                    "working_paper_id": project_id,
                    "working_paper_path": project_id,
                    "status": ITEM_STATUS_READY,
                    "title": title_by_id[source_id],
                    "normalized_title": normalize_title(title_by_id[source_id]),
                    "converted_pdf": project_pdf_relative,
                    "pdf_page_count": item["pdf_page_count"],
                    "working_paper_sha256": source_hash,
                    "pdf_sha256": pdf_hash,
                    "seal_page": item["seal_page"],
                    "reuse_of": project_representative_id,
                    "duplicate_group_sha256": item["duplicate_group_sha256"],
                }
            )
            project_words.append(
                {
                    "path": (
                        Path(layout["label"]) / "原始Word" / relative
                    ).as_posix(),
                    "sha256": source_hash,
                }
            )
            reuse_group = reuse_groups.setdefault(
                item["duplicate_group_sha256"],
                {
                    "sha256": item["duplicate_group_sha256"],
                    "representative": project_representative_id,
                    "members": [],
                    "seal_page": item["seal_page"],
                },
            )
            if reuse_group["seal_page"] != item["seal_page"]:
                raise ValueError("同一重复文件组映射到了不同的合集页")
            reuse_group["members"].append(project_id)

        final_word_root = project_root / layout["label"] / "原始Word"
        final_word_root_text = str(final_word_root.resolve())
        batch_payload = {
            "version": 1,
            "working_paper_root": final_word_root_text,
            "seal_pages": final_collection_relative,
            "duplicate_policy": "reuse",
            "excluded_duplicates": [],
            "reuse_groups": list(reuse_groups.values()),
            "group_key": group_key,
            "items": project_items,
        }
        _write_manifest(staged_batch_manifest, batch_payload)
        project_words.sort(key=lambda item: item["path"].casefold())
        group_record = {
            "label": layout["label"],
            "state": "prepared",
            "word_root": (Path(layout["label"]) / "原始Word").as_posix(),
            "process_root": (
                Path(PROCESS_DATA_NAME) / layout["label"]
            ).as_posix(),
            "words": project_words,
            "duplicate_policy": "reuse",
            "collection": final_collection_relative,
            "batch_manifest": final_batch_relative,
            "artifacts": {
                "collection": {
                    "path": final_collection_relative,
                    "sha256": sha256_file(staged_collection),
                },
                "batch_manifest": {
                    "path": final_batch_relative,
                    "sha256": sha256_file(staged_batch_manifest),
                },
            },
        }
        manifest["groups"][group_key] = group_record
        manifest["updated_at"] = _utc_now()
        staged_manifest = staging_root / PROJECT_MANIFEST_NAME
        _write_manifest(staged_manifest, manifest)

        if is_new_project:
            staging_root.replace(project_root)
            new_project_installed = True
            collection_path = project_root / final_collection_relative
            batch_manifest_path = project_root / final_batch_relative
            if (
                sha256_file(collection_path) != quick_manifest["collection_sha256"]
                or sha256_file(batch_manifest_path)
                != group_record["artifacts"]["batch_manifest"]["sha256"]
            ):
                raise ValueError("导入成果哈希校验失败")
        else:
            staged_group_root.replace(target_group_root)
            moved_paths.append(target_group_root)
            process_parent = project_root / PROCESS_DATA_NAME
            created_process_parent = not process_parent.exists()
            process_parent.mkdir(exist_ok=True)
            staged_process_group_root = (
                staging_root / PROCESS_DATA_NAME / layout["label"]
            )
            staged_process_group_root.replace(target_process_group_root)
            moved_paths.append(target_process_group_root)

            collection_path = project_root / final_collection_relative
            batch_manifest_path = project_root / final_batch_relative
            if (
                sha256_file(collection_path) != quick_manifest["collection_sha256"]
                or sha256_file(batch_manifest_path)
                != group_record["artifacts"]["batch_manifest"]["sha256"]
            ):
                raise ValueError("导入成果哈希校验失败")
            for item in project_items:
                relative = PurePosixPath(item["working_paper_id"])
                word_path = target_group_root / "原始Word" / Path(*relative.parts)
                pdf_path = (
                    target_process_group_root
                    / "完整底稿PDF"
                    / relative.parent
                    / f"{relative.name}.pdf"
                )
                if (
                    sha256_file(word_path) != item["working_paper_sha256"]
                    or sha256_file(pdf_path) != item["pdf_sha256"]
                ):
                    raise ValueError(
                        f"导入成果哈希校验失败：{item['working_paper_id']}"
                    )

            current_manifest_path = _manifest_path(project_root)
            if sha256_file(current_manifest_path) != initial_manifest_hash:
                raise RuntimeError("项目清单在导入期间发生变化，请检查后重试")
            staged_manifest.replace(current_manifest_path)
            manifest_committed = True

        return GroupPreparationResult(
            project_root,
            group_key,
            collection_path,
            batch_manifest_path,
            len(reuse_groups),
            0,
        )
    except Exception:
        if is_new_project and new_project_installed and project_root.exists():
            shutil.rmtree(project_root)
        elif not is_new_project and not manifest_committed:
            for moved_path in reversed(moved_paths):
                if moved_path.is_dir():
                    shutil.rmtree(moved_path)
                else:
                    moved_path.unlink(missing_ok=True)
            process_parent = project_root / PROCESS_DATA_NAME
            if created_process_parent and process_parent.is_dir():
                try:
                    process_parent.rmdir()
                except OSError:
                    pass
        raise
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=manifest_committed)

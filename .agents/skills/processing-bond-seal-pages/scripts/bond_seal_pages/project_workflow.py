from dataclasses import dataclass, replace
from datetime import datetime, timezone
from html import escape
import json
import math
import os
from pathlib import Path
import shutil
import sys

from .pdf_ops import sha256_file


PROJECT_MANIFEST_NAME = "项目状态_project.json"
PROJECT_SUFFIX = "_签署页处理"
PROCESS_DATA_NAME = "处理数据_请勿删除"
ITEM_STATUS_READY = "ready"
MODEL_DECISIONS = {"approved", "needs_adjustment", "unable_to_determine"}
GROUP_STATES = {
    "initializing",
    "initialized",
    "prepared",
    "returned_received",
    "date_review_ready",
    "completed",
    "completed_with_attention",
}

GROUP_LAYOUTS = {
    "issuer": {
        "label": "发行人说明性文件",
        "collection_role": "发行人待盖章页合集",
        "returned_role": "发行人回章原件",
        "review_role": "发行人日期确认稿",
        "result_role": "发行人盖章版底稿",
        "report_role": "发行人处理清单",
    },
    "project_team": {
        "label": "项目组分析文件",
        "collection_role": "项目组签字页合集",
        "returned_role": "项目组回签原件",
        "review_role": "项目组日期确认稿",
        "result_role": "项目组签字版底稿",
        "report_role": "项目组处理清单",
    },
}


@dataclass(frozen=True)
class ProjectInitializationResult:
    project_root: Path
    manifest_path: Path
    moved_files: int
    resumed: bool = False
    requires_confirmation: bool = False


@dataclass(frozen=True)
class GroupPreparationResult:
    project_root: Path
    group_key: str
    collection_path: Path
    batch_manifest_path: Path
    succeeded: int
    failed: int


@dataclass(frozen=True)
class GroupDateReviewResult:
    project_root: Path
    group_key: str
    returned_original: Path
    review_pdf: Path
    review_manifest_path: Path
    succeeded: int
    attention_pages: tuple[int, ...]


@dataclass(frozen=True)
class VisualCalibrationResult:
    project_root: Path
    group_key: str
    page_number: int
    round_number: int
    accepted: bool
    status: str
    log_path: Path
    reason: str | None = None


@dataclass(frozen=True)
class GroupFinalizationResult:
    project_root: Path
    group_key: str
    result_root: Path
    report_path: Path
    succeeded: int
    failed: int


@dataclass(frozen=True)
class VisualReviewPackage:
    project_root: Path
    group_key: str
    pages: tuple[dict, ...]
    log_path: Path


@dataclass(frozen=True)
class ProjectStatusResult:
    project_root: Path
    status: str
    groups: dict
    requires_confirmation: bool


@dataclass(frozen=True)
class GroupRebuildResult:
    project_root: Path
    group_key: str
    warning: str
    review: GroupDateReviewResult


@dataclass(frozen=True)
class ProjectCleanupResult:
    project_root: Path
    cleaned_path: Path
    groups: tuple[str, ...]


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _validate_project_name(project_name):
    if not isinstance(project_name, str) or not project_name.strip():
        raise ValueError("项目名称不能为空")
    if Path(project_name).name != project_name or any(
        separator in project_name for separator in ("/", "\\")
    ):
        raise ValueError("项目名称不能包含路径分隔符")
    return project_name.strip()


def _is_within(path, parent):
    path = path.resolve()
    parent = parent.resolve()
    return path == parent or parent in path.parents


def _word_files(source):
    return sorted(
        (
            path
            for path in source.rglob("*")
            if path.is_file() and path.suffix.lower() in {".doc", ".docx"}
        ),
        key=lambda path: path.relative_to(source).as_posix(),
    )


def _manifest_path(project_root):
    # 项目状态必须在过程数据清理后仍然存在，才能记录已清理状态并安全续接。
    return project_root / PROJECT_MANIFEST_NAME


def _project_path(project_root, relative_path, role):
    project_root = Path(project_root).resolve()
    candidate = (project_root / relative_path).resolve()
    if candidate == project_root or project_root not in candidate.parents:
        raise ValueError(f"{role}路径超出签署页处理项目：{relative_path}")
    return candidate


def _write_manifest(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _is_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _validate_group_record(project_root, group_key, group):
    if group_key not in GROUP_LAYOUTS or not isinstance(group, dict):
        raise ValueError(f"项目状态包含不受支持的签署文件组：{group_key}")
    layout = GROUP_LAYOUTS[group_key]
    required_strings = ("label", "state", "word_root", "process_root")
    if any(not isinstance(group.get(key), str) or not group[key] for key in required_strings):
        raise ValueError(f"签署文件组结构不完整：{group_key}")
    if group["label"] != layout["label"] or group["state"] not in GROUP_STATES:
        raise ValueError(f"签署文件组状态不受支持：{group_key}")
    word_root = _project_path(project_root, group["word_root"], "原始 Word")
    _project_path(project_root, group["process_root"], "处理数据")
    words = group.get("words")
    if not isinstance(words, list):
        raise ValueError(f"签署文件组缺少稳定的 Word 记录：{group_key}")
    seen_paths = set()
    for record in words:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("path"), str)
            or not _is_sha256(record.get("sha256"))
        ):
            raise ValueError(f"签署文件组包含无效的 Word 记录：{group_key}")
        word_path = _project_path(project_root, record["path"], "原始 Word")
        if word_path != word_root and word_root not in word_path.parents:
            raise ValueError(f"Word 记录超出所属签署文件组：{record['path']}")
        if word_path.suffix.lower() not in {".doc", ".docx"}:
            raise ValueError(f"Word 记录扩展名不受支持：{record['path']}")
        normalized = str(word_path).casefold()
        if normalized in seen_paths:
            raise ValueError(f"签署文件组包含重复 Word 记录：{record['path']}")
        seen_paths.add(normalized)
    artifacts = group.get("artifacts", {})
    if not isinstance(artifacts, dict):
        raise ValueError(f"签署文件组关键成果记录格式无效：{group_key}")
    for key, record in artifacts.items():
        if (
            not isinstance(key, str)
            or not isinstance(record, dict)
            or not isinstance(record.get("path"), str)
            or not _is_sha256(record.get("sha256"))
        ):
            raise ValueError(f"签署文件组关键成果记录无效：{group_key}")
        _project_path(project_root, record["path"], f"关键成果 {key}")


def load_signing_project(project_root):
    project_root = Path(project_root).resolve()
    manifest_path = _manifest_path(project_root)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"不是有效的签署页处理项目：{project_root}") from error
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(payload.get("project_name"), str)
        or not payload["project_name"].strip()
        or not isinstance(payload.get("groups"), dict)
        or not payload["groups"]
    ):
        raise ValueError(f"签署页处理项目状态格式不受支持：{project_root}")
    if project_root.name != f"{payload['project_name']}{PROJECT_SUFFIX}":
        raise ValueError(f"项目目录名与项目状态不一致：{project_root}")
    for group_key, group in payload["groups"].items():
        _validate_group_record(project_root, group_key, group)
    return payload


def _load_group_context(project_root, group_key, *, allowed_states=None):
    project_root = Path(project_root).resolve()
    manifest = load_signing_project(project_root)
    if group_key not in manifest["groups"] or group_key not in GROUP_LAYOUTS:
        raise ValueError(f"项目中不存在签署文件组：{group_key}")
    group = manifest["groups"][group_key]
    if allowed_states is not None and group.get("state") not in allowed_states:
        raise ValueError("签署文件组当前阶段不允许执行此操作")
    word_root = _project_path(project_root, group["word_root"], "原始 Word")
    process_root = _project_path(project_root, group["process_root"], "处理数据")
    return project_root, manifest, group, word_root, process_root


def _record_artifact(project_root, group, key, path):
    path = Path(path).resolve()
    group.setdefault("artifacts", {})[key] = {
        "path": path.relative_to(project_root).as_posix(),
        "sha256": sha256_file(path),
    }


def _required_artifact_keys(group):
    state = group.get("state")
    if group.get("process_data_cleaned"):
        required = {"returned_original", "review_pdf", "report"}
        if not group.get("rebuild_warning"):
            required.add("collection")
        if int(group.get("final_succeeded", 0)) > 0:
            required.add("final_output")
        return required
    required = set()
    if state in {
        "prepared",
        "returned_received",
        "date_review_ready",
        "completed",
        "completed_with_attention",
    }:
        required.add("batch_manifest")
        if not group.get("rebuild_warning"):
            required.add("collection")
    if state in {
        "returned_received",
        "date_review_ready",
        "completed",
        "completed_with_attention",
    }:
        required.add("returned_original")
    if state in {"date_review_ready", "completed", "completed_with_attention"}:
        required.update({"review_pdf", "review_manifest"})
    if state in {"completed", "completed_with_attention"}:
        required.add("report")
        if int(group.get("final_succeeded", 0)) > 0:
            required.add("final_output")
    return required


def _artifact_issues(project_root, group):
    issues = []
    artifacts = group.get("artifacts", {})
    if not isinstance(artifacts, dict):
        return [{"artifact": "artifacts", "change": "invalid_record"}]
    present_keys = set(artifacts)
    for required in _required_artifact_keys(group):
        if required == "final_output":
            if not any(key.startswith("final_output_") for key in present_keys):
                issues.append({"artifact": required, "change": "missing_record"})
        elif required not in present_keys:
            issues.append({"artifact": required, "change": "missing_record"})
    if group.get("state") in {"completed", "completed_with_attention"}:
        recorded_final_paths = {
            record.get("path")
            for key, record in artifacts.items()
            if key.startswith("final_output_") and isinstance(record, dict)
        }
        for final_path in group.get("final_outputs", []):
            if final_path not in recorded_final_paths:
                issues.append(
                    {"artifact": final_path, "change": "missing_final_record"}
                )
    for key, record in artifacts.items():
        if not isinstance(record, dict) or not record.get("path") or not record.get("sha256"):
            issues.append({"artifact": key, "change": "invalid_record"})
            continue
        try:
            path = _project_path(project_root, record["path"], f"关键成果 {key}")
        except ValueError:
            issues.append({"artifact": key, "change": "unsafe_path"})
            continue
        if not path.is_file():
            issues.append({"artifact": key, "change": "missing"})
        elif sha256_file(path) != record["sha256"]:
            issues.append({"artifact": key, "change": "modified"})
    return issues


def _attention_page_sets(group):
    if "date_attention_pages" in group or "visual_attention_pages" in group:
        date_pages = set(group.get("date_attention_pages", []))
        visual_pages = set(group.get("visual_attention_pages", []))
    else:
        # 旧项目无法可靠区分原因，保守地把既有重点页视为日期问题。
        date_pages = set(group.get("attention_pages", []))
        visual_pages = set()
    return date_pages, visual_pages


def _sync_attention_pages(group, date_pages=None, visual_pages=None):
    current_date, current_visual = _attention_page_sets(group)
    date_pages = current_date if date_pages is None else set(date_pages)
    visual_pages = current_visual if visual_pages is None else set(visual_pages)
    group["date_attention_pages"] = sorted(date_pages)
    group["visual_attention_pages"] = sorted(visual_pages)
    group["attention_pages"] = sorted(date_pages | visual_pages)


def _existing_project_result(project_root):
    manifest = load_signing_project(project_root)
    status = inspect_signing_project(project_root)
    moved_files = sum(
        len(group.get("words", []))
        for group in manifest["groups"].values()
        if isinstance(group, dict)
    )
    return ProjectInitializationResult(
        project_root,
        _manifest_path(project_root),
        moved_files,
        resumed=True,
        requires_confirmation=status.requires_confirmation,
    )


def initialize_signing_project(
    project_parent,
    project_name,
    group_sources,
    *,
    confirmed=False,
):
    if not confirmed:
        raise PermissionError("尚未确认移动原始 Word，不能初始化项目")

    project_parent = Path(project_parent).resolve()
    project_name = _validate_project_name(project_name)
    project_root = project_parent / f"{project_name}{PROJECT_SUFFIX}"
    if project_root.exists() and any(project_root.iterdir()):
        return _existing_project_result(project_root)

    if not isinstance(group_sources, dict) or not group_sources:
        raise ValueError("至少需要提供一个签署文件组")
    unknown_groups = sorted(set(group_sources) - set(GROUP_LAYOUTS))
    if unknown_groups:
        raise ValueError(f"不支持的签署文件组：{'、'.join(unknown_groups)}")

    plans = []
    resolved_sources = []
    for group_key, source_value in group_sources.items():
        source = Path(source_value).resolve()
        if not source.is_dir():
            raise ValueError(f"签署文件组目录不存在：{source}")
        if not _is_within(source, project_parent):
            raise ValueError(f"签署文件组必须位于项目文件夹内：{source}")
        if os.path.splitdrive(source)[0].casefold() != os.path.splitdrive(project_root)[
            0
        ].casefold():
            raise ValueError("原始 Word 与签署页处理项目必须位于同一磁盘")
        resolved_sources.append(source)
        layout = GROUP_LAYOUTS[group_key]
        target_root = project_root / layout["label"] / "原始Word"
        word_files = _word_files(source)
        if not word_files:
            raise ValueError(f"签署文件组中没有 Word：{source}")
        for source_file in word_files:
            relative = source_file.relative_to(source)
            plans.append((group_key, source_file, target_root / relative))

    for index, first in enumerate(resolved_sources):
        for second in resolved_sources[index + 1 :]:
            if _is_within(first, second) or _is_within(second, first):
                raise ValueError("不同签署文件组的输入目录不能互相包含")

    targets = [target for _, _, target in plans]
    if len({str(path).casefold() for path in targets}) != len(targets):
        raise ValueError("原始 Word 的目标路径发生冲突")
    if any(path.exists() for path in targets):
        raise FileExistsError("原始 Word 的目标位置必须为空")

    project_root.mkdir(parents=True, exist_ok=True)
    groups = {}
    for group_key in group_sources:
        layout = GROUP_LAYOUTS[group_key]
        visible_root = project_root / layout["label"]
        word_root = visible_root / "原始Word"
        result_root = visible_root / f"{project_name}_{layout['result_role']}"
        process_root = project_root / PROCESS_DATA_NAME / layout["label"]
        word_root.mkdir(parents=True, exist_ok=True)
        result_root.mkdir(parents=True, exist_ok=True)
        (process_root / "完整底稿PDF").mkdir(parents=True, exist_ok=True)
        groups[group_key] = {
            "label": layout["label"],
            "state": "initializing",
            "word_root": word_root.relative_to(project_root).as_posix(),
            "process_root": process_root.relative_to(project_root).as_posix(),
            "words": [],
        }

    manifest = {
        "version": 1,
        "project_name": project_name,
        "status": "initializing",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "groups": groups,
    }
    manifest_path = _manifest_path(project_root)
    _write_manifest(manifest_path, manifest)

    moved = 0
    try:
        for group_key, source_file, target_file in plans:
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_file), str(target_file))
            moved += 1
            groups[group_key]["words"].append(
                {
                    "path": target_file.relative_to(project_root).as_posix(),
                    "sha256": sha256_file(target_file),
                }
            )
            _write_manifest(manifest_path, manifest)
    except Exception as error:
        manifest["status"] = "initialization_failed"
        manifest["updated_at"] = _utc_now()
        manifest["error"] = str(error) or error.__class__.__name__
        _write_manifest(manifest_path, manifest)
        raise ValueError(
            f"移动原始 Word 失败；已移动 {moved} 个文件，请检查项目状态：{error}"
        ) from error

    for group in groups.values():
        group["words"].sort(key=lambda item: item["path"])
        group["state"] = "initialized"
    manifest["status"] = "initialized"
    manifest["updated_at"] = _utc_now()
    _write_manifest(manifest_path, manifest)
    return ProjectInitializationResult(project_root, manifest_path, moved)


def _group_artifact_path(project_root, project_name, group_key, role):
    layout = GROUP_LAYOUTS[group_key]
    return (
        project_root
        / layout["label"]
        / f"{project_name}_{layout[role]}"
    )


def _prepare_reuse_batch(
    project_root,
    word_root,
    process_root,
    collection_path,
    group_key,
    converter,
    preflight_result,
    *,
    write_collection=True,
):
    from pypdf import PdfReader, PdfWriter

    from .seal_page_titles import extract_text_title
    from .titles import normalize_title

    owned_converter = converter is None
    if preflight_result is not None or owned_converter:
        from .preflight import require_preflight_ready, run_preflight

        preflight_result = preflight_result or run_preflight()
        require_preflight_ready(preflight_result)
    if owned_converter:
        from .word_conversion import create_platform_word_pdf_converter

        converter = create_platform_word_pdf_converter()

    pdf_root = process_root / "完整底稿PDF"
    if pdf_root.exists():
        shutil.rmtree(pdf_root)
    pdf_root.mkdir(parents=True)
    words = _word_files(word_root)
    groups_by_hash = {}
    unreadable = []
    for word in words:
        relative = word.relative_to(word_root)
        try:
            digest = sha256_file(word)
        except Exception as error:
            unreadable.append(
                {
                    "working_paper_id": relative.as_posix(),
                    "working_paper_path": relative.as_posix(),
                    "status": "failed",
                    "error": f"无法读取底稿文件：{error}",
                }
            )
            continue
        groups_by_hash.setdefault(digest, []).append((word, relative))

    items_by_id = {
        item["working_paper_id"]: item for item in unreadable
    }
    reuse_groups = []
    collection = PdfWriter()
    try:
        for digest, members in groups_by_hash.items():
            representative = None
            representative_pdf = None
            representative_reader = None
            errors = []
            for candidate, relative in members:
                candidate_pdf = (
                    pdf_root / relative.parent / f"{relative.name}.pdf"
                )
                candidate_pdf.parent.mkdir(parents=True, exist_ok=True)
                try:
                    converter.convert(candidate, candidate_pdf)
                    reader = PdfReader(str(candidate_pdf))
                    if not reader.pages:
                        raise ValueError("转换后的 PDF 没有页面")
                    representative = (candidate, relative)
                    representative_pdf = candidate_pdf
                    representative_reader = reader
                    break
                except Exception as error:
                    candidate_pdf.unlink(missing_ok=True)
                    errors.append(
                        f"{relative.as_posix()}：{str(error) or error.__class__.__name__}"
                    )

            if representative is None:
                reason = "；".join(errors) or "同一重复组的 Word 均转换失败"
                for _, relative in members:
                    items_by_id[relative.as_posix()] = {
                        "working_paper_id": relative.as_posix(),
                        "working_paper_path": relative.as_posix(),
                        "working_paper_sha256": digest,
                        "status": "failed",
                        "error": reason,
                    }
                continue

            representative_id = representative[1].as_posix()
            title = extract_text_title(
                representative_reader.pages[-1].extract_text() or "",
                fallback=representative[0].stem,
            )
            page_count = len(representative_reader.pages)
            collection.add_page(representative_reader.pages[-1])
            seal_page = len(collection.pages)
            member_ids = []
            for _, relative in members:
                working_paper_id = relative.as_posix()
                member_pdf = pdf_root / relative.parent / f"{relative.name}.pdf"
                member_pdf.parent.mkdir(parents=True, exist_ok=True)
                if member_pdf != representative_pdf:
                    shutil.copyfile(representative_pdf, member_pdf)
                items_by_id[working_paper_id] = {
                    "working_paper_id": working_paper_id,
                    "working_paper_path": working_paper_id,
                    "status": ITEM_STATUS_READY,
                    "title": title,
                    "normalized_title": normalize_title(title),
                    "converted_pdf": member_pdf.relative_to(
                        process_root
                    ).as_posix(),
                    "pdf_page_count": page_count,
                    "working_paper_sha256": digest,
                    "pdf_sha256": sha256_file(member_pdf),
                    "seal_page": seal_page,
                    "reuse_of": representative_id,
                    "duplicate_group_sha256": digest,
                }
                member_ids.append(working_paper_id)
            reuse_groups.append(
                {
                    "sha256": digest,
                    "representative": representative_id,
                    "members": member_ids,
                    "seal_page": seal_page,
                }
            )
    finally:
        if owned_converter:
            converter.close()

    if write_collection:
        collection_path.parent.mkdir(parents=True, exist_ok=True)
        collection_path.unlink(missing_ok=True)
        with collection_path.open("wb") as output:
            collection.write(output)
    items = [
        items_by_id[path.relative_to(word_root).as_posix()]
        for path in words
        if path.relative_to(word_root).as_posix() in items_by_id
    ]
    batch_manifest = {
        "version": 1,
        "working_paper_root": str(word_root.resolve()),
        "seal_pages": (
            collection_path.relative_to(project_root).as_posix()
            if write_collection
            else ""
        ),
        "duplicate_policy": "reuse",
        "excluded_duplicates": [],
        "reuse_groups": reuse_groups,
        "group_key": group_key,
        "items": items,
    }
    batch_manifest_path = process_root / "处理批次清单_manifest.json"
    _write_manifest(batch_manifest_path, batch_manifest)
    return {
        "manifest": batch_manifest,
        "manifest_path": batch_manifest_path,
        "succeeded": len(collection.pages),
        "failed": sum(item.get("status") == "failed" for item in items),
    }


def inspect_signing_project(project_root):
    project_root = Path(project_root).resolve()
    manifest = load_signing_project(project_root)
    groups = {}
    requires_confirmation = False
    for group_key, group in manifest["groups"].items():
        word_root = _project_path(project_root, group["word_root"], "原始 Word")
        expected = {
            item.get("path"): item.get("sha256")
            for item in group.get("words", [])
            if isinstance(item, dict)
        }
        current = {}
        for word in _word_files(word_root):
            project_relative = word.relative_to(project_root).as_posix()
            current[project_relative] = sha256_file(word)
        changes = []
        for path in sorted(set(expected) | set(current)):
            if path not in current:
                changes.append({"path": path, "change": "missing"})
            elif path not in expected:
                changes.append({"path": path, "change": "added"})
            elif expected[path] != current[path]:
                changes.append({"path": path, "change": "modified"})
        artifact_changes = _artifact_issues(project_root, group)
        if changes and group.get("state") != "initialized":
            state = "word_changes_detected"
            requires_confirmation = True
        elif artifact_changes:
            state = "artifact_changes_detected"
            requires_confirmation = True
        else:
            state = group.get("state", "unknown")
        next_actions = {
            "initialized": "prepare",
            "prepared": "receive_returned_pages",
            "returned_received": "create_date_review",
            "date_review_ready": "visual_review_or_confirm",
            "completed": "wait_for_other_group_or_cleanup",
            "completed_with_attention": "confirm_attention_before_cleanup",
            "word_changes_detected": "confirm_rebuild",
            "artifact_changes_detected": "inspect_or_rebuild",
        }
        groups[group_key] = {
            "label": group.get("label", group_key),
            "state": state,
            "stored_state": group.get("state"),
            "word_changes": changes,
            "artifact_changes": artifact_changes,
            "next_action": next_actions.get(state, "inspect_manually"),
        }
    return ProjectStatusResult(
        project_root,
        manifest.get("status", "unknown"),
        groups,
        requires_confirmation,
    )


def rebuild_signing_group(
    project_root,
    group_key,
    *,
    confirmed=False,
    returned_pdf=None,
    signing_date=None,
    converter=None,
    preflight_result=None,
    ocr_engine=None,
    font_path=None,
):
    if not confirmed:
        raise PermissionError("尚未确认补建处理数据，不能继续")
    project_root, manifest, group, word_root, process_root = _load_group_context(
        project_root, group_key
    )
    returned_original = _group_artifact_path(
        project_root,
        manifest["project_name"],
        group_key,
        "returned_role",
    ).with_suffix(".pdf")
    if returned_pdf is not None:
        supplied = Path(returned_pdf).resolve()
        if supplied != returned_original.resolve():
            if returned_original.exists():
                raise FileExistsError(f"签署回页原件已存在：{returned_original}")
            returned_original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(supplied), str(returned_original))
    if not returned_original.is_file():
        raise ValueError("补建处理数据需要当前原始 Word 和对应签署回页原件")

    unused_collection_path = (
        process_root / ".补建不输出签署页合集_rebuild.pdf"
    )
    rebuild = _prepare_reuse_batch(
        project_root,
        word_root,
        process_root,
        unused_collection_path,
        group_key,
        converter,
        preflight_result,
        write_collection=False,
    )
    unused_collection_path.unlink(missing_ok=True)
    words = []
    for word in _word_files(word_root):
        words.append(
            {
                "path": word.relative_to(project_root).as_posix(),
                "sha256": sha256_file(word),
            }
        )
    group.update(
        {
            "state": "prepared",
            "words": words,
            "duplicate_policy": "reuse",
            "batch_manifest": rebuild["manifest_path"].relative_to(
                project_root
            ).as_posix(),
            "returned_original": returned_original.relative_to(
                project_root
            ).as_posix(),
            "returned_sha256": sha256_file(returned_original),
            "rebuild_warning": (
                "当前 Word 与当初送签版本的版式一致性无法得到证明；"
                "签署回页必须重新匹配并重新确认日期"
            ),
        }
    )
    group["artifacts"] = {}
    _record_artifact(project_root, group, "batch_manifest", rebuild["manifest_path"])
    _record_artifact(project_root, group, "returned_original", returned_original)
    for artifact in (
        process_root / "模型定位调整记录_visual-calibration.json",
    ):
        artifact.unlink(missing_ok=True)
    visual_temp = process_root / ".视觉复核暂存_visual"
    if visual_temp.exists():
        shutil.rmtree(visual_temp)
    manifest["status"] = "in_progress"
    manifest["updated_at"] = _utc_now()
    _write_manifest(_manifest_path(project_root), manifest)
    review = create_group_date_review(
        project_root,
        group_key,
        returned_original,
        signing_date=signing_date,
        ocr_engine=ocr_engine,
        font_path=font_path,
    )
    return GroupRebuildResult(
        project_root,
        group_key,
        group["rebuild_warning"],
        review,
    )


def _send_to_system_recycle_bin(path):
    path = Path(path).resolve()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", wintypes.WORD),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        operation = SHFILEOPSTRUCTW()
        operation.wFunc = 3
        operation.pFrom = f"{path}\0\0"
        operation.fFlags = 0x40 | 0x10 | 0x04 | 0x400
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
        if result != 0 or operation.fAnyOperationsAborted:
            raise OSError(f"无法把处理数据移入系统回收站，错误码：{result}")
        return
    if sys.platform == "darwin":
        trash_root = Path.home() / ".Trash"
        trash_root.mkdir(parents=True, exist_ok=True)
        candidate = trash_root / path.name
        index = 1
        while candidate.exists():
            candidate = trash_root / f"{path.name} {index}"
            index += 1
        shutil.move(str(path), str(candidate))
        return
    raise RuntimeError("当前平台不支持把处理数据移入系统回收站")


def clean_project_processing_data(
    project_root,
    *,
    confirmed=False,
    accept_attention=False,
    trash_handler=None,
):
    if not confirmed:
        raise PermissionError("尚未取得独立清理确认，不能清理处理数据")
    project_root = Path(project_root).resolve()
    manifest = load_signing_project(project_root)
    completed_states = {"completed", "completed_with_attention"}
    incomplete = [
        group.get("label", key)
        for key, group in manifest["groups"].items()
        if group.get("state") not in completed_states
    ]
    if incomplete:
        raise PermissionError(
            f"以下签署文件组尚未完成，不能清理：{'、'.join(incomplete)}"
        )
    attention = [
        group.get("label", key)
        for key, group in manifest["groups"].items()
        if group.get("state") == "completed_with_attention"
        or group.get("attention_pages")
        or group.get("attention_items")
    ]
    if attention and not accept_attention:
        raise PermissionError(
            f"以下签署文件组仍有需关注项：{'、'.join(attention)}；"
            "请明确接受后再清理"
        )
    process_root = project_root / PROCESS_DATA_NAME
    if not process_root.is_dir():
        raise ValueError("处理数据目录不存在或已经清理")
    handler = trash_handler or _send_to_system_recycle_bin
    handler(process_root)
    if process_root.exists():
        raise OSError("处理数据未成功移入系统回收站")
    visible_artifact_keys = {
        "collection",
        "returned_original",
        "review_pdf",
        "report",
    }
    for group in manifest["groups"].values():
        group["artifacts"] = {
            key: value
            for key, value in group.get("artifacts", {}).items()
            if key in visible_artifact_keys or key.startswith("final_output_")
        }
        group["process_data_cleaned"] = True
    manifest["status"] = "cleaned_with_attention" if attention else "cleaned"
    manifest["processing_data_cleaned_at"] = _utc_now()
    manifest["cleanup_confirmation"] = {
        "confirmed": True,
        "accepted_attention": bool(accept_attention),
        "incomplete_groups": [],
        "attention_groups": attention,
    }
    manifest["updated_at"] = _utc_now()
    _write_manifest(_manifest_path(project_root), manifest)
    return ProjectCleanupResult(
        project_root,
        process_root,
        tuple(manifest["groups"]),
    )


def prepare_signing_group(
    project_root,
    group_key,
    *,
    duplicate_policy,
    converter=None,
    preflight_result=None,
):
    project_root, manifest, group, word_root, process_root = _load_group_context(
        project_root, group_key
    )
    if duplicate_policy not in {"individual", "reuse"}:
        raise ValueError("重复文件策略必须是 individual 或 reuse")
    if str(manifest.get("status", "")).startswith("cleaned"):
        raise ValueError("项目过程数据已经清理，不能重新生成待签署合集")
    project_name = manifest["project_name"]
    collection_path = _group_artifact_path(
        project_root,
        project_name,
        group_key,
        "collection_role",
    ).with_suffix(".pdf")
    if group.get("state") != "initialized":
        issues = _artifact_issues(project_root, group)
        if not issues and group.get("state") in {
            "prepared",
            "returned_received",
            "date_review_ready",
            "completed",
            "completed_with_attention",
        }:
            batch_manifest_path = _project_path(
                project_root, group["batch_manifest"], "处理批次清单"
            )
            items = _load_json(batch_manifest_path, "处理批次清单").get("items", [])
            succeeded = sum(item.get("status") == ITEM_STATUS_READY for item in items)
            return GroupPreparationResult(
                project_root,
                group_key,
                collection_path,
                batch_manifest_path,
                succeeded,
                len(items) - succeeded,
            )
        raise ValueError("当前阶段不能重新生成待签署合集；请先检查项目状态")
    if duplicate_policy == "reuse":
        reuse_result = _prepare_reuse_batch(
            project_root,
            word_root,
            process_root,
            collection_path,
            group_key,
            converter,
            preflight_result,
        )
        batch_manifest_path = reuse_result["manifest_path"]
        succeeded = reuse_result["succeeded"]
        failed = reuse_result["failed"]
    else:
        succeeded = failed = None
    staging_root = process_root / ".准备暂存_staging"
    if staging_root.exists():
        shutil.rmtree(staging_root)

    if duplicate_policy == "individual":
        from .processing_batch import prepare_processing_batch

        try:
            result = prepare_processing_batch(
                word_root,
                staging_root,
                converter=converter,
                preflight_result=preflight_result,
                duplicate_policy="keep",
            )
            pdf_root = process_root / "完整底稿PDF"
            if pdf_root.exists():
                shutil.rmtree(pdf_root)
            shutil.move(str(staging_root / "pdfs"), str(pdf_root))

            batch_manifest = json.loads(
                (staging_root / "manifest.json").read_text(encoding="utf-8")
            )
            for item in batch_manifest.get("items", []):
                converted_pdf = item.get("converted_pdf")
                if isinstance(converted_pdf, str) and converted_pdf.startswith("pdfs/"):
                    item["converted_pdf"] = (
                        "完整底稿PDF/" + converted_pdf.removeprefix("pdfs/")
                    )
            collection_path.unlink(missing_ok=True)
            shutil.move(str(staging_root / "seal-pages.pdf"), str(collection_path))
            batch_manifest["seal_pages"] = collection_path.relative_to(
                project_root
            ).as_posix()
            batch_manifest["group_key"] = group_key
            batch_manifest["duplicate_policy"] = duplicate_policy
            batch_manifest_path = process_root / "处理批次清单_manifest.json"
            _write_manifest(batch_manifest_path, batch_manifest)
            succeeded = result.succeeded
            failed = result.failed
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root)

    group.update(
        {
            "state": "prepared",
            "duplicate_policy": duplicate_policy,
            "collection": collection_path.relative_to(project_root).as_posix(),
            "batch_manifest": batch_manifest_path.relative_to(
                project_root
            ).as_posix(),
        }
    )
    _record_artifact(project_root, group, "collection", collection_path)
    _record_artifact(project_root, group, "batch_manifest", batch_manifest_path)
    manifest["updated_at"] = _utc_now()
    _write_manifest(_manifest_path(project_root), manifest)
    return GroupPreparationResult(
        project_root,
        group_key,
        collection_path,
        batch_manifest_path,
        succeeded,
        failed,
    )


def create_group_date_review(
    project_root,
    group_key,
    returned_pdf,
    *,
    signing_date=None,
    ocr_engine=None,
    font_path=None,
):
    project_root, manifest, group, _, process_root = _load_group_context(
        project_root, group_key
    )
    if group.get("state") == "date_review_ready" and not _artifact_issues(
        project_root, group
    ):
        review_pdf = _project_path(project_root, group["review_pdf"], "日期确认稿")
        review_manifest_path = _project_path(
            project_root, group["review_manifest"], "日期确认清单"
        )
        returned_original = _project_path(
            project_root, group["returned_original"], "签署回页原件"
        )
        attention_pages = tuple(group.get("attention_pages", []))
        return GroupDateReviewResult(
            project_root,
            group_key,
            returned_original,
            review_pdf,
            review_manifest_path,
            int(group.get("date_review_succeeded", 0)),
            attention_pages,
        )
    if group.get("state") not in {
        "prepared",
        "returned_received",
    }:
        raise ValueError("签署文件组尚未生成待签署材料")

    if signing_date is not None:
        from .date_completion import ensure_times_new_roman_font

        ensure_times_new_roman_font(font_path)

    project_name = manifest["project_name"]
    returned_original = _group_artifact_path(
        project_root,
        project_name,
        group_key,
        "returned_role",
    ).with_suffix(".pdf")
    source_returned = Path(returned_pdf).resolve()
    if source_returned != returned_original.resolve():
        if returned_original.exists():
            raise FileExistsError(f"签署回页原件已存在：{returned_original}")
        returned_original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_returned), str(returned_original))
    if not returned_original.is_file():
        raise ValueError(f"签署回页原件不存在：{returned_original}")

    group["state"] = "returned_received"
    group["returned_original"] = returned_original.relative_to(
        project_root
    ).as_posix()
    group["returned_sha256"] = sha256_file(returned_original)
    manifest["updated_at"] = _utc_now()
    _write_manifest(_manifest_path(project_root), manifest)

    batch_manifest_path = _project_path(
        project_root,
        group["batch_manifest"],
        "处理批次清单",
    )
    compatibility_manifest = process_root / "manifest.json"
    staging_root = process_root / ".日期确认暂存_review"
    if staging_root.exists():
        shutil.rmtree(staging_root)
    compatibility_manifest.unlink(missing_ok=True)
    shutil.copyfile(batch_manifest_path, compatibility_manifest)

    from .review_workflow import create_date_review

    try:
        result = create_date_review(
            process_root,
            returned_original,
            staging_root,
            ocr_engine=ocr_engine,
            signing_date=signing_date,
        )
        review_pdf = _group_artifact_path(
            project_root,
            project_name,
            group_key,
            "review_role",
        ).with_suffix(".pdf")
        review_pdf.unlink(missing_ok=True)
        shutil.move(str(result.review_pdf), str(review_pdf))
        review_manifest_path = process_root / "日期确认清单_review-manifest.json"
        review_manifest_path.unlink(missing_ok=True)
        shutil.move(str(result.manifest_path), str(review_manifest_path))
    finally:
        compatibility_manifest.unlink(missing_ok=True)
        if staging_root.exists():
            shutil.rmtree(staging_root)

    date_attention_pages = {
        item.returned_page
        for item in result.items
        if item.returned_page is not None and item.date_status in {"partial", "failed"}
    }
    visual_attention_pages = {
        item.returned_page
        for item in result.items
        if item.returned_page is not None
        and item.date_status == "filled_needs_review"
    }
    attention_items = sorted(
        {
            item.working_paper_id
            for item in result.items
            if item.status != "review_ready"
            or item.date_status in {"partial", "failed"}
        }
    )
    attention_pages = tuple(sorted(date_attention_pages | visual_attention_pages))
    group.update(
        {
            "state": "date_review_ready",
            "review_pdf": review_pdf.relative_to(project_root).as_posix(),
            "review_manifest": review_manifest_path.relative_to(
                project_root
            ).as_posix(),
            "signing_date": signing_date.isoformat() if signing_date else None,
            "visual_review": "pending",
            "date_review_succeeded": result.succeeded,
            "attention_items": attention_items,
        }
    )
    _sync_attention_pages(group, date_attention_pages, visual_attention_pages)
    _record_artifact(project_root, group, "returned_original", returned_original)
    _record_artifact(project_root, group, "review_pdf", review_pdf)
    _record_artifact(project_root, group, "review_manifest", review_manifest_path)
    manifest["updated_at"] = _utc_now()
    _write_manifest(_manifest_path(project_root), manifest)
    return GroupDateReviewResult(
        project_root,
        group_key,
        returned_original,
        review_pdf,
        review_manifest_path,
        result.succeeded,
        attention_pages,
    )


def _load_json(path, role):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取{role}：{path}") from error


def _placement_from_record(record):
    from .date_layout import Box, DatePlacement

    try:
        box_record = record["box"]
        return DatePlacement(
            component=str(record["component"]),
            value=str(record["value"]),
            box=Box(
                float(box_record["x0"]),
                float(box_record["y0"]),
                float(box_record["x1"]),
                float(box_record["y1"]),
            ),
            baseline=float(record["baseline"]),
            scale=float(record["scale"]),
            source=str(record.get("source", "requested")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("日期确认清单包含无效落字坐标") from error


def _proposed_placements(initial_placements, proposals, page_width, page_height):
    if not isinstance(proposals, dict):
        raise ValueError("模型定位建议必须按日期组成部分提供坐标")
    expected = {placement.component for placement in initial_placements}
    if set(proposals) != expected:
        raise ValueError("模型定位建议只能包含脚本新增的日期组成部分")

    proposed = []
    for initial in initial_placements:
        coordinates = proposals[initial.component]
        if not isinstance(coordinates, dict) or set(coordinates) != {"x", "y"}:
            raise ValueError("模型定位建议只能包含 x、y 坐标")
        x = coordinates["x"]
        y = coordinates["y"]
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
            or not math.isfinite(float(x))
            or not math.isfinite(float(y))
        ):
            raise ValueError("模型定位坐标必须是有限数字")
        x = float(x)
        y = float(y)
        width = initial.box.x1 - initial.box.x0
        height = initial.box.y1 - initial.box.y0
        movement_limit = max(24.0, height * 4.0)
        if (
            abs(x - initial.box.x0) > movement_limit
            or abs(y - initial.box.y0) > movement_limit
        ):
            raise ValueError("模型定位坐标超出允许的日期调整区域")
        if x < 0 or y < 0 or x + width > page_width or y + height > page_height:
            raise ValueError("模型定位坐标超出页面范围")
        from .date_layout import Box, DatePlacement

        proposed.append(
            DatePlacement(
                initial.component,
                initial.value,
                Box(x, y, x + width, y + height),
                y + height * 0.8,
                initial.scale,
                initial.source,
            )
        )
    return tuple(proposed)


def _previous_approved_proposal_boxes(initial_placements, page_log):
    from .date_layout import Box

    boxes = []
    by_component = {placement.component: placement for placement in initial_placements}
    for round_record in page_log.get("rounds", []):
        if round_record.get("hard_checks") != "passed":
            continue
        proposal = round_record.get("proposal", {})
        for component, coordinates in proposal.items():
            initial = by_component.get(component)
            if initial is None or not isinstance(coordinates, dict):
                continue
            x = coordinates.get("x")
            y = coordinates.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            width = initial.box.x1 - initial.box.x0
            height = initial.box.y1 - initial.box.y0
            boxes.append(Box(float(x), float(y), float(x) + width, float(y) + height))
    return boxes


def _validate_placement_collisions(returned_pdf, page_index, placements):
    from .pdf_ops import extract_text_boxes

    occupied = tuple(text_box.box for text_box in extract_text_boxes(returned_pdf, page_index))
    for placement in placements:
        if any(placement.box.intersects(box) for box in occupied):
            raise ValueError(f"{placement.component} 的模型定位与原页文字发生碰撞")
    for index, first in enumerate(placements):
        if any(first.box.intersects(second.box) for second in placements[index + 1 :]):
            raise ValueError("模型定位后的日期数字互相重叠")


def _replace_review_page(
    review_pdf,
    page_number,
    replacement_page,
    *,
    mutable_boxes=(),
):
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(review_pdf))
    if page_number < 1 or page_number > len(reader.pages):
        raise ValueError(f"日期确认稿页码超出范围：{page_number}")
    original_sizes = [
        (float(page.mediabox.width), float(page.mediabox.height))
        for page in reader.pages
    ]
    replacement_size = (
        float(replacement_page.mediabox.width),
        float(replacement_page.mediabox.height),
    )
    if replacement_size != original_sizes[page_number - 1]:
        raise ValueError("模型调整改变了日期确认稿页面尺寸")
    writer = PdfWriter()
    for index, page in enumerate(reader.pages, start=1):
        writer.add_page(replacement_page if index == page_number else page)
    temporary = Path(review_pdf).with_suffix(".pdf.visual.tmp")
    try:
        with temporary.open("wb") as output:
            writer.write(output)
        verified = PdfReader(str(temporary))
        if len(verified.pages) != len(reader.pages):
            raise ValueError("模型调整改变了日期确认稿页数")
        verified_sizes = [
            (float(page.mediabox.width), float(page.mediabox.height))
            for page in verified.pages
        ]
        if verified_sizes != original_sizes:
            raise ValueError("模型调整改变了日期确认稿页面顺序或尺寸")
        from PIL import Image, ImageChops, ImageDraw

        from .pdf_ops import render_pdf_page

        before = render_pdf_page(review_pdf, page_number - 1).convert("RGB")
        rendered = render_pdf_page(temporary, page_number - 1)
        if rendered.width < 1 or rendered.height < 1:
            raise ValueError("模型调整后的日期确认稿无法正常渲染")
        rendered = rendered.convert("RGB")
        if mutable_boxes:
            if rendered.size != before.size:
                raise ValueError("模型调整改变了页面渲染尺寸")
            scale_x = rendered.width / original_sizes[page_number - 1][0]
            scale_y = rendered.height / original_sizes[page_number - 1][1]
            outside_mask = Image.new("L", rendered.size, 255)
            drawer = ImageDraw.Draw(outside_mask)
            for box in mutable_boxes:
                drawer.rectangle(
                    (
                        max(0, int(box.x0 * scale_x) - 4),
                        max(0, int(rendered.height - box.y1 * scale_y) - 4),
                        min(rendered.width, int(box.x1 * scale_x) + 4),
                        min(rendered.height, int(rendered.height - box.y0 * scale_y) + 4),
                    ),
                    fill=0,
                )
            difference = ImageChops.difference(before, rendered)
            outside_difference = Image.composite(
                difference,
                Image.new("RGB", rendered.size),
                outside_mask,
            )
            if outside_difference.getbbox() is not None:
                raise ValueError("模型调整改变了日期区域以外的页面内容")
        temporary.replace(review_pdf)
    finally:
        temporary.unlink(missing_ok=True)


def _visual_log(path, group_key):
    if path.exists():
        payload = _load_json(path, "模型定位调整记录")
        if isinstance(payload, dict) and isinstance(payload.get("pages"), dict):
            return payload
    return {"version": 1, "group_key": group_key, "pages": {}}


def apply_visual_date_calibration(
    project_root,
    group_key,
    page_number,
    proposals,
    *,
    model_decision,
    font_path=None,
):
    project_root, manifest, group, _, process_root = _load_group_context(
        project_root, group_key, allowed_states={"date_review_ready"}
    )
    if not isinstance(page_number, int) or isinstance(page_number, bool):
        raise ValueError("日期确认稿页码必须是整数")
    if model_decision not in MODEL_DECISIONS:
        raise ValueError(
            "模型视觉结论必须是 approved、needs_adjustment 或 unable_to_determine"
        )

    review_manifest_path = _project_path(
        project_root, group["review_manifest"], "日期确认清单"
    )
    review_manifest = _load_json(review_manifest_path, "日期确认清单")
    placement_records = review_manifest.get("date_placements", {}).get(
        str(page_number)
    )
    if not isinstance(placement_records, list) or not placement_records:
        raise ValueError(f"第 {page_number} 页没有脚本新增日期，不能调整")
    initial_placements = tuple(
        _placement_from_record(record) for record in placement_records
    )

    returned_original = _project_path(
        project_root, group["returned_original"], "签署回页原件"
    )
    review_pdf = _project_path(project_root, group["review_pdf"], "日期确认稿")
    from pypdf import PdfReader

    returned_reader = PdfReader(str(returned_original))
    if page_number < 1 or page_number > len(returned_reader.pages):
        raise ValueError(f"签署回页原件页码超出范围：{page_number}")
    source_page = returned_reader.pages[page_number - 1]
    log_path = process_root / "模型定位调整记录_visual-calibration.json"
    log = _visual_log(log_path, group_key)
    page_log = log["pages"].setdefault(
        str(page_number), {"status": "pending", "rounds": []}
    )
    if page_log.get("status") in {
        "approved",
        "fallback_script",
        "unable_to_determine",
    }:
        raise ValueError(f"第 {page_number} 页视觉校准已经结束")
    round_number = len(page_log["rounds"]) + 1
    if round_number > 5:
        raise ValueError(f"第 {page_number} 页已达到五轮视觉校准上限")

    from .date_completion import apply_date_placements_to_original

    def restore_script_page():
        initial_page = apply_date_placements_to_original(
            returned_original,
            page_number - 1,
            initial_placements,
            font_path=font_path,
        )
        _replace_review_page(review_pdf, page_number, initial_page)

    if model_decision == "unable_to_determine":
        restore_script_page()
        reason = "模型无法可靠判断日期位置，已保留脚本初始页并转人工复核"
        page_log["rounds"].append(
            {
                "round": round_number,
                "proposal": proposals,
                "hard_checks": "not_run",
                "model_decision": model_decision,
                "reason": reason,
            }
        )
        page_log["status"] = model_decision
        page_log["reason"] = reason
        group["visual_review"] = "manual_attention"
        date_pages, visual_pages = _attention_page_sets(group)
        visual_pages.add(page_number)
        _sync_attention_pages(group, date_pages, visual_pages)
        visual_temp = process_root / ".视觉复核暂存_visual"
        if visual_temp.exists():
            shutil.rmtree(visual_temp)
        group["visual_calibration_log"] = log_path.relative_to(
            project_root
        ).as_posix()
        _write_manifest(log_path, log)
        _record_artifact(project_root, group, "review_pdf", review_pdf)
        manifest["updated_at"] = _utc_now()
        _write_manifest(_manifest_path(project_root), manifest)
        return VisualCalibrationResult(
            project_root,
            group_key,
            page_number,
            round_number,
            False,
            model_decision,
            log_path,
            reason,
        )

    try:
        placements = _proposed_placements(
            initial_placements,
            proposals,
            float(source_page.mediabox.width),
            float(source_page.mediabox.height),
        )
        _validate_placement_collisions(
            returned_original,
            page_number - 1,
            placements,
        )
    except ValueError as error:
        reason = str(error)
        restore_script_page()
        page_log["rounds"].append(
            {
                "round": round_number,
                "proposal": proposals,
                "hard_checks": "failed",
                "model_decision": model_decision,
                "reason": reason,
            }
        )
        status = "rejected"
        if round_number == 5:
            status = "fallback_script"
            reason = f"{reason}；五轮视觉校准后仍未确认，已恢复脚本初始位置"
        page_log["status"] = status
        page_log["reason"] = reason
        _write_manifest(log_path, log)
        group["visual_review"] = (
            "manual_attention" if status == "fallback_script" else "in_progress"
        )
        if status == "fallback_script":
            visual_temp = process_root / ".视觉复核暂存_visual"
            if visual_temp.exists():
                shutil.rmtree(visual_temp)
        date_pages, visual_pages = _attention_page_sets(group)
        visual_pages.add(page_number)
        _sync_attention_pages(group, date_pages, visual_pages)
        group["visual_calibration_log"] = log_path.relative_to(
            project_root
        ).as_posix()
        _record_artifact(project_root, group, "review_pdf", review_pdf)
        manifest["updated_at"] = _utc_now()
        _write_manifest(_manifest_path(project_root), manifest)
        return VisualCalibrationResult(
            project_root,
            group_key,
            page_number,
            round_number,
            False,
            status,
            log_path,
            reason,
        )

    replacement_page = apply_date_placements_to_original(
        returned_original,
        page_number - 1,
        placements,
        font_path=font_path,
    )
    _replace_review_page(
        review_pdf,
        page_number,
        replacement_page,
        mutable_boxes=tuple(
            [placement.box for placement in initial_placements]
            + [placement.box for placement in placements]
            + _previous_approved_proposal_boxes(initial_placements, page_log)
        ),
    )
    status = model_decision
    page_log["rounds"].append(
        {
            "round": round_number,
            "proposal": proposals,
            "hard_checks": "passed",
            "model_decision": model_decision,
        }
    )
    page_log["status"] = status
    if round_number == 5 and model_decision == "needs_adjustment":
        restore_script_page()
        status = "fallback_script"
        page_log["status"] = status
        page_log["reason"] = "五轮视觉校准后仍未确认，已恢复脚本初始位置"
    _write_manifest(log_path, log)

    placement_pages = {
        page
        for page, records in review_manifest.get("date_placements", {}).items()
        if records
    }
    approved_pages = {
        page
        for page, record in log["pages"].items()
        if record.get("status") == "approved"
    }
    manual_pages = {
        page
        for page, record in log["pages"].items()
        if record.get("status") in {"fallback_script", "unable_to_determine"}
    }
    group["visual_review"] = "approved" if placement_pages <= approved_pages else "in_progress"
    date_pages, visual_pages = _attention_page_sets(group)
    if status == "approved":
        visual_pages.discard(page_number)
    if status == "fallback_script":
        visual_pages.add(page_number)
    _sync_attention_pages(group, date_pages, visual_pages)
    if manual_pages:
        group["visual_review"] = "manual_attention"
    if group["visual_review"] in {"approved", "manual_attention"}:
        visual_temp = process_root / ".视觉复核暂存_visual"
        if visual_temp.exists():
            shutil.rmtree(visual_temp)
    group["visual_calibration_log"] = log_path.relative_to(
        project_root
    ).as_posix()
    _record_artifact(project_root, group, "review_pdf", review_pdf)
    manifest["updated_at"] = _utc_now()
    _write_manifest(_manifest_path(project_root), manifest)
    return VisualCalibrationResult(
        project_root,
        group_key,
        page_number,
        round_number,
        status == "approved",
        status,
        log_path,
        None,
    )


def prepare_visual_review_package(project_root, group_key):
    project_root, manifest, group, _, process_root = _load_group_context(
        project_root, group_key, allowed_states={"date_review_ready"}
    )
    review_manifest = _load_json(
        _project_path(project_root, group["review_manifest"], "日期确认清单"),
        "日期确认清单",
    )
    returned_original = _project_path(
        project_root, group["returned_original"], "签署回页原件"
    )
    review_pdf = _project_path(project_root, group["review_pdf"], "日期确认稿")
    image_root = process_root / ".视觉复核暂存_visual"
    if image_root.exists():
        shutil.rmtree(image_root)
    image_root.mkdir(parents=True)

    from pypdf import PdfReader

    from .pdf_ops import extract_date_anchors, render_pdf_page

    pages = []
    returned_reader = PdfReader(str(returned_original))
    for page_text, placements in sorted(
        review_manifest.get("date_placements", {}).items(),
        key=lambda item: int(item[0]),
    ):
        if not placements:
            continue
        page_number = int(page_text)
        original_image = image_root / f"第{page_number}页_原始回页.png"
        review_image = image_root / f"第{page_number}页_脚本落字.png"
        render_pdf_page(returned_original, page_number - 1).save(original_image)
        render_pdf_page(review_pdf, page_number - 1).save(review_image)
        page = returned_reader.pages[page_number - 1]
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)
        allowed_regions = {}
        for placement in placements:
            box = placement["box"]
            height = float(box["y1"]) - float(box["y0"])
            limit = max(24.0, height * 4.0)
            allowed_regions[placement["component"]] = {
                "x0": max(0.0, float(box["x0"]) - limit),
                "y0": max(0.0, float(box["y0"]) - limit),
                "x1": min(page_width, float(box["x1"]) + limit),
                "y1": min(page_height, float(box["y1"]) + limit),
            }
        try:
            anchors = [
                {
                    "component": anchor.component,
                    "box": {
                        "x0": anchor.box.x0,
                        "y0": anchor.box.y0,
                        "x1": anchor.box.x1,
                        "y1": anchor.box.y1,
                    },
                }
                for anchor in extract_date_anchors(returned_original, page_number - 1)
            ]
        except Exception:
            anchors = []
        pages.append(
            {
                "page": page_number,
                "original_image": str(original_image),
                "review_image": str(review_image),
                "locked_placements": placements,
                "target_date": group.get("signing_date"),
                "date_anchors": anchors,
                "allowed_regions": allowed_regions,
                "locked_properties": [
                    "component",
                    "value",
                    "font",
                    "font_size",
                    "page_count",
                    "page_order",
                    "page_size",
                ],
                "allowed_changes": ["x", "y"],
                "max_rounds": 5,
            }
        )
    log_path = process_root / "模型定位调整记录_visual-calibration.json"
    log = _visual_log(log_path, group_key)
    log["vision_capability"] = "available"
    _write_manifest(log_path, log)
    group["visual_review"] = "pending" if pages else "not_required"
    group["visual_calibration_log"] = log_path.relative_to(
        project_root
    ).as_posix()
    manifest["updated_at"] = _utc_now()
    _write_manifest(_manifest_path(project_root), manifest)
    return VisualReviewPackage(project_root, group_key, tuple(pages), log_path)


def skip_group_visual_review(project_root, group_key):
    project_root, manifest, group, _, process_root = _load_group_context(
        project_root, group_key, allowed_states={"date_review_ready"}
    )
    review_manifest = _load_json(
        _project_path(project_root, group["review_manifest"], "日期确认清单"),
        "日期确认清单",
    )
    placement_pages = sorted(
        int(page)
        for page, placements in review_manifest.get("date_placements", {}).items()
        if placements
    )
    log_path = process_root / "模型定位调整记录_visual-calibration.json"
    log = _visual_log(log_path, group_key)
    log["vision_capability"] = "skipped_no_image_input"
    for page in placement_pages:
        log["pages"][str(page)] = {
            "status": "manual_attention",
            "reason": "当前模型不支持图像输入，已跳过视觉检查",
            "rounds": [],
        }
    _write_manifest(log_path, log)
    visual_temp = process_root / ".视觉复核暂存_visual"
    if visual_temp.exists():
        shutil.rmtree(visual_temp)
    group["visual_review"] = (
        "skipped_no_image_input" if placement_pages else "not_required"
    )
    date_pages, visual_pages = _attention_page_sets(group)
    visual_pages.update(placement_pages)
    _sync_attention_pages(group, date_pages, visual_pages)
    group["visual_calibration_log"] = log_path.relative_to(
        project_root
    ).as_posix()
    manifest["updated_at"] = _utc_now()
    _write_manifest(_manifest_path(project_root), manifest)
    return VisualReviewPackage(project_root, group_key, (), log_path)


def _final_name_map(records):
    counts = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(
            record.get("working_paper_path"), str
        ):
            continue
        relative = Path(record["working_paper_path"])
        key = (relative.parent.as_posix().casefold(), relative.stem.casefold())
        counts[key] = counts.get(key, 0) + 1
    names = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(
            record.get("working_paper_path"), str
        ):
            continue
        relative = Path(record["working_paper_path"])
        key = (relative.parent.as_posix().casefold(), relative.stem.casefold())
        suffix = ""
        if counts.get(key, 0) > 1:
            source_suffix = relative.suffix.lower().lstrip(".") or "word"
            suffix = f"（{source_suffix}）"
        names[record.get("working_paper_id", relative.as_posix())] = (
            relative.parent / f"{relative.stem}{suffix}.pdf"
        )
    return names


def _project_report_summary(group, batch_manifest, confirmation_time, accept_attention):
    reuse_rows = []
    for reuse_group in batch_manifest.get("reuse_groups", []):
        members = reuse_group.get("members", [])
        if len(members) < 2:
            continue
        reuse_rows.append(
            "<li><b>代表文件：</b>"
            f"{escape(str(reuse_group.get('representative', '—')))}；"
            f"<b>复用文件：</b>{escape('、'.join(map(str, members)))}；"
            f"<b>合集页：</b>{escape(str(reuse_group.get('seal_page', '—')))}</li>"
        )
    reuse_html = "".join(reuse_rows) or "<li>本组没有需要复用的完全重复文件。</li>"
    attention = "、".join(map(str, group.get("attention_pages", []))) or "无"
    attention_items = "、".join(map(str, group.get("attention_items", []))) or "无"
    return f"""
    <section class="boundary" aria-label="项目确认与复用摘要">
      <h2>本组确认与复用摘要</h2>
      <p><b>视觉复核状态：</b>{escape(str(group.get('visual_review', '未记录')))}；
      <b>需关注页：</b>{escape(attention)}；
      <b>需关注底稿：</b>{escape(attention_items)}；
      <b>日期确认时间：</b>{escape(confirmation_time)}；
      <b>已接受需关注项：</b>{'是' if accept_attention else '否'}</p>
      <ul>{reuse_html}</ul>
    </section>
    """


def finalize_signing_group(
    project_root,
    group_key,
    *,
    confirmed=False,
    accept_attention=False,
):
    if not confirmed:
        raise PermissionError("尚未取得用户日期确认，不能开始回拼")
    project_root, manifest, group, word_root, process_root = _load_group_context(
        project_root, group_key
    )
    if group.get("state") in {"completed", "completed_with_attention"}:
        issues = _artifact_issues(project_root, group)
        if issues:
            raise ValueError("既有最终成果校验失败，请先检查项目状态")
        return GroupFinalizationResult(
            project_root,
            group_key,
            _project_path(project_root, group["result_root"], "最终成果目录"),
            _project_path(project_root, group["report"], "处理清单"),
            int(group.get("final_succeeded", 0)),
            int(group.get("final_failed", 0)),
        )
    if group.get("state") != "date_review_ready":
        raise ValueError("签署文件组没有待确认的日期确认稿")
    visual_status = group.get("visual_review")
    has_attention = bool(group.get("attention_pages") or group.get("attention_items"))
    if (
        visual_status not in {"approved", "not_required"} or has_attention
    ) and not accept_attention:
        raise PermissionError(
            "模型视觉复核尚未完成或仍有需关注页；人工确认接受后才能回拼"
        )

    batch_manifest_path = _project_path(
        project_root, group["batch_manifest"], "处理批次清单"
    )
    review_manifest_path = _project_path(
        project_root, group["review_manifest"], "日期确认清单"
    )
    review_pdf = _project_path(project_root, group["review_pdf"], "日期确认稿")
    batch_data = _load_json(batch_manifest_path, "处理批次清单")
    batch_records = batch_data.get("items", [])
    final_names = _final_name_map(batch_records)
    layout = GROUP_LAYOUTS[group_key]
    visible_root = project_root / layout["label"]
    result_root = visible_root / f"{manifest['project_name']}_{layout['result_role']}"
    result_root.mkdir(parents=True, exist_ok=True)
    expected_targets = [result_root / path for path in final_names.values()]
    if any(path.exists() for path in expected_targets):
        raise FileExistsError("最终成果目录中已存在同名 PDF，不能覆盖")

    compatibility_manifest = process_root / "manifest.json"
    review_staging = process_root / ".回拼确认暂存_review"
    output_staging = process_root / ".回拼结果暂存_output"
    for path in (review_staging, output_staging):
        if path.exists():
            shutil.rmtree(path)
    compatibility_manifest.unlink(missing_ok=True)
    shutil.copyfile(batch_manifest_path, compatibility_manifest)
    review_staging.mkdir(parents=True)
    shutil.copyfile(review_pdf, review_staging / "dated-returned-pages.pdf")
    shutil.copyfile(
        review_manifest_path,
        review_staging / "review-manifest.json",
    )

    from .review_workflow import finalize_date_review

    try:
        result = finalize_date_review(
            process_root,
            review_staging,
            output_staging,
            confirmed=True,
        )
        visible_items = []
        for record, item in zip(batch_records, result.items):
            if item.output_path is None or not item.output_path.exists():
                visible_items.append(item)
                continue
            relative_output = final_names.get(item.working_paper_id)
            if relative_output is None:
                visible_items.append(item)
                continue
            target = result_root / relative_output
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item.output_path), str(target))
            visible_items.append(replace(item, output_path=target))

        from .processing_report import write_processing_report

        temporary_report = write_processing_report(
            project_root,
            review_pdf,
            visible_root,
            batch_records,
            visible_items,
            result.ocr_failures,
            working_paper_root=word_root,
            seal_pages_name=group.get("collection", ""),
            returned_pdf_label=(
                "发行人日期确认稿"
                if group_key == "issuer"
                else "项目组日期确认稿"
            ),
            returned_page_prefix="日期确认稿",
        )
        confirmation_time = _utc_now()
        report_html = temporary_report.read_text(encoding="utf-8")
        summary_html = _project_report_summary(
            group, batch_data, confirmation_time, accept_attention
        )
        report_html = report_html.replace(
            '<section class="controls" aria-label="清单筛选">',
            summary_html + '<section class="controls" aria-label="清单筛选">',
            1,
        )
        temporary_report.write_text(report_html, encoding="utf-8")
        report_path = visible_root / f"{manifest['project_name']}_{layout['report_role']}.html"
        report_path.unlink(missing_ok=True)
        temporary_report.replace(report_path)
    finally:
        compatibility_manifest.unlink(missing_ok=True)
        for path in (review_staging, output_staging):
            if path.exists():
                shutil.rmtree(path)

    succeeded = sum(item.status == "completed" for item in visible_items)
    failed = len(visible_items) - succeeded
    completed_with_attention = bool(
        failed or group.get("attention_pages") or group.get("attention_items")
    )
    group.update(
        {
            "state": (
                "completed_with_attention"
                if completed_with_attention
                else "completed"
            ),
            "date_confirmed_at": confirmation_time,
            "date_confirmation": {
                "confirmed": True,
                "accepted_attention": bool(accept_attention),
                "visual_status": visual_status,
            },
            "result_root": result_root.relative_to(project_root).as_posix(),
            "report": report_path.relative_to(project_root).as_posix(),
            "final_outputs": [
                item.output_path.relative_to(project_root).as_posix()
                for item in visible_items
                if item.output_path is not None
            ],
            "final_succeeded": succeeded,
            "final_failed": failed,
        }
    )
    _record_artifact(project_root, group, "report", report_path)
    for index, item in enumerate(visible_items, start=1):
        if item.output_path is not None and item.output_path.is_file():
            _record_artifact(project_root, group, f"final_output_{index}", item.output_path)
    completed_states = {"completed", "completed_with_attention"}
    manifest["status"] = (
        "completed"
        if all(
            record.get("state") in completed_states
            for record in manifest["groups"].values()
        )
        else "in_progress"
    )
    manifest["updated_at"] = _utc_now()
    _write_manifest(_manifest_path(project_root), manifest)
    return GroupFinalizationResult(
        project_root,
        group_key,
        result_root,
        report_path,
        succeeded,
        failed,
    )

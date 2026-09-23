from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import uuid
from typing import Any, Mapping

from pypdf import PdfReader

from .pdf_ops import sha256_file


QUICK_BATCH_FORMAT = "bond-seal-quick-batch"
QUICK_BATCH_VERSION = 1
_SUPPORTED_WORD_SUFFIXES = {".doc", ".docx"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class QuickBatchRequest:
    files: tuple[Path, ...]
    source_root: Path
    output_directory: Path


class QuickBatchContractError(ValueError):
    """The request or persisted quick-batch manifest violates its contract."""


class SelectedBatchCancelledError(RuntimeError):
    """The user cancelled a selected batch before publication completed."""


class SelectedBatchProcessingError(RuntimeError):
    """One or more selected files could not be converted safely."""

    def __init__(
        self,
        failures: list[dict[str, str]],
        *,
        selected_count: int,
        page_count: int = 0,
    ) -> None:
        self.failures = tuple(dict(failure) for failure in failures)
        self.selected_count = selected_count
        self.page_count = page_count
        details = "；".join(
            f"{failure.get('path', '未知文件')}：{failure.get('reason', '处理失败')}"
            for failure in self.failures
        )
        message = "快速合集生成失败"
        if details:
            message = f"{message}：{details}"
        super().__init__(message)


def _resolved(path: Path, *, strict: bool) -> Path:
    try:
        return path.resolve(strict=strict)
    except OSError as error:
        raise QuickBatchContractError(f"路径无法解析：{path}：{error}") from error


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_posix_relative(value: Any, *, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise QuickBatchContractError(f"{label}必须是非空相对路径")
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or "\\" in value
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise QuickBatchContractError(f"{label}必须是安全的相对路径：{value}")
    return path


def parse_quick_batch_request(request: Mapping[str, Any]) -> QuickBatchRequest:
    """Validate and normalize the frozen request v1 without touching input files."""
    if not isinstance(request, Mapping):
        raise QuickBatchContractError("请求必须是 JSON 对象")
    if request.get("version") != 1 or isinstance(request.get("version"), bool):
        raise QuickBatchContractError("不支持的快速批次请求版本")

    raw_files = request.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise QuickBatchContractError("files 必须是非空 Word 文件列表")

    files: list[Path] = []
    seen: set[str] = set()
    for raw_path in raw_files:
        if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
            raise QuickBatchContractError("files 中的每个路径都必须是非空字符串")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise QuickBatchContractError(f"Word 路径必须是绝对路径：{raw_path}")
        path = _resolved(path, strict=True)
        if not path.is_file():
            raise QuickBatchContractError(f"Word 文件不存在或不是文件：{path}")
        if path.suffix.lower() not in _SUPPORTED_WORD_SUFFIXES:
            raise QuickBatchContractError(f"仅支持 .doc 和 .docx：{path}")
        key = str(path).casefold()
        if key in seen:
            raise QuickBatchContractError(f"文件列表包含重复路径：{path}")
        seen.add(key)
        files.append(path)

    source_root = files[0].parent
    if any(path.parent != source_root for path in files[1:]):
        raise QuickBatchContractError("所选 Word 必须直接位于同一个文件夹中")

    files.sort(key=lambda path: (path.name.casefold(), path.name))

    raw_output_directory = request.get("output_directory")
    if raw_output_directory is None:
        output_directory = source_root
    else:
        if not isinstance(raw_output_directory, (str, Path)) or not str(
            raw_output_directory
        ).strip():
            raise QuickBatchContractError("output_directory 必须是非空路径")
        output_directory = Path(raw_output_directory).expanduser()
        if not output_directory.is_absolute():
            output_directory = source_root / output_directory
        output_directory = _resolved(output_directory, strict=False)
        if output_directory.exists() and not output_directory.is_dir():
            raise QuickBatchContractError(
                f"输出位置已存在但不是文件夹：{output_directory}"
            )

    return QuickBatchRequest(
        files=tuple(files),
        source_root=source_root,
        output_directory=output_directory,
    )


def _pdf_page_count(path: Path) -> int:
    reader = PdfReader(str(path))
    try:
        return len(reader.pages)
    finally:
        stream = getattr(reader, "stream", None)
        close = getattr(stream, "close", None)
        if close is not None:
            close()


def validate_quick_batch_manifest(
    batch_dir: str | Path,
    *,
    verify_source_hashes: bool = True,
) -> dict[str, Any]:
    """Read and validate a complete quick-batch sidecar without modifying anything.

    Source and cache paths are resolved before containment checks to reject symlink
    escapes. The returned dictionary is the validated JSON object.
    """
    batch_path = _resolved(Path(batch_dir), strict=True)
    if not batch_path.is_dir():
        raise QuickBatchContractError(f"批次目录不存在：{batch_path}")

    manifest_path = batch_path / "quick_batch.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QuickBatchContractError(
            f"快速批次清单不存在或无法读取：{manifest_path}"
        ) from error

    if not isinstance(manifest, dict):
        raise QuickBatchContractError("快速批次清单必须是 JSON 对象")
    if manifest.get("format") != QUICK_BATCH_FORMAT:
        raise QuickBatchContractError("快速批次清单格式不受支持")
    if manifest.get("version") != QUICK_BATCH_VERSION or isinstance(
        manifest.get("version"), bool
    ):
        raise QuickBatchContractError("快速批次清单版本不受支持")
    try:
        uuid.UUID(str(manifest.get("batch_id", "")))
    except (ValueError, AttributeError) as error:
        raise QuickBatchContractError("快速批次 ID 无效") from error

    raw_source_root = manifest.get("source_root")
    if not isinstance(raw_source_root, str) or not Path(raw_source_root).is_absolute():
        raise QuickBatchContractError("source_root 必须是绝对路径")
    source_root = _resolved(Path(raw_source_root), strict=True)
    if not source_root.is_dir():
        raise QuickBatchContractError(f"源文件夹不存在：{source_root}")

    collection_name = manifest.get("collection")
    if (
        not isinstance(collection_name, str)
        or not collection_name
        or "/" in collection_name
        or "\\" in collection_name
        or Path(collection_name).name != collection_name
        or PureWindowsPath(collection_name).name != collection_name
        or Path(collection_name).suffix.lower() != ".pdf"
    ):
        raise QuickBatchContractError("collection 必须是同级 PDF 文件名")
    collection_path = _resolved(batch_path.parent / collection_name, strict=True)
    if collection_path.parent != batch_path.parent or not collection_path.is_file():
        raise QuickBatchContractError("合集 PDF 必须存在于批次目录的同级文件夹")

    collection_sha256 = manifest.get("collection_sha256")
    if not isinstance(collection_sha256, str) or not _SHA256_RE.fullmatch(
        collection_sha256
    ):
        raise QuickBatchContractError("合集 PDF SHA-256 格式无效")
    if sha256_file(collection_path) != collection_sha256:
        raise QuickBatchContractError("合集 PDF 哈希与清单不一致")
    try:
        collection_page_count = _pdf_page_count(collection_path)
    except Exception as error:
        raise QuickBatchContractError(f"合集 PDF 无法读取：{error}") from error

    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise QuickBatchContractError("快速批次必须至少包含一个文件项")
    selected_count = manifest.get("selected_count")
    if selected_count is not None and (
        not isinstance(selected_count, int)
        or isinstance(selected_count, bool)
        or selected_count != len(items)
    ):
        raise QuickBatchContractError("selected_count 与文件项数量不一致")

    root_resolved = source_root.resolve()
    batch_resolved = batch_path.resolve()
    items_by_id: dict[str, dict[str, Any]] = {}
    seen_ids_casefold: set[str] = set()
    groups: dict[str, list[dict[str, Any]]] = {}
    page_owner: dict[int, str] = {}
    last_sort_key: tuple[str, str] | None = None

    for item in items:
        if not isinstance(item, dict):
            raise QuickBatchContractError("批次文件项格式无效")

        working_paper_id = item.get("working_paper_id")
        relative_source = _safe_posix_relative(
            item.get("working_paper_path"),
            label="working_paper_path",
        )
        if len(relative_source.parts) != 1:
            raise QuickBatchContractError("working_paper_path 必须是源文件夹中的直接文件")
        if working_paper_id != relative_source.as_posix():
            raise QuickBatchContractError(
                "working_paper_id 必须与 working_paper_path 一致"
            )
        normalized_id = str(working_paper_id).casefold()
        if normalized_id in seen_ids_casefold:
            raise QuickBatchContractError(f"批次包含重复文件项：{working_paper_id}")
        seen_ids_casefold.add(normalized_id)
        sort_key = (relative_source.name.casefold(), relative_source.name)
        if last_sort_key is not None and sort_key < last_sort_key:
            raise QuickBatchContractError("批次文件项未按文件名稳定排序")
        last_sort_key = sort_key

        source_path = _resolved(source_root.joinpath(*relative_source.parts), strict=True)
        if (
            source_path.parent != root_resolved
            or not _is_within(source_path, root_resolved)
            or not source_path.is_file()
            or source_path.suffix.lower() not in _SUPPORTED_WORD_SUFFIXES
        ):
            raise QuickBatchContractError(
                f"源 Word 路径超出 source_root 或格式无效：{working_paper_id}"
            )
        source_hash = item.get("working_paper_sha256")
        if not isinstance(source_hash, str) or not _SHA256_RE.fullmatch(source_hash):
            raise QuickBatchContractError(
                f"源 Word SHA-256 格式无效：{working_paper_id}"
            )
        if verify_source_hashes and sha256_file(source_path) != source_hash:
            raise QuickBatchContractError(
                f"源 Word 已变化，不能导入旧批次：{working_paper_id}"
            )

        relative_pdf = _safe_posix_relative(
            item.get("converted_pdf"),
            label="converted_pdf",
        )
        converted_pdf = _resolved(
            batch_path.joinpath(*relative_pdf.parts),
            strict=True,
        )
        if (
            converted_pdf == batch_resolved
            or not _is_within(converted_pdf, batch_resolved)
            or not converted_pdf.is_file()
        ):
            raise QuickBatchContractError(
                f"转换 PDF 路径超出批次目录：{working_paper_id}"
            )
        pdf_hash = item.get("pdf_sha256")
        if not isinstance(pdf_hash, str) or not _SHA256_RE.fullmatch(pdf_hash):
            raise QuickBatchContractError(
                f"转换 PDF SHA-256 格式无效：{working_paper_id}"
            )
        if sha256_file(converted_pdf) != pdf_hash:
            raise QuickBatchContractError(
                f"转换 PDF 哈希与清单不一致：{working_paper_id}"
            )
        try:
            page_count = _pdf_page_count(converted_pdf)
        except Exception as error:
            raise QuickBatchContractError(
                f"转换 PDF 无法读取：{working_paper_id}：{error}"
            ) from error
        declared_page_count = item.get("pdf_page_count")
        if (
            page_count < 1
            or not isinstance(declared_page_count, int)
            or isinstance(declared_page_count, bool)
            or declared_page_count != page_count
        ):
            raise QuickBatchContractError(
                f"转换 PDF 页数与清单不一致：{working_paper_id}"
            )

        seal_page = item.get("seal_page")
        if (
            not isinstance(seal_page, int)
            or isinstance(seal_page, bool)
            or seal_page < 1
            or seal_page > collection_page_count
        ):
            raise QuickBatchContractError(f"合集页码超出范围：{working_paper_id}")

        existing_page_hash = page_owner.get(seal_page)
        if existing_page_hash is not None and existing_page_hash != source_hash:
            raise QuickBatchContractError("不同 SHA-256 文件错误地复用了合集页")
        page_owner[seal_page] = source_hash
        if any(existing.get("seal_page") != seal_page for existing in groups.get(source_hash, [])):
            raise QuickBatchContractError("重复文件没有映射到同一合集页")
        if any(existing.get("pdf_sha256") != pdf_hash for existing in groups.get(source_hash, [])):
            raise QuickBatchContractError("重复文件组的转换 PDF 哈希不一致")
        reuse_of = item.get("reuse_of")
        if not isinstance(reuse_of, str) or not reuse_of:
            raise QuickBatchContractError(f"reuse_of 必须是文件 ID：{working_paper_id}")
        if item.get("duplicate_group_sha256") != source_hash:
            raise QuickBatchContractError(
                f"duplicate_group_sha256 与源文件哈希不一致：{working_paper_id}"
            )

        items_by_id[working_paper_id] = item
        groups.setdefault(source_hash, []).append(item)

    for source_hash, members in groups.items():
        representative_ids = {member.get("reuse_of") for member in members}
        if len(representative_ids) != 1:
            raise QuickBatchContractError("同一重复文件组的 reuse_of 不一致")
        representative_id = next(iter(representative_ids))
        representative = items_by_id.get(representative_id)
        if representative is None or representative not in members:
            raise QuickBatchContractError("reuse_of 必须指向同一 SHA-256 组内的文件")
        if representative.get("reuse_of") != representative_id:
            raise QuickBatchContractError("reuse_of 目标必须是重复组代表文件自身")
        if (
            representative.get("working_paper_sha256") != source_hash
            or representative.get("seal_page") != members[0].get("seal_page")
            or representative.get("pdf_sha256") != members[0].get("pdf_sha256")
        ):
            raise QuickBatchContractError("reuse_of 目标与重复组签署页不一致")

    if collection_page_count != len(groups):
        raise QuickBatchContractError(
            "合集 PDF 页数必须等于不同源文件 SHA-256 的数量"
        )
    if set(page_owner) != set(range(1, collection_page_count + 1)):
        raise QuickBatchContractError("合集签署页页码必须连续且无遗漏")
    declared_page_count = manifest.get("page_count")
    if declared_page_count is not None and (
        not isinstance(declared_page_count, int)
        or isinstance(declared_page_count, bool)
        or declared_page_count != len(groups)
    ):
        raise QuickBatchContractError("page_count 与重复文件组数量不一致")

    return manifest

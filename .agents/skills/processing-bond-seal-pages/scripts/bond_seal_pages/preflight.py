from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


_UNSET = object()
_MINIMUM_PYTHON = (3, 10)
_BASE_PACKAGES = (
    "rapidocr",
    "onnxruntime",
    "pypdf",
    "pdfplumber",
    "pypdfium2",
    "reportlab",
    "pymupdf",
)
_PIP_NAMES = {
    "rapidocr": "rapidocr>=3.9.0",
    "onnxruntime": "onnxruntime",
    "pypdf": "pypdf",
    "pdfplumber": "pdfplumber",
    "pypdfium2": "pypdfium2",
    "reportlab": "reportlab",
    "pymupdf": "pymupdf",
    "win32com.client": "pywin32",
}
_SOURCE_LABELS = {
    "active-conda": "当前激活的 Conda/Anaconda 环境",
    "current-environment": "当前执行环境",
    "python-launcher": "系统 Python 启动器",
    "system-python": "系统 Python",
}


@dataclass(frozen=True)
class PythonRuntimeInfo:
    executable: str
    version: tuple[int, int, int]
    architecture: str


@dataclass(frozen=True)
class PythonCandidate:
    command: tuple[str, ...]
    source: str
    priority: int
    runtime: PythonRuntimeInfo | None = None
    error: str | None = None


@dataclass(frozen=True)
class PreflightCheck:
    key: str
    label: str
    ok: bool
    detail: str
    impact: str | None = None


@dataclass(frozen=True)
class PreflightResult:
    platform_name: str
    candidates: tuple[PythonCandidate, ...]
    selected: PythonCandidate | None
    ambiguous_candidates: tuple[PythonCandidate, ...]
    checks: tuple[PreflightCheck, ...]
    converter_executable: Path | None = None

    @property
    def ready(self):
        return self.selected is not None and all(check.ok for check in self.checks)

    @property
    def failed_checks(self):
        return tuple(check for check in self.checks if not check.ok)


@dataclass(frozen=True)
class InstallationPlan:
    target: PythonCandidate | None
    commands: tuple[tuple[str, ...], ...]
    manual_steps: tuple[str, ...]


def _python_command_key(command):
    return os.path.normcase(" ".join(str(part) for part in command))


def _candidate_commands(platform_name, env, current_executable, which):
    candidates = []
    conda_prefix = env.get("CONDA_PREFIX")
    if conda_prefix:
        conda_python = (
            Path(conda_prefix) / "python.exe"
            if platform_name == "win32"
            else Path(conda_prefix) / "bin" / "python"
        )
        candidates.append(((str(conda_python),), "active-conda", 0))
    if current_executable:
        candidates.append(((str(current_executable),), "current-environment", 1))
    if platform_name == "win32":
        launcher = which("py")
        if launcher:
            candidates.append(((str(launcher), "-3"), "python-launcher", 2))
    for name in ("python", "python3"):
        executable = which(name)
        if executable:
            candidates.append(((str(executable),), "system-python", 2))
    unique = {}
    for command, source, priority in candidates:
        unique.setdefault(_python_command_key(command), (command, source, priority))
    return tuple(unique.values())


def _default_python_probe(command, runner):
    probe = (
        "import json,platform,sys;"
        "print(json.dumps({'executable':sys.executable,"
        "'version':list(sys.version_info[:3]),"
        "'architecture':platform.architecture()[0]}))"
    )
    completed = runner(
        [*command, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    data = json.loads(completed.stdout.strip().splitlines()[-1])
    return PythonRuntimeInfo(
        str(data["executable"]),
        tuple(int(part) for part in data["version"]),
        str(data["architecture"]),
    )


def _default_package_probe(candidate, runner, modules):
    module_names = json.dumps(modules)
    probe = f"""
import importlib.util
import json
modules = json.loads({module_names!r})
result = {{}}
for module in modules:
    try:
        parent = module.split('.', 1)[0]
        result[module] = importlib.util.find_spec(parent) is not None and importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError):
        result[module] = False
print(json.dumps(result))
"""
    completed = runner(
        [*candidate.command, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _probe_candidates(command_specs, python_probe):
    probed = []
    for command, source, priority in command_specs:
        try:
            runtime = python_probe(command)
            probed.append(PythonCandidate(command, source, priority, runtime=runtime))
        except Exception as error:
            probed.append(
                PythonCandidate(
                    command,
                    source,
                    priority,
                    error=str(error) or error.__class__.__name__,
                )
            )

    by_runtime = {}
    invalid = []
    for candidate in probed:
        if candidate.runtime is None:
            invalid.append(candidate)
            continue
        key = os.path.normcase(str(Path(candidate.runtime.executable)))
        existing = by_runtime.get(key)
        if existing is None or candidate.priority < existing.priority:
            by_runtime[key] = candidate
    return tuple(
        sorted(
            [*by_runtime.values(), *invalid],
            key=lambda item: (item.priority, _python_command_key(item.command)),
        )
    )


def _normalized_executable(value):
    return os.path.normcase(os.path.normpath(str(value)))


def _candidate_matches(candidate, selected_python):
    requested = _normalized_executable(selected_python)
    return requested in {
        _normalized_executable(candidate.runtime.executable),
        _normalized_executable(candidate.command[0]),
    }


def _select_candidate(candidates, selected_python=None):
    available = tuple(candidate for candidate in candidates if candidate.runtime)
    if not available:
        return None, ()
    if selected_python is not None:
        matches = tuple(
            candidate
            for candidate in available
            if _candidate_matches(candidate, selected_python)
        )
        if len(matches) != 1:
            paths = "、".join(
                candidate.runtime.executable for candidate in available
            )
            raise ValueError(
                f"指定的 Python 不在可用候选中：{selected_python}；候选：{paths}"
            )
        return matches[0], ()
    best_priority = min(candidate.priority for candidate in available)
    best = tuple(
        candidate for candidate in available if candidate.priority == best_priority
    )
    if len(best) == 1:
        return best[0], ()
    return None, best


def discover_windows_word(which=shutil.which):
    executable = which("WINWORD.EXE") or which("winword")
    if executable:
        return Path(executable)
    try:
        import winreg
    except ImportError:
        return None
    subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WINWORD.EXE"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for access in (
            winreg.KEY_READ,
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_32KEY", 0),
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        ):
            try:
                with winreg.OpenKey(hive, subkey, 0, access) as key:
                    value, _ = winreg.QueryValueEx(key, None)
                candidate = Path(os.path.expandvars(value))
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    return None


def ocr_model_cache_ready(cache_dir=None):
    if cache_dir is None:
        from .ocr import default_ocr_cache_dir

        cache_dir = default_ocr_cache_dir()
    root = Path(cache_dir)
    if (root / ".ppocrv6-small-ready").is_file():
        return True
    if not root.is_dir():
        return False
    matching_models = [
        path
        for path in root.rglob("*.onnx")
        if "ppocrv6" in path.as_posix().lower()
        and "small" in path.as_posix().lower()
    ]
    return len(matching_models) >= 2


def _selection_checks(candidates, selected, ambiguous):
    if not candidates or not any(candidate.runtime for candidate in candidates):
        return [
            PreflightCheck(
                "python-discovery",
                "Python",
                False,
                "没有发现可用 Python",
                "需要先安装 64 位 Python 3.10 或更高版本",
            )
        ]
    if ambiguous:
        paths = "、".join(candidate.runtime.executable for candidate in ambiguous)
        return [
            PreflightCheck(
                "python-selection",
                "Python 选择",
                False,
                f"发现同优先级候选，需要选择：{paths}",
                "选择后只会检查和修改被选中的一个环境",
            )
        ]
    version = selected.runtime.version
    architecture = selected.runtime.architecture
    return [
        PreflightCheck(
            "python-version",
            "Python 版本",
            version >= _MINIMUM_PYTHON,
            f"{version[0]}.{version[1]}.{version[2]}",
            None if version >= _MINIMUM_PYTHON else "需要 Python 3.10 或更高版本",
        ),
        PreflightCheck(
            "python-architecture",
            "Python 架构",
            "64" in architecture,
            architecture,
            None if "64" in architecture else "OCR 和 PDF 依赖要求 64 位 Python",
        ),
    ]


def _execution_check(selected, current_executable):
    if selected is None or not current_executable:
        return None
    matches = _normalized_executable(selected.runtime.executable) == (
        _normalized_executable(current_executable)
    )
    return PreflightCheck(
        "execution-python",
        "业务执行 Python",
        matches,
        str(current_executable),
        None
        if matches
        else f"请使用 {selected.runtime.executable} 重新运行前置检查和业务入口",
    )


def run_preflight(
    platform_name=_UNSET,
    env=None,
    current_executable=_UNSET,
    selected_python=None,
    require_converter=True,
    which=shutil.which,
    runner=subprocess.run,
    python_probe=None,
    package_probe=None,
    model_cache_ready=None,
    windows_word_locator=None,
    mac_libreoffice_locator=None,
):
    platform_name = sys.platform if platform_name is _UNSET else platform_name
    env = dict(os.environ if env is None else env)
    current_executable = (
        sys.executable if current_executable is _UNSET else current_executable
    )
    python_probe = python_probe or (
        lambda command: _default_python_probe(command, runner)
    )
    command_specs = _candidate_commands(
        platform_name,
        env,
        current_executable,
        which,
    )
    candidates = _probe_candidates(command_specs, python_probe)
    selected, ambiguous = _select_candidate(candidates, selected_python)
    checks = _selection_checks(candidates, selected, ambiguous)
    execution_check = _execution_check(selected, current_executable)
    if execution_check is not None:
        checks.append(execution_check)

    supported_platform = platform_name in {"win32", "darwin"}
    checks.append(
        PreflightCheck(
            "platform",
            "操作系统",
            supported_platform,
            "Windows" if platform_name == "win32" else "macOS" if platform_name == "darwin" else platform_name,
            None if supported_platform else "当前版本仅支持 Windows 和 macOS",
        )
    )
    converter_executable = None
    if selected is not None:
        windows_packages = (
            ("win32com.client",)
            if require_converter and platform_name == "win32"
            else ()
        )
        modules = (*_BASE_PACKAGES, *windows_packages)
        package_probe = package_probe or (
            lambda candidate: _default_package_probe(candidate, runner, modules)
        )
        try:
            packages = package_probe(selected)
        except Exception as error:
            packages = {module: False for module in modules}
            checks.append(
                PreflightCheck(
                    "dependency-probe",
                    "Python 依赖检查",
                    False,
                    str(error) or error.__class__.__name__,
                    "无法确认依赖状态，业务处理不会启动",
                )
            )
        for module in modules:
            installed = bool(packages.get(module))
            checks.append(
                PreflightCheck(
                    f"dependency:{module}",
                    module,
                    installed,
                    "已安装" if installed else "未安装",
                    None if installed else f"将只安装到 {selected.runtime.executable}",
                )
            )
        model_checker = model_cache_ready or ocr_model_cache_ready
        model_ready = bool(model_checker())
        checks.append(
            PreflightCheck(
                "ocr-model",
                "PP-OCRv6 small 模型",
                model_ready,
                "已准备，可离线使用" if model_ready else "尚未准备",
                None if model_ready else "首次模型准备需要联网下载；完成后可离线复用",
            )
        )

    if require_converter and platform_name == "win32":
        locator = windows_word_locator or discover_windows_word
        converter_executable = locator()
        checks.append(
            PreflightCheck(
                "converter",
                "Microsoft Word",
                converter_executable is not None,
                str(converter_executable) if converter_executable else "未发现 Microsoft Word",
                None if converter_executable else "需要安装 Microsoft Word；不会自动替换为其他转换器",
            )
        )
    elif require_converter and platform_name == "darwin":
        if mac_libreoffice_locator is None:
            from .word_conversion import discover_macos_libreoffice

            mac_libreoffice_locator = discover_macos_libreoffice
        converter_executable = mac_libreoffice_locator()
        checks.append(
            PreflightCheck(
                "converter",
                "LibreOffice",
                converter_executable is not None,
                str(converter_executable) if converter_executable else "未发现 LibreOffice",
                None if converter_executable else "需要安装 LibreOffice；安装系统应用前必须征得同意",
            )
        )

    return PreflightResult(
        platform_name,
        candidates,
        selected,
        ambiguous,
        tuple(checks),
        Path(converter_executable) if converter_executable else None,
    )


def format_preflight_report(result):
    lines = [f"前置检查：{'通过' if result.ready else '未通过'}"]
    if result.candidates:
        lines.append("发现的 Python：")
        for candidate in result.candidates:
            label = _SOURCE_LABELS.get(candidate.source, candidate.source)
            if candidate.runtime:
                lines.append(f"- {label}：{candidate.runtime.executable}")
            else:
                lines.append(f"- {label}：{' '.join(candidate.command)}（不可用：{candidate.error}）")
    if result.selected:
        lines.append(f"目标 Python：{result.selected.runtime.executable}")
    for check in result.failed_checks:
        lines.append(f"缺失/异常：{check.label} — {check.detail}")
        if check.impact:
            lines.append(f"  安装或处理影响：{check.impact}")
    lines.append("未执行任何安装，也未创建私有回退环境。")
    return "\n".join(lines)


def build_installation_plan(result, approved=False, which=shutil.which):
    if not approved:
        raise PermissionError("尚未获得用户同意，不能生成可执行安装计划")
    commands = []
    manual_steps = []
    if result.selected is None:
        available = tuple(
            candidate for candidate in result.candidates if candidate.runtime
        )
        if available:
            raise ValueError("存在多个可用 Python，请先明确选择一个候选")
        if result.platform_name == "win32" and which("winget"):
            commands.append(
                (
                    str(which("winget")),
                    "install",
                    "--exact",
                    "--id",
                    "Python.Python.3.12",
                )
            )
        elif result.platform_name == "darwin" and which("brew"):
            commands.append((str(which("brew")), "install", "python@3.12"))
        elif result.platform_name == "win32":
            manual_steps.append("安装 64 位 Python 3.10 或更高版本并加入 PATH")
        elif result.platform_name == "darwin":
            manual_steps.append("安装 64 位 Python 3.10 或更高版本")
        manual_steps.append("安装后重新运行前置检查，再选择唯一目标 Python")
        return InstallationPlan(None, tuple(commands), tuple(manual_steps))
    missing_modules = [
        check.key.split(":", 1)[1]
        for check in result.failed_checks
        if check.key.startswith("dependency:")
    ]
    if missing_modules:
        pip_packages = tuple(
            dict.fromkeys(_PIP_NAMES[module] for module in missing_modules)
        )
        commands.append(
            (*result.selected.command, "-m", "pip", "install", *pip_packages)
        )
    if any(check.key == "ocr-model" for check in result.failed_checks):
        scripts_root = str(Path(__file__).resolve().parents[1])
        model_setup = (
            f"import sys;sys.path.insert(0, {scripts_root!r});"
            "from bond_seal_pages.ocr import prepare_ocr_models;prepare_ocr_models()"
        )
        commands.append(
            (
                *result.selected.command,
                "-c",
                model_setup,
            )
        )
    if any(check.key == "converter" for check in result.failed_checks):
        if result.platform_name == "darwin" and which("brew"):
            commands.append((str(which("brew")), "install", "--cask", "libreoffice"))
        elif result.platform_name == "darwin":
            manual_steps.append("从 LibreOffice 官方安装包安装 macOS 应用")
        elif result.platform_name == "win32":
            manual_steps.append("安装或修复 Microsoft Word")
    for check in result.failed_checks:
        if check.key in {"python-version", "python-architecture", "platform"}:
            manual_steps.append(check.impact or check.detail)
    return InstallationPlan(
        result.selected,
        tuple(commands),
        tuple(dict.fromkeys(manual_steps)),
    )


def require_preflight_ready(result):
    if not result.ready:
        raise RuntimeError(f"前置检查未通过：\n{format_preflight_report(result)}")

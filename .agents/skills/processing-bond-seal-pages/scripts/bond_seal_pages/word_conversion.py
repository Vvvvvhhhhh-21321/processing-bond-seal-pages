from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


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

    def convert(self, input_path, output_path):
        input_path = Path(input_path).resolve()
        output_path = Path(output_path).resolve()
        document = self._application.Documents.Open(
            str(input_path),
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


def discover_macos_libreoffice(
    which=shutil.which,
    runner=subprocess.run,
    is_file=Path.is_file,
):
    for name in ("libreoffice", "soffice"):
        executable = which(name)
        if executable:
            return Path(executable)
    spotlight = which("mdfind")
    if not spotlight:
        return None
    try:
        completed = runner(
            [spotlight, "kMDItemCFBundleIdentifier == 'org.libreoffice.script'"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in completed.stdout.splitlines():
        application = Path(line.strip())
        if not application.name.lower().endswith(".app"):
            continue
        executable = application / "Contents" / "MacOS" / "soffice"
        if is_file(executable):
            return executable
    return None


class MacOSLibreOfficePdfConverter:
    def __init__(
        self,
        executable=None,
        runner=subprocess.run,
        locator=discover_macos_libreoffice,
    ):
        executable = executable or locator()
        if not executable:
            raise RuntimeError(
                "未发现 LibreOffice；请先安装 LibreOffice，"
                "并确认 soffice 可执行文件可用"
            )
        self._executable = Path(executable)
        self._runner = runner

    def convert(self, input_path, output_path):
        input_path = Path(input_path).resolve()
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="bond-seal-pages-",
            dir=output_path.parent,
        ) as directory:
            temporary_root = Path(directory)
            profile_uri = (temporary_root / "profile").resolve().as_uri()
            command = [
                str(self._executable),
                f"-env:UserInstallation={profile_uri}",
                "--headless",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(temporary_root),
                str(input_path),
            ]
            try:
                completed = self._runner(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
            except (OSError, subprocess.SubprocessError) as error:
                stderr = getattr(error, "stderr", "") or ""
                detail = f"：{stderr.strip()}" if stderr.strip() else ""
                raise RuntimeError(f"LibreOffice 转换失败{detail}") from error
            converted_path = temporary_root / f"{input_path.stem}.pdf"
            if not converted_path.is_file():
                detail = (completed.stderr or completed.stdout or "").strip()
                suffix = f"：{detail}" if detail else ""
                raise RuntimeError(f"LibreOffice 未生成 PDF{suffix}")
            converted_path.replace(output_path)

    def close(self):
        return None


def create_platform_word_pdf_converter(platform_name=None):
    platform_name = platform_name or sys.platform
    if platform_name == "win32":
        return WindowsWordPdfConverter()
    if platform_name == "darwin":
        return MacOSLibreOfficePdfConverter()
    raise RuntimeError("当前版本仅支持 Windows 和 macOS 的 Word 转 PDF")

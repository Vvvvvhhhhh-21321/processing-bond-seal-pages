from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Sequence
import uuid


PDF_FORMAT_CODE = 17
_WORD_READY_EVENT = "word_ready"
_WORKER_EOF = object()


def _default_worker_command() -> list[str]:
    if sys.platform != "win32":
        raise RuntimeError("Microsoft Word 转换 worker 仅支持 Windows")
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve().with_name("BondSealWordWorker.exe")
        if not executable.is_file():
            raise RuntimeError(
                "缺少 Word 转换 worker：请将 BondSealWordWorker.exe 放在主程序旁边"
            )
        return [str(executable)]
    return [sys.executable, "-X", "utf8", "-m", "bond_seal_pages.word_worker"]


def _worker_environment() -> dict[str, str]:
    environment = os.environ.copy()
    package_parent = str(Path(__file__).resolve().parent.parent)
    existing = environment.get("PYTHONPATH", "")
    paths = [package_parent]
    if existing:
        paths.append(existing)
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    environment["PYTHONUTF8"] = "1"
    return environment


def _terminate_attested_word_process(identity: dict[str, int] | None) -> bool:
    """Terminate one verified Word process, never a process selected by name alone."""
    if sys.platform != "win32" or not isinstance(identity, dict):
        return False
    try:
        pid = int(identity["pid"])
        expected_creation = int(identity["creation_time"])
    except (KeyError, TypeError, ValueError):
        return False
    if pid <= 0 or expected_creation <= 0:
        return False

    process = None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        process = open_process(0x0001 | 0x1000, False, pid)
        if not process:
            return False

        image_buffer = ctypes.create_unicode_buffer(32768)
        image_size = wintypes.DWORD(len(image_buffer))
        query_image = kernel32.QueryFullProcessImageNameW
        query_image.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        query_image.restype = wintypes.BOOL
        if not query_image(process, 0, image_buffer, ctypes.byref(image_size)):
            return False
        if Path(image_buffer.value).name.casefold() != "winword.exe":
            return False

        class FileTime(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD),
            ]

        creation = FileTime()
        exited = FileTime()
        kernel = FileTime()
        user = FileTime()
        get_times = kernel32.GetProcessTimes
        get_times.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        ]
        get_times.restype = wintypes.BOOL
        if not get_times(
            process,
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return False
        actual_creation = (int(creation.dwHighDateTime) << 32) | int(
            creation.dwLowDateTime
        )
        if actual_creation != expected_creation:
            return False

        terminate = kernel32.TerminateProcess
        terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
        terminate.restype = wintypes.BOOL
        if not terminate(process, 1):
            return False
        wait = kernel32.WaitForSingleObject
        wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        wait.restype = wintypes.DWORD
        wait(process, 3000)
        return True
    except Exception:
        return False
    finally:
        if process:
            try:
                import ctypes

                ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(process)
            except Exception:
                pass


class WindowsWordPdfConverter:
    """Convert using a private worker process so hung COM calls are cancellable.

    `application_factory` remains available for lightweight unit-test adapters.
    Production calls use an isolated worker and never find/kill WINWORD by name.
    """

    def __init__(
        self,
        application_factory: Callable[[], Any] | None = None,
        *,
        timeout_seconds: float = 180.0,
        shutdown_timeout_seconds: float = 15.0,
        worker_command_factory: Callable[[], Sequence[str]] | None = None,
        popen_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        if timeout_seconds <= 0 or shutdown_timeout_seconds <= 0:
            raise ValueError("worker 超时必须大于 0")
        self._application_factory = application_factory
        self._timeout_seconds = timeout_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._worker_command_factory = worker_command_factory or _default_worker_command
        self._popen_factory = popen_factory
        self._process: Any = None
        self._responses: queue.Queue[Any] | None = None
        self._reader_thread: threading.Thread | None = None
        self._owned_word_process: dict[str, int] | None = None
        self._direct_application: Any = None

        if application_factory is not None:
            from .word_worker import configure_word_application

            self._direct_application = application_factory()
            configure_word_application(self._direct_application)

    def _start_worker(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if self._process is not None:
            self._terminate_worker(terminate_word=True)
        command = list(self._worker_command_factory())
        if not command:
            raise RuntimeError("Word worker 命令为空")
        environment = _worker_environment()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = self._popen_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
            env=environment,
            creationflags=creationflags,
        )
        responses: queue.Queue[Any] = queue.Queue()
        self._process = process
        self._responses = responses
        self._owned_word_process = None

        def read_responses() -> None:
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    try:
                        message = json.loads(line)
                        if not isinstance(message, dict):
                            raise ValueError("worker 消息必须是 JSON 对象")
                        responses.put(message)
                    except (json.JSONDecodeError, ValueError) as error:
                        responses.put({"_protocol_error": str(error)})
            except Exception as error:
                responses.put({"_protocol_error": str(error)})
            finally:
                responses.put(_WORKER_EOF)

        self._reader_thread = threading.Thread(
            target=read_responses,
            name="bond-seal-word-worker-reader",
            daemon=True,
        )
        self._reader_thread.start()

    def _clear_worker_references(self) -> None:
        self._process = None
        self._responses = None
        self._reader_thread = None
        self._owned_word_process = None

    def _terminate_worker(self, *, terminate_word: bool) -> None:
        process = self._process
        reader_thread = self._reader_thread
        if terminate_word:
            _terminate_attested_word_process(self._owned_word_process)
        self._clear_worker_references()
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        except (OSError, subprocess.SubprocessError):
            pass
        for stream_name in ("stdin", "stdout"):
            stream = getattr(process, stream_name, None)
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if reader_thread is not None and reader_thread is not threading.current_thread():
            reader_thread.join(timeout=1)

    def _receive_response(self, request_id: str, timeout: float) -> dict[str, Any]:
        responses = self._responses
        assert responses is not None
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate_worker(terminate_word=True)
                raise TimeoutError(f"Word 转 PDF 超时（>{timeout:g} 秒）")
            try:
                message = responses.get(timeout=remaining)
            except queue.Empty:
                self._terminate_worker(terminate_word=True)
                raise TimeoutError(f"Word 转 PDF 超时（>{timeout:g} 秒）")
            if message is _WORKER_EOF:
                process = self._process
                return_code = process.poll() if process is not None else None
                self._terminate_worker(terminate_word=True)
                raise RuntimeError(
                    f"Word worker 意外退出（exit={return_code}）"
                )
            if "_protocol_error" in message:
                self._terminate_worker(terminate_word=True)
                raise RuntimeError(f"Word worker 协议错误：{message['_protocol_error']}")
            if message.get("request_id") != request_id:
                self._terminate_worker(terminate_word=True)
                raise RuntimeError("Word worker 响应 ID 不匹配")
            if message.get("event") == _WORD_READY_EVENT:
                identity = message.get("word_process")
                self._owned_word_process = identity if isinstance(identity, dict) else None
                continue
            return message

    def convert(self, input_path: str | Path, output_path: str | Path) -> Path:
        input_file = Path(input_path).resolve(strict=True)
        output_file = Path(output_path).resolve()
        if not input_file.is_file() or input_file.suffix.lower() not in {".doc", ".docx"}:
            raise ValueError(f"仅支持存在的 .doc/.docx 文件：{input_file}")
        if output_file.exists():
            raise FileExistsError(f"转换目标已存在：{output_file}")
        output_file.parent.mkdir(parents=True, exist_ok=True)

        if self._direct_application is not None:
            from .word_worker import convert_document

            return convert_document(self._direct_application, input_file, output_file)

        self._start_worker()
        assert self._process is not None
        assert self._process.stdin is not None
        request_id = str(uuid.uuid4())
        request = {
            "request_id": request_id,
            "operation": "convert",
            "input_path": str(input_file),
            "output_path": str(output_file),
        }
        try:
            self._process.stdin.write(
                json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            self._terminate_worker(terminate_word=True)
            raise RuntimeError(f"无法向 Word worker 发送请求：{error}") from error

        try:
            response = self._receive_response(request_id, self._timeout_seconds)
        except TimeoutError:
            raise
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or "Word 转换失败")
        if not output_file.is_file() or output_file.stat().st_size == 0:
            raise RuntimeError("Word worker 没有生成有效 PDF")
        return output_file

    def cancel(self) -> None:
        if self._direct_application is not None:
            try:
                self._direct_application.Quit()
            finally:
                self._direct_application = None
            return
        self._terminate_worker(terminate_word=True)

    def close(self) -> None:
        if self._direct_application is not None:
            application = self._direct_application
            self._direct_application = None
            application.Quit()
            return

        process = self._process
        if process is None:
            return
        request_id = str(uuid.uuid4())
        graceful_shutdown = False
        try:
            assert process.stdin is not None
            process.stdin.write(
                json.dumps(
                    {"request_id": request_id, "operation": "shutdown"},
                    separators=(",", ":"),
                )
                + "\n"
            )
            process.stdin.flush()
            response = self._receive_response(request_id, self._shutdown_timeout_seconds)
            if not response.get("ok"):
                raise RuntimeError(response.get("error") or "Word worker 关闭失败")
            return_code = process.wait(timeout=self._shutdown_timeout_seconds)
            graceful_shutdown = return_code == 0
        except subprocess.TimeoutExpired as error:
            self._terminate_worker(terminate_word=True)
            raise TimeoutError("关闭 Word worker 超时") from error
        except TimeoutError as error:
            raise TimeoutError("关闭 Word worker 超时") from error
        except (BrokenPipeError, OSError) as error:
            self._terminate_worker(terminate_word=True)
            raise RuntimeError(f"无法关闭 Word worker：{error}") from error
        finally:
            if self._process is process:
                if graceful_shutdown:
                    reader_thread = self._reader_thread
                    self._clear_worker_references()
                    for stream_name in ("stdin", "stdout"):
                        stream = getattr(process, stream_name, None)
                        if stream is not None:
                            try:
                                stream.close()
                            except OSError:
                                pass
                    if reader_thread is not None:
                        reader_thread.join(timeout=1)
                else:
                    self._terminate_worker(terminate_word=True)
        if return_code != 0:
            raise RuntimeError(f"Word worker 异常退出（exit={return_code}）")


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

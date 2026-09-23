from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, TextIO
from ctypes import wintypes


PDF_FORMAT_CODE = 17
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3


def _create_word_application() -> Any:
    try:
        from win32com.client import DispatchEx
    except ImportError as error:
        raise RuntimeError(
            "缺少 Windows Word 转换依赖 pywin32，请先安装后再试"
        ) from error
    return DispatchEx("Word.Application")


def configure_word_application(application: Any) -> None:
    """Configure this dedicated Word instance before opening any document."""
    application.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
    application.Visible = False
    application.DisplayAlerts = 0
    options = getattr(application, "Options", None)
    if options is not None:
        options.UpdateLinksAtOpen = False


def _list_existing_word_pids() -> set[int] | None:
    """Return a process snapshot used to prove that DispatchEx created a new Word."""
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        snapshot = kernel32.CreateToolhelp32Snapshot
        snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        snapshot.restype = wintypes.HANDLE
        process_first = kernel32.Process32FirstW
        process_first.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        process_first.restype = wintypes.BOOL
        process_next = kernel32.Process32NextW
        process_next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        process_next.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        class ProcessEntry32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        handle = snapshot(0x00000002, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if not handle or handle == invalid_handle:
            return None
        pids: set[int] = set()
        try:
            entry = ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            success = process_first(handle, ctypes.byref(entry))
            while success:
                if entry.szExeFile.casefold() == "winword.exe":
                    pids.add(int(entry.th32ProcessID))
                success = process_next(handle, ctypes.byref(entry))
        finally:
            close_handle(handle)
        return pids
    except Exception:
        return None


def _process_identity(pid: int) -> dict[str, int] | None:
    """Read the exact Word process image and creation timestamp for safe cleanup."""
    if os.name != "nt" or pid <= 0:
        return None
    process = None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        process = open_process(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not process:
            return None

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
            return None
        if Path(image_buffer.value).name.casefold() != "winword.exe":
            return None

        class FileTime(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

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
            return None
        creation_value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return {"pid": pid, "creation_time": creation_value}
    except Exception:
        return None
    finally:
        if process:
            try:
                ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(process)
            except Exception:
                pass


def _owned_word_process(
    application: Any,
    preexisting_pids: set[int] | None,
) -> dict[str, int] | None:
    """Attest a fresh Word PID using its HWND and a pre-activation process snapshot."""
    if preexisting_pids is None:
        return None
    try:
        hwnd = int(application.Hwnd)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_window_process = user32.GetWindowThreadProcessId
        get_window_process.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        get_window_process.restype = wintypes.DWORD
        pid = wintypes.DWORD()
        if not get_window_process(hwnd, ctypes.byref(pid)):
            return None
        process_id = int(pid.value)
        if process_id in preexisting_pids:
            return None
        return _process_identity(process_id)
    except Exception:
        return None


def convert_document(
    application: Any,
    input_path: str | Path,
    output_path: str | Path,
) -> Path:
    input_file = Path(input_path).resolve(strict=True)
    output_file = Path(output_path).resolve()
    if not input_file.is_file() or input_file.suffix.lower() not in {".doc", ".docx"}:
        raise ValueError(f"仅支持存在的 .doc/.docx 文件：{input_file}")
    if output_file.exists():
        raise FileExistsError(f"转换目标已存在：{output_file}")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    document = application.Documents.Open(
        str(input_file),
        ConfirmConversions=False,
        ReadOnly=True,
        AddToRecentFiles=False,
        NoEncodingDialog=True,
    )
    try:
        document.ExportAsFixedFormat(str(output_file), PDF_FORMAT_CODE)
    finally:
        document.Close(0)

    if not output_file.is_file() or output_file.stat().st_size == 0:
        output_file.unlink(missing_ok=True)
        raise RuntimeError("Microsoft Word 没有生成有效 PDF")
    return output_file


def _write_response(output_stream: TextIO, response: dict[str, Any]) -> None:
    output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
    output_stream.write("\n")
    output_stream.flush()


def serve(
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    *,
    application_factory: Callable[[], Any] | None = None,
) -> int:
    """Serve one-request-at-a-time conversions in an isolated Python process."""
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    application_factory = application_factory or _create_word_application
    application = None
    exit_code = 0

    try:
        for line in input_stream:
            if not line.strip():
                continue
            request_id: str | None = None
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("worker 请求必须是 JSON 对象")
                request_id = request.get("request_id")
                operation = request.get("operation")
                if operation == "shutdown":
                    _write_response(
                        output_stream,
                        {"request_id": request_id, "ok": True},
                    )
                    break
                if operation != "convert":
                    raise ValueError("worker 操作不受支持")

                if application is None:
                    preexisting_pids = _list_existing_word_pids()
                    application = application_factory()
                    configure_word_application(application)
                    owned_word = _owned_word_process(application, preexisting_pids)
                    _write_response(
                        output_stream,
                        {
                            "request_id": request_id,
                            "event": "word_ready",
                            "word_process": owned_word,
                        },
                    )

                converted = convert_document(
                    application,
                    request.get("input_path", ""),
                    request.get("output_path", ""),
                )
                _write_response(
                    output_stream,
                    {
                        "request_id": request_id,
                        "ok": True,
                        "output_path": str(converted),
                    },
                )
            except Exception as error:
                _write_response(
                    output_stream,
                    {
                        "request_id": request_id,
                        "ok": False,
                        "error": str(error) or error.__class__.__name__,
                    },
                )
    except (BrokenPipeError, OSError):
        exit_code = 1
    finally:
        if application is not None:
            try:
                application.Quit()
            except Exception:
                exit_code = 1
    return exit_code


def main() -> int:
    stdin_reconfigure = getattr(sys.stdin, "reconfigure", None)
    if stdin_reconfigure is not None:
        stdin_reconfigure(encoding="utf-8", errors="strict")
    stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
    if stdout_reconfigure is not None:
        stdout_reconfigure(encoding="utf-8", errors="strict")
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())

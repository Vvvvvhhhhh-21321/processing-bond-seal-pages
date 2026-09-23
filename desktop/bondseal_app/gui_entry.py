from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox

from bondseal_app.runtime import ensure_core_importable


def _read_request(path: Path) -> dict:
    import json

    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    finally:
        # Explorer hands off a unique temporary request. Remove it once consumed.
        path.unlink(missing_ok=True)
    if not isinstance(request, dict):
        raise ValueError("快速合集请求必须是 JSON 对象")
    return request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="债券签署页合集快速生成")
    parser.add_argument("--request-file", type=Path)
    args = parser.parse_args(argv)

    try:
        request = _read_request(args.request_file) if args.request_file else None
    except Exception as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("无法读取请求", str(error), parent=root)
        root.destroy()
        return 1

    ensure_core_importable()
    from bondseal_app.window import QuickBatchWindow

    root = tk.Tk()
    QuickBatchWindow(root, request)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

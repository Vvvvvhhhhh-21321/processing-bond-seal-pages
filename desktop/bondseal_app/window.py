from __future__ import annotations

import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any


_STAGE_LABELS = {
    "hashing": "正在检查所选 Word 文件…",
    "converting": "正在通过 Microsoft Word 转换…",
    "publishing": "正在校验并保存合集…",
    "completed": "处理完成",
}


class QuickBatchWindow:
    def __init__(self, root: tk.Tk, request: dict[str, Any] | None):
        self.root = root
        self.request = request
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.running = False
        self.result: dict[str, Any] | None = None

        root.title("签署页合集快速生成")
        root.geometry("700x480")
        root.minsize(560, 380)
        root.protocol("WM_DELETE_WINDOW", self._close)

        body = ttk.Frame(root, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="签署页合集快速生成",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(anchor="w")
        self.status = tk.StringVar(value="正在准备…")
        ttk.Label(body, textvariable=self.status).pack(anchor="w", pady=(14, 6))

        self.progress = ttk.Progressbar(body, mode="determinate", maximum=1)
        self.progress.pack(fill="x", pady=(0, 10))
        self.current = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.current, wraplength=650).pack(anchor="w")

        ttk.Label(body, text="所选文件 / 处理明细").pack(anchor="w", pady=(14, 4))
        self.details = tk.Text(body, height=12, wrap="word", state="disabled")
        self.details.pack(fill="both", expand=True)

        self.actions = ttk.Frame(body)
        self.actions.pack(fill="x", pady=(14, 0))
        self.cancel_button = ttk.Button(
            self.actions, text="取消处理", command=self._cancel, state="disabled"
        )
        self.cancel_button.pack(side="left")
        self.open_pdf = ttk.Button(
            self.actions, text="打开合集", command=self._open_collection, state="disabled"
        )
        self.open_pdf.pack(side="left")
        self.open_folder = ttk.Button(
            self.actions,
            text="打开输出文件夹",
            command=self._open_batch_folder,
            state="disabled",
        )
        self.open_folder.pack(side="left", padx=(8, 0))
        ttk.Button(self.actions, text="关闭", command=self._close).pack(side="right")

        if request is None:
            self.status.set("选择 Word 文件后，右键使用“生成签署页合集”。")
            self._append("此窗口由 Explorer 右键菜单或 bondseal.exe collect 启动。")
        else:
            files = request.get("files", [])
            self._append(f"共选择 {len(files)} 个 Word 文件：")
            for item in files:
                self._append(f"• {item}")
            self._set_busy(True)
            self.root.after(80, self._start)
        self.root.after(100, self._poll)

    def _append(self, text: str) -> None:
        self.details.configure(state="normal")
        self.details.insert("end", text + "\n")
        self.details.see("end")
        self.details.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self.running = busy
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
            self.cancel_button.configure(state="normal")
        else:
            self.progress.stop()
            self.cancel_button.configure(state="disabled")

    def _start(self) -> None:
        from bond_seal_pages.selected_batch import collect_selected_batch

        def run() -> None:
            try:
                result = collect_selected_batch(
                    self.request,
                    progress=lambda event: self.events.put(("progress", event)),
                    cancel_event=self.cancel_event,
                )
                self.events.put(("success", result))
            except Exception as error:
                if self.cancel_event.is_set() and error.__class__.__name__ == "SelectedBatchCancelledError":
                    self.events.put(("cancelled", None))
                    return
                failures = getattr(error, "failures", None)
                self.events.put(
                    (
                        "failure",
                        {
                            "error": str(error) or error.__class__.__name__,
                            "failures": failures or [],
                        },
                    )
                )

        self.status.set("正在生成合集…")
        threading.Thread(target=run, name="bondseal-collect", daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self._show_progress(payload)
                elif kind == "success":
                    self._show_success(payload)
                elif kind == "failure":
                    self._show_failure(payload)
                elif kind == "cancelled":
                    self._show_cancelled()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _show_progress(self, event: dict[str, Any]) -> None:
        stage = event.get("stage")
        self.status.set(_STAGE_LABELS.get(stage, "正在处理…"))
        total = event.get("total")
        completed = event.get("completed")
        if isinstance(total, int) and total > 0 and isinstance(completed, int):
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=total, value=completed)
        path = event.get("path")
        self.current.set(f"当前文件：{path}" if path else "")

    def _show_success(self, result: dict[str, Any]) -> None:
        self._set_busy(False)
        self.result = result
        self.status.set(
            f"完成：{result.get('selected_count', 0)} 个 Word，"
            f"生成 {result.get('page_count', 0)} 页合集。"
        )
        self.current.set("")
        self._append(f"合集：{result['collection']}")
        self._append(f"处理数据：{result['batch_dir']}")
        self.open_pdf.configure(state="normal")
        self.open_folder.configure(state="normal")

    def _show_failure(self, failure: dict[str, Any]) -> None:
        self._set_busy(False)
        self.status.set("处理失败；未生成完整合集。")
        self.current.set("")
        self._append(f"失败原因：{failure['error']}")
        for item in failure["failures"]:
            self._append(f"• {item.get('path', '')}：{item.get('reason', '')}")

    def _show_cancelled(self) -> None:
        self._set_busy(False)
        self.status.set("已取消；未发布签署页合集。")
        self.current.set("")
        self._append("任务已取消，临时处理数据已清理。")

    def _open_collection(self) -> None:
        if self.result:
            os.startfile(self.result["collection"])

    def _open_batch_folder(self) -> None:
        if self.result:
            os.startfile(self.result["batch_dir"])

    def _cancel(self) -> None:
        if not self.running or self.cancel_event.is_set():
            return
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.status.set("正在取消并清理临时文件…")

    def _close(self) -> None:
        if self.running:
            self.status.set("任务仍在运行，请等处理完成后再关闭窗口。")
            return
        self.root.destroy()

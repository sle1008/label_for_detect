"""Toolbar widget."""

import tkinter as tk
from tkinter import ttk

from utils.constants import UI_TOOLBAR_BG, UI_TEXT_COLOR, UI_PANEL_BG, UI_BORDER


class Toolbar(tk.Frame):
    """Top toolbar with common actions."""

    def __init__(self, parent, callbacks: dict = None):
        super().__init__(parent, bg=UI_BORDER, bd=0)

        self._callbacks = callbacks or {}
        self._inner = tk.Frame(self, bg=UI_TOOLBAR_BG, bd=0)
        self._inner.pack(fill='x', padx=1, pady=1)
        self._setup_ui()

    def _make_button(self, text, action, bg=None, fg=None):
        """Create a styled button."""
        parent = self._inner
        if bg:
            btn = tk.Button(
                parent,
                text=text,
                command=lambda: self._call(action),
                font=("Microsoft YaHei UI", 9),
                relief="flat",
                bd=0,
                padx=8,
                pady=4,
                cursor="hand2",
                bg=bg,
                fg=fg or "white",
                activebackground=bg,
                activeforeground=fg or "white",
            )
        else:
            btn = tk.Button(
                parent,
                text=text,
                command=lambda: self._call(action),
                font=("Microsoft YaHei UI", 9),
                relief="flat",
                bd=0,
                padx=8,
                pady=4,
                cursor="hand2",
                bg=UI_TOOLBAR_BG,
                fg=UI_TEXT_COLOR,
                activebackground=UI_PANEL_BG,
                activeforeground=UI_TEXT_COLOR,
            )
            if fg and fg == "#d9534f":
                btn.config(fg=fg, activeforeground=fg)
        btn.pack(side="left", padx=2, pady=2)
        return btn

    def _setup_ui(self):
        self._make_button("\U0001f4c2 打开目录", "open_dir", bg="#4a90d9", fg="white")
        self._make_button("\U0001f4be 保存", "save", bg="#5cb85c", fg="white")
        self._make_button("\U0001f3f7 加载标签", "load_labels", bg="#7b68ae", fg="white")

        self._make_button("\U0001f916 预标注", "pre_annotate", bg="#f0ad4e", fg="white")
        self._batch_btn = self._make_batch_button()
        self._make_button("\U0001f4e4 导出", "export", bg="#5bc0de", fg="white")

        ttk.Separator(self._inner, orient="vertical").pack(
            side="left", fill="y", padx=6, pady=4
        )

        self._make_button("\u25c0 上一张 (A)", "prev_image")
        self._make_button("下一张 (D) \u25b6", "next_image")

        ttk.Separator(self._inner, orient="vertical").pack(
            side="left", fill="y", padx=6, pady=4
        )

        self._make_button("\u25b2 前框 (Q)", "prev_bbox")
        self._make_button("后框 (E) \u25bc", "next_bbox")

        ttk.Separator(self._inner, orient="vertical").pack(
            side="left", fill="y", padx=6, pady=4
        )

        self._make_button("\u21a9 撤销", "undo")
        self._make_button("\u21aa 重做", "redo")
        self._make_button("\u2716 删除 (Del)", "delete_selected", fg="#d9534f")

        ttk.Separator(self._inner, orient="vertical").pack(
            side="left", fill="y", padx=6, pady=4
        )

        self._make_button("\u26f6 适应窗口 (F)", "fit_window")
        self._make_button("\u2630 标签显示 (T)", "cycle_label_mode")

    def _make_batch_button(self):
        self._batch_running = False
        btn = tk.Button(
            self._inner,
            text="\u26a1 批量预标注",
            command=self._on_batch_button_click,
            font=("Microsoft YaHei UI", 9),
            relief="flat",
            bd=0,
            padx=8,
            pady=4,
            cursor="hand2",
            bg="#d9534f",
            fg="white",
            activebackground="#d9534f",
            activeforeground="white",
        )
        btn.pack(side="left", padx=2, pady=2)
        return btn

    def _on_batch_button_click(self):
        if self._batch_running:
            self._call("stop_batch_pre_annotate")
        else:
            self._call("batch_pre_annotate")

    def set_batch_running(self, running: bool):
        """Toggle batch button between start and stop."""
        self._batch_running = bool(running)
        if self._batch_running:
            self._batch_btn.config(
                text="\u25a0 停止",
                bg="#777777",
                activebackground="#777777",
            )
        else:
            self._batch_btn.config(
                text="\u26a1 批量预标注",
                bg="#d9534f",
                activebackground="#d9534f",
            )

    def _call(self, action: str):
        if action in self._callbacks:
            self._callbacks[action]()

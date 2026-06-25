"""Center Toplevel dialogs on the main application window."""

import tkinter as tk
from tkinter import messagebox
from typing import Optional


def _root_widget(widget: tk.Misc) -> tk.Misc:
    return widget.winfo_toplevel()


def center_toplevel(
    window: tk.Toplevel,
    parent: tk.Misc,
    width: Optional[int] = None,
    height: Optional[int] = None,
):
    """Place a Toplevel at the center of the parent top-level window."""
    window.update_idletasks()
    root = _root_widget(parent)
    root.update_idletasks()

    w = width if width is not None else window.winfo_reqwidth()
    h = height if height is not None else window.winfo_reqheight()

    rx = root.winfo_rootx()
    ry = root.winfo_rooty()
    rw = root.winfo_width()
    rh = root.winfo_height()

    x = rx + max(0, (rw - w) // 2)
    y = ry + max(0, (rh - h) // 2)
    window.geometry(f'{w}x{h}+{x}+{y}')


def setup_modal_dialog(
    dialog: tk.Toplevel,
    parent: tk.Misc,
    width: int,
    height: int,
    *,
    resizable: bool = False,
):
    """Standard modal dialog setup and centering (call after UI is built)."""
    dialog.transient(_root_widget(parent))
    dialog.grab_set()
    dialog.resizable(resizable, resizable)
    dialog.update_idletasks()
    w = max(width, dialog.winfo_reqwidth())
    h = max(height, dialog.winfo_reqheight())
    center_toplevel(dialog, parent, w, h)


def showinfo(parent: tk.Misc, title: str, message: str):
    return messagebox.showinfo(title, message, parent=_root_widget(parent))


def showwarning(parent: tk.Misc, title: str, message: str):
    return messagebox.showwarning(title, message, parent=_root_widget(parent))


def showerror(parent: tk.Misc, title: str, message: str):
    return messagebox.showerror(title, message, parent=_root_widget(parent))


def askyesno(parent: tk.Misc, title: str, message: str) -> bool:
    return messagebox.askyesno(title, message, parent=_root_widget(parent))

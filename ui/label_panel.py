"""Label selection panel."""

import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
from typing import Callable, List, Optional

from core.label_manager import LabelDef, LabelManager
from io_ops.folder_labels import save_labels_txt
from ui.window_utils import setup_modal_dialog, askyesno, showinfo, showwarning, showerror
from utils.constants import UI_SURFACE_BG, UI_TEXT_COLOR, UI_ACCENT


class LabelPanel(ttk.LabelFrame):
    """Right-side panel for selecting label classes."""

    def __init__(self, parent, label_manager: LabelManager,
                 on_class_selected: Callable[[int], None] = None,
                 on_sort_mode_changed: Callable[[bool], None] = None,
                 default_export_path: Callable[[], str] = None,
                 on_labels_exported: Callable[[str], None] = None):
        super().__init__(parent, text='标签类别', padding=4)

        self._label_manager = label_manager
        self._on_class_selected = on_class_selected
        self._on_sort_mode_changed = on_sort_mode_changed
        self._default_export_path = default_export_path
        self._on_labels_exported = on_labels_exported
        self._suppress_select_event = False
        self._visible_labels: List[LabelDef] = []

        self._setup_ui()

    def _setup_ui(self):
        search_frame = ttk.Frame(self)
        search_frame.pack(fill='x', pady=(0, 4))

        ttk.Label(search_frame, text='搜索:', width=5).pack(side='left')
        self._search_var = tk.StringVar()
        self._search_entry = ttk.Entry(search_frame, textvariable=self._search_var)
        self._search_entry.pack(side='left', fill='x', expand=True, padx=(2, 0))
        self._search_var.trace_add('write', self._on_search_changed)

        self._listbox_frame = ttk.Frame(self)
        self._listbox_frame.pack(fill='both', expand=True, pady=(0, 4))

        self._listbox = tk.Listbox(
            self._listbox_frame, selectmode='single',
            font=('Microsoft YaHei UI', 10), activestyle='none',
            bg=UI_SURFACE_BG, fg=UI_TEXT_COLOR,
            selectbackground=UI_ACCENT, selectforeground='white',
            relief='flat', bd=0, highlightthickness=0,
        )
        self._listbox.pack(fill='both', expand=True, side='left')

        scrollbar = ttk.Scrollbar(
            self._listbox_frame, orient='vertical',
            command=self._listbox.yview,
        )
        scrollbar.pack(fill='y', side='right')
        self._listbox.config(yscrollcommand=scrollbar.set)

        self._listbox.bind('<<ListboxSelect>>', self._on_select)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill='x', pady=(4, 0))
        for col in range(4):
            btn_frame.columnconfigure(col, weight=1)

        ttk.Button(
            btn_frame, text='添加标签', command=self._add_label,
        ).grid(row=0, column=0, sticky='ew', padx=(0, 1))
        ttk.Button(
            btn_frame, text='删除标签', command=self._delete_label,
        ).grid(row=0, column=1, sticky='ew', padx=1)
        self._sort_btn = ttk.Button(
            btn_frame, text='切换排序', command=self._toggle_sort,
        )
        self._sort_btn.grid(row=0, column=2, sticky='ew', padx=1)
        ttk.Button(
            btn_frame, text='导出', command=self._export_labels,
        ).grid(row=0, column=3, sticky='ew', padx=(1, 0))

    def set_sort_by_name(self, by_name: bool):
        """Apply persisted sort mode."""
        self._label_manager.sort_by_name = by_name

    def _toggle_sort(self):
        by_name = self._label_manager.toggle_display_sort()
        self.refresh()
        if self._on_sort_mode_changed:
            self._on_sort_mode_changed(by_name)

    def _on_search_changed(self, *_):
        self.refresh()

    def focus_search(self, clear: bool = False):
        """Focus the label search box (optionally clear filter)."""
        if clear:
            self._search_var.set('')
        self._search_entry.focus_set()
        self._search_entry.icursor(tk.END)

    def refresh(self, select_class_id: int = None):
        """Refresh the label list, applying current search filter."""
        query = self._search_var.get().strip().lower()
        all_labels = self._label_manager.labels_for_display()
        if query:
            self._visible_labels = [
                label for label in all_labels
                if query in label.name.lower()
            ]
        else:
            self._visible_labels = list(all_labels)

        self._listbox.delete(0, 'end')
        for label in self._visible_labels:
            text = f'[{label.class_id}] {label.name}'
            self._listbox.insert('end', text)
            self._listbox.itemconfig(
                'end', fg=label.color,
                selectbackground=UI_ACCENT, selectforeground='white',
            )

        target_id = (
            select_class_id
            if select_class_id is not None
            else self._label_manager.current_class_id
        )
        for i, label in enumerate(self._visible_labels):
            if label.class_id == target_id:
                self._listbox.selection_set(i)
                self._listbox.see(i)
                break

    def highlight_class(self, class_id: int):
        """Highlight a class in the list without triggering selection callback."""
        self._suppress_select_event = True
        try:
            self.refresh(select_class_id=class_id)
        finally:
            self._suppress_select_event = False

    def _on_select(self, event=None):
        if self._suppress_select_event:
            return
        selection = self._listbox.curselection()
        if not selection:
            return

        idx = selection[0]
        if idx < len(self._visible_labels):
            label = self._visible_labels[idx]
            self._label_manager.current_class_id = label.class_id
            if self._on_class_selected:
                self._on_class_selected(label.class_id)

    def _add_label(self):
        dialog = tk.Toplevel(self)
        dialog.title('添加标签')

        ttk.Label(dialog, text='标签名称:').pack(padx=10, pady=(10, 2))
        name_entry = ttk.Entry(dialog, width=30)
        name_entry.pack(padx=10, pady=2)
        name_entry.focus_set()

        def confirm():
            name = name_entry.get().strip()
            if name:
                self._label_manager.add_label(name)
                self.refresh()
            dialog.destroy()

        name_entry.bind('<Return>', lambda e: confirm())
        ttk.Button(dialog, text='确定', command=confirm).pack(pady=10)
        setup_modal_dialog(dialog, self, 300, 120)

    def _delete_label(self):
        selection = self._listbox.curselection()
        if not selection:
            return

        idx = selection[0]
        if idx < len(self._visible_labels):
            label = self._visible_labels[idx]
            if askyesno(self, '确认', f'确定删除标签 "{label.name}" 吗？'):
                self._label_manager.remove_label(label.class_id)
                self.refresh()

    def _export_labels(self):
        """Export label definitions to a txt file."""
        if self._label_manager.count() == 0:
            showwarning(self, '提示', '没有可导出的标签')
            return

        initialdir = ''
        initialfile = 'classes.txt'
        if self._default_export_path:
            path_hint = Path(self._default_export_path())
            if path_hint.parent.is_dir():
                initialdir = str(path_hint.parent)
            if path_hint.name:
                initialfile = path_hint.name

        path = filedialog.asksaveasfilename(
            title='导出标签文件',
            defaultextension='.txt',
            filetypes=[('TXT文件', '*.txt'), ('所有文件', '*.*')],
            initialdir=initialdir or None,
            initialfile=initialfile,
            parent=self.winfo_toplevel(),
        )
        if not path:
            return

        export_path = Path(path)
        if export_path.suffix.lower() != '.txt':
            export_path = export_path.with_suffix('.txt')

        try:
            count = save_labels_txt(self._label_manager, export_path)
            if self._on_labels_exported:
                self._on_labels_exported(str(export_path))
            else:
                showinfo(self, '完成', f'已导出 {count} 个标签')
        except Exception as e:
            showerror(self, '错误', f'导出失败: {e}')

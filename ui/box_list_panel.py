"""Annotation box list panel."""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from core.image_item import ImageItem
from core.label_manager import LabelManager
from ui.window_utils import setup_modal_dialog
from utils.constants import UI_TEXT_MUTED

DEFAULT_BOX_LIST_COLUMNS = {'id': 30, 'class': 80, 'conf': 50, 'coords': 120}
_ROW_HEIGHT = 30
_HEADING_HEIGHT = 24


class BoxListPanel(ttk.LabelFrame):
    """Right-side panel showing list of annotations for current image."""
    
    def __init__(self, parent, label_manager: LabelManager,
                 on_bbox_selected: Callable = None,
                 on_bbox_deleted: Callable = None,
                 column_widths: dict = None,
                 on_column_widths_changed: Callable = None):
        super().__init__(parent, text='标注列表', padding=4)
        
        self._label_manager = label_manager
        self._on_bbox_selected = on_bbox_selected
        self._on_bbox_deleted = on_bbox_deleted
        self._on_column_widths_changed = on_column_widths_changed
        self._column_widths = dict(column_widths or DEFAULT_BOX_LIST_COLUMNS)
        self._current_image: Optional[ImageItem] = None
        self._columns = ('id', 'class', 'conf', 'coords')
        
        self._setup_ui()
    
    def _setup_ui(self):
        style = ttk.Style(self)
        style.configure('BoxList.Treeview', rowheight=_ROW_HEIGHT)
        
        self._table_frame = ttk.Frame(self)
        self._table_frame.pack(fill='both', expand=True, side='top')
        self._table_frame.rowconfigure(0, weight=1)
        self._table_frame.columnconfigure(0, weight=1)
        
        self._tree = ttk.Treeview(
            self._table_frame, columns=self._columns, show='headings',
            selectmode='extended', height=1,
            style='BoxList.Treeview',
        )
        
        self._tree.heading('id', text='#')
        self._tree.heading('class', text='类别')
        self._tree.heading('conf', text='置信度')
        self._tree.heading('coords', text='坐标')
        
        for col in self._columns:
            self._tree.column(
                col,
                width=self._column_widths.get(col, DEFAULT_BOX_LIST_COLUMNS[col]),
                anchor='center' if col in ('id', 'conf') else 'w',
                stretch=False,
            )
        
        self._v_scroll = ttk.Scrollbar(
            self._table_frame, orient='vertical', command=self._tree.yview,
        )
        self._h_scroll = ttk.Scrollbar(
            self._table_frame, orient='horizontal', command=self._tree.xview,
        )
        self._tree.configure(
            yscrollcommand=self._on_yscroll,
            xscrollcommand=self._on_xscroll,
        )
        
        self._tree.grid(row=0, column=0, sticky='nsew')
        
        self._tree.bind('<<TreeviewSelect>>', self._on_select)
        self._tree.bind('<ButtonRelease-1>', self._on_column_resize)
        self._tree.bind('<Configure>', lambda e: self.after_idle(self._update_scrollbars))
        self._table_frame.bind('<Configure>', lambda e: self.after_idle(self._update_scrollbars))
        
        self._context_menu = tk.Menu(self, tearoff=0)
        self._context_menu.add_command(label='删除选中', command=self._delete_selected)
        self._context_menu.add_command(label='修改类别', command=self._change_class)
        self._tree.bind('<Button-3>', self._on_right_click)
        
        self._stats_var = tk.StringVar(value='标注数: 0')
        ttk.Label(self, textvariable=self._stats_var,
                 font=('Microsoft YaHei UI', 9),
                 foreground=UI_TEXT_MUTED).pack(fill='x', pady=(4, 0))
    
    def _on_yscroll(self, first, last):
        self._v_scroll.set(first, last)
        self.after_idle(self._update_scrollbars)
    
    def _on_xscroll(self, first, last):
        self._h_scroll.set(first, last)
        self.after_idle(self._update_scrollbars)
    
    def _content_width(self) -> int:
        return sum(int(self._tree.column(c, 'width')) for c in self._columns)
    
    def _visible_row_capacity(self) -> int:
        h = self._tree.winfo_height()
        if h <= 1:
            h = self._table_frame.winfo_height()
        usable = max(1, h - _HEADING_HEIGHT)
        return max(1, usable // _ROW_HEIGHT)
    
    def _update_scrollbars(self):
        if not self._tree.winfo_exists():
            return
        
        row_count = len(self._tree.get_children())
        capacity = self._visible_row_capacity()
        display_rows = min(row_count, capacity) if row_count else 1
        if int(self._tree.cget('height')) != display_rows:
            self._tree.configure(height=display_rows)
        
        tree_w = self._tree.winfo_width()
        content_w = self._content_width()
        need_h = tree_w > 1 and content_w > tree_w
        need_v = row_count > capacity
        
        if need_v:
            self._v_scroll.grid(row=0, column=1, sticky='ns')
        else:
            self._v_scroll.grid_remove()
            self._tree.yview_moveto(0)
        
        if need_h:
            self._h_scroll.grid(row=1, column=0, sticky='ew')
        else:
            self._h_scroll.grid_remove()
            self._tree.xview_moveto(0)
    
    def _on_column_resize(self, event):
        region = self._tree.identify_region(event.x, event.y)
        if region not in ('separator', 'heading'):
            return
        
        widths = self.get_column_widths()
        if widths == self._column_widths:
            self.after_idle(self._update_scrollbars)
            return
        
        self._column_widths = widths
        if self._on_column_widths_changed:
            self._on_column_widths_changed(widths)
        self.after_idle(self._update_scrollbars)
    
    def get_column_widths(self) -> dict:
        return {
            col: int(self._tree.column(col, 'width'))
            for col in self._columns
        }
    
    def set_image(self, image_item: ImageItem):
        self._current_image = image_item
        self.refresh()
    
    def refresh(self):
        for iid in self._tree.selection():
            self._tree.selection_remove(iid)
        self._tree.delete(*self._tree.get_children())
        
        if not self._current_image:
            self._stats_var.set('标注数: 0')
            self.after_idle(self._update_scrollbars)
            return
        
        for i, bbox in enumerate(self._current_image.annotations):
            class_name = self._label_manager.get_name(bbox.class_id)
            conf = f'{bbox.confidence:.2f}' if bbox.confidence < 1.0 else '-'
            coords = f'({bbox.x1:.0f},{bbox.y1:.0f})-({bbox.x2:.0f},{bbox.y2:.0f})'
            
            self._tree.insert('', 'end', iid=str(i),
                            values=(i+1, class_name, conf, coords))
        
        self._stats_var.set(f'标注数: {self._current_image.annotation_count()}')
        self.after_idle(self._update_scrollbars)
    
    def _on_select(self, event):
        if not self._current_image:
            return
        
        selection = self._tree.selection()
        indices = [int(iid) for iid in selection]
        
        self._current_image.deselect_all()
        for idx in indices:
            if 0 <= idx < len(self._current_image.annotations):
                self._current_image.annotations[idx].is_selected = True
        
        if self._on_bbox_selected:
            self._on_bbox_selected()
    
    def _on_right_click(self, event):
        item = self._tree.identify_row(event.y)
        if item:
            self._tree.selection_set(item)
            self._context_menu.post(event.x_root, event.y_root)
    
    def _delete_selected(self):
        if not self._current_image:
            return
        
        selection = self._tree.selection()
        indices = sorted([int(iid) for iid in selection], reverse=True)
        
        for idx in indices:
            if 0 <= idx < len(self._current_image.annotations):
                del self._current_image.annotations[idx]
        
        self._current_image.mark_dirty()
        self.refresh()
        
        if self._on_bbox_deleted:
            self._on_bbox_deleted()
    
    def _change_class(self):
        if not self._current_image:
            return
        
        selection = self._tree.selection()
        if not selection:
            return
        
        dialog = tk.Toplevel(self)
        dialog.title('修改类别')
        
        ttk.Label(dialog, text='选择新类别:').pack(padx=10, pady=(10, 2))
        
        labels = self._label_manager.all_labels()
        names = [f'[{l.class_id}] {l.name}' for l in labels]
        
        combo = ttk.Combobox(dialog, values=names, state='readonly', width=25)
        combo.pack(padx=10)
        if names:
            combo.current(0)
        
        def confirm():
            idx = combo.current()
            if idx >= 0 and idx < len(labels):
                new_class = labels[idx]
                for iid in selection:
                    ann_idx = int(iid)
                    if 0 <= ann_idx < len(self._current_image.annotations):
                        self._current_image.annotations[ann_idx].class_id = new_class.class_id
                        self._current_image.annotations[ann_idx].class_name = new_class.name
                self._current_image.mark_dirty()
                self.refresh()
            dialog.destroy()
        
        ttk.Button(dialog, text='确定', command=confirm).pack(pady=8)
        setup_modal_dialog(dialog, self, 280, 130)

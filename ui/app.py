"""Main application window."""

import os
import sys
import queue
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
from typing import List, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.annotation import BBox
from core.image_item import ImageItem
from core.label_manager import LabelManager
from core.project import Project, ImageFilter
from core.undo_redo import UndoRedoManager, Command, CompoundCommand
from core.config import AppConfig, ConfigManager
from io_ops.image_loader import AsyncImageLoader
from io_ops.label_file_parser import load_annotation_file
from io_ops.yolo_exporter import export_yolo
from io_ops.coco_exporter import export_coco
from io_ops.voc_exporter import export_voc
from io_ops.pre_annotator import PreAnnotator
from ui.canvas_panel import (
    AnnotationCanvas, CanvasMode,
    LABEL_MODE_CYCLE, LABEL_MODE_NAMES,
    LABEL_MODE_FULL, LABEL_MODE_COMPACT, LABEL_MODE_HIDDEN,
)
from ui.toolbar import Toolbar
from ui.label_panel import LabelPanel
from ui.box_list_panel import BoxListPanel
from ui.thumbnail_panel import ThumbnailPanel
from ui.status_bar import StatusBar
from ui.dialogs import ExportDialog, StatisticsDialog, LabelLoadDialog, JumpToImageDialog
from io_ops.annotation_status import (
    get_image_category, is_image_annotated, invalidate_annotation_status,
    load_manual_statuses, save_manual_statuses, preferred_annotation_txt_path,
    label_class_folder_for_image, infer_label_category_from_annotations,
    IMAGE_CATEGORY_ANNOTATED, IMAGE_CATEGORY_UNANNOTATED, IMAGE_CATEGORY_UNCERTAIN,
)
from io_ops.image_files import delete_image_and_labels, delete_annotation_files
from io_ops.annotation_writer import write_yolo_annotations_atomic
from io_ops.folder_labels import (
    detect_class_folder_layout,
    infer_immediate_subfolder_name,
    save_labels_txt,
)
from ui.widgets import ThresholdSlider
from ui.window_utils import showinfo, showwarning, showerror, askyesno
from utils.time_format import format_duration
from utils.paths import get_icon_path, format_image_display_path
from utils.constants import (
    DEFAULT_CONFIDENCE_THRESHOLD, UI_BG_COLOR, UI_PANEL_BG,
    UI_SURFACE_BG, UI_TEXT_COLOR, UI_BORDER, UI_TOOLBAR_BG, UI_ACCENT,
    LEFT_PANEL_WIDTH, RIGHT_PANEL_WIDTH, RIGHT_PANEL_MIN_WIDTH_2K, RIGHT_PANEL_MIN_WIDTH_4K,
    PRELOAD_FORWARD, PRELOAD_BACKWARD,
)


class AnnotationApp(tk.Tk):
    """Main annotation application window."""
    
    def __init__(self):
        super().__init__()
        
        self.title('目标检测标注工具')
        self.minsize(800, 600)
        self._apply_window_icon()
        
        # Start fullscreen
        self.state('zoomed')
        
        # Core components
        self._project = Project()
        self._label_manager = LabelManager()
        self._undo_manager = UndoRedoManager()
        self._image_undo_managers = {}
        self._navigation_undo_stack = []
        self._config_manager = ConfigManager()
        self._config = self._config_manager.load()
        self._image_loader = AsyncImageLoader()
        self._pre_annotator = PreAnnotator()
        self._threshold = DEFAULT_CONFIDENCE_THRESHOLD
        self._scan_generation = 0
        self._restore_dir_path = ''
        self._restore_image_path = ''
        self._restore_image_index = 0
        self._main_thread_queue = queue.Queue()
        self._filter_status_snapshot = {}
        self._pending_refresh = None
        self._label_cache_ready_generation = -1
        self._label_cache_job_generation = 0
        self._label_cache_jobs = set()
        self._label_cache_progress = 0
        self._pane_layout_after_id = None
        self._pane_layout_retry_count = 0
        self._last_displayed_index = -1
        self._last_save_error = ''
        self._label_filter_var = tk.StringVar(value='全部标签')
        self._label_filter_display_to_id = {'全部标签': None}
        self._label_filter_popup = None
        
        # Apply saved geometry
        if self._config.window_geometry:
            try:
                self.geometry(self._config.window_geometry)
            except Exception:
                pass
        
        self._setup_theme()
        self._setup_ui()
        self._setup_menus()
        self._setup_bindings()
        
        # Restore last session after main loop starts
        self.after(100, self._restore_session)
        self._poll_main_thread_queue()
        
        self.protocol('WM_DELETE_WINDOW', self._on_close)
    
    def _apply_window_icon(self):
        """Use app.ico from application root for title bar / taskbar."""
        icon_path = get_icon_path()
        if icon_path.is_file():
            try:
                self.iconbitmap(default=str(icon_path))
            except Exception:
                pass
    
    def _setup_theme(self):
        """Apply unified light-gray styling."""
        self.configure(bg=UI_BG_COLOR)
        
        style = ttk.Style(self)
        style.theme_use('clam')
        
        style.configure('.', background=UI_BG_COLOR, foreground=UI_TEXT_COLOR)
        style.configure('TFrame', background=UI_BG_COLOR)
        style.configure('TLabel', background=UI_BG_COLOR, foreground=UI_TEXT_COLOR)
        style.configure('TLabelframe', background=UI_BG_COLOR, foreground=UI_TEXT_COLOR)
        style.configure('TLabelframe.Label', background=UI_BG_COLOR, foreground=UI_TEXT_COLOR)
        style.configure('TButton', background=UI_SURFACE_BG, foreground=UI_TEXT_COLOR)
        style.map('TButton', background=[('active', UI_PANEL_BG)])
        style.configure('TScrollbar', background=UI_SURFACE_BG, troughcolor=UI_BG_COLOR)
        style.configure('Treeview', background=UI_SURFACE_BG, fieldbackground=UI_SURFACE_BG,
                        foreground=UI_TEXT_COLOR, bordercolor=UI_BORDER)
        style.configure('Treeview.Heading', background=UI_PANEL_BG, foreground=UI_TEXT_COLOR)
        style.configure('Horizontal.TProgressbar', background=UI_ACCENT, troughcolor=UI_PANEL_BG)
        style.configure('TPanedwindow', background=UI_BG_COLOR)
    
    def _setup_ui(self):
        """Setup the UI layout."""
        # Toolbar
        self._toolbar = Toolbar(
            self, callbacks={
                'refresh_dir': self._refresh_directory,
                'save': self._save_current,
                'load_labels': self._load_label_file,
                'prev_image': self._prev_image,
                'next_image': self._next_image,
                'undo': self._undo,
                'redo': self._redo,
                'delete_selected': self._delete_selected,
                'prev_bbox': self._prev_bbox,
                'next_bbox': self._next_bbox,
                'fit_window': self._fit_window,
                'cycle_label_mode': self._cycle_label_mode,
                'pre_annotate': self._pre_annotate_current,
                'batch_pre_annotate': self._batch_pre_annotate,
                'stop_batch_pre_annotate': self._stop_batch_pre_annotate,
                'export': self._export,
            }
        )
        self._toolbar.pack(fill='x', side='top', padx=6, pady=(4, 2))
        
        # Status bar
        self._status_bar = StatusBar(self)
        self._status_bar.pack(fill='x', side='bottom')

        # Center toast popup state
        self._toast_win = None
        self._toast_after_id = None
        
        # Main content — tk.PanedWindow supports minsize (ttk does not)
        self._paned = tk.PanedWindow(
            self, orient=tk.HORIZONTAL,
            sashwidth=5, sashrelief=tk.FLAT,
            bg=UI_BORDER, bd=0, sashpad=2,
        )
        self._paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        
        left_w = self._saved_left_panel_width()
        self._left_wrap = tk.Frame(self._paned, width=left_w, bg=UI_BG_COLOR)
        self._left_wrap.pack_propagate(False)
        self._thumb_panel = ThumbnailPanel(
            self._left_wrap,
            on_image_selected=self._on_thumb_selected,
            path_formatter=self._format_image_path,
            on_context_menu=self._on_image_context_menu,
            on_prev_image=self._prev_image,
            on_next_image=self._next_image,
        )
        self._thumb_panel.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self._paned.add(
            self._left_wrap, minsize=LEFT_PANEL_WIDTH,
            width=left_w, stretch='never',
        )
        
        # Center: Canvas
        self._canvas = AnnotationCanvas(
            self._paned,
            on_annotation_changed=self._on_annotation_changed,
            on_mode_changed=self._on_mode_changed,
            on_new_bbox=self._on_new_bbox,
            on_zoom_changed=self._on_zoom_changed,
            on_geometry_changed=self._on_geometry_changed,
        )
        self._canvas.set_label_manager(self._label_manager)
        self._setup_canvas_context_menu()
        self._canvas.set_image_context_menu_handler(self._on_canvas_image_context_menu)
        self._paned.add(self._canvas, minsize=400, stretch='always')
        
        right_w = self._saved_right_panel_width()
        self._right_wrap = tk.Frame(self._paned, width=right_w, bg=UI_BG_COLOR)
        self._right_wrap.pack_propagate(False)
        
        self._right_paned = ttk.PanedWindow(self._right_wrap, orient='vertical')
        self._right_paned.pack(fill='both', expand=True, padx=2, pady=2)
        
        # Top: label categories (weight 6)
        self._label_pane = ttk.Frame(self._right_paned)
        self._label_panel = LabelPanel(
            self._label_pane, self._label_manager,
            on_class_selected=self._on_class_selected,
            on_sort_mode_changed=self._on_label_sort_changed,
            default_export_path=self._default_label_export_path,
            on_labels_exported=self._on_labels_file_exported,
        )
        self._label_panel.pack(fill='both', expand=True)
        self._right_paned.add(self._label_pane, weight=70)
        
        # Middle: threshold slider (weight 0.4)
        self._threshold_pane = tk.Frame(self._right_paned, bg=UI_BG_COLOR, height=58)
        self._threshold_pane.pack_propagate(False)
        self._threshold_slider = ThresholdSlider(
            self._threshold_pane, value=self._threshold,
            command=self._on_threshold_changed
        )
        self._threshold_slider.pack(fill='x', padx=4, pady=6)
        self._right_paned.add(self._threshold_pane, weight=4)
        
        # Bottom: annotation list (weight 3.5)
        self._box_list_panel = BoxListPanel(
            self._right_paned, self._label_manager,
            on_bbox_selected=self._on_bbox_selected_from_list,
            on_bbox_deleted=self._on_annotation_changed,
            column_widths=self._config.box_list_column_widths,
            on_column_widths_changed=self._on_box_list_columns_changed,
        )
        self._right_paned.add(self._box_list_panel, weight=26)
        
        self._paned.add(
            self._right_wrap, minsize=self._right_panel_min_width(),
            width=right_w, stretch='never',
        )
        self._paned.bind('<ButtonRelease-1>', self._on_main_paned_sash_release)
        self._right_paned.bind('<ButtonRelease-1>', self._on_right_paned_sash_release)
        self.after_idle(self._apply_saved_pane_layout)

    def _main_pane_sash_x(self, index: int) -> int:
        """Horizontal tk.PanedWindow uses sash_coord/sash_place, not sashpos."""
        return int(self._paned.sash_coord(index)[0])

    def _set_main_pane_sash_x(self, index: int, x: int):
        sx, sy = self._paned.sash_coord(index)
        self._paned.sash_place(index, int(x), sy)

    def _cancel_pane_layout_apply(self):
        if self._pane_layout_after_id is not None:
            try:
                self.after_cancel(self._pane_layout_after_id)
            except tk.TclError:
                pass
            self._pane_layout_after_id = None

    def _schedule_pane_layout_retry(self):
        if self._pane_layout_retry_count >= 40:
            return
        self._pane_layout_retry_count += 1
        self._cancel_pane_layout_apply()
        self._pane_layout_after_id = self.after(50, self._apply_saved_pane_layout)

    def _saved_left_panel_width(self) -> int:
        width = int(getattr(self._config, 'left_panel_width', 0) or 0)
        if width < LEFT_PANEL_WIDTH:
            return LEFT_PANEL_WIDTH
        return width

    def _saved_right_panel_width(self) -> int:
        width = int(getattr(self._config, 'right_panel_width', 0) or 0)
        min_width = self._right_panel_min_width()
        if width < min_width:
            return min_width
        return width

    def _right_panel_min_width(self) -> int:
        sw = max(1, self.winfo_screenwidth())
        if sw >= 3500:
            return RIGHT_PANEL_MIN_WIDTH_4K
        return RIGHT_PANEL_MIN_WIDTH_2K

    def _main_paned_sash_width(self) -> int:
        return int(self._paned.cget('sashwidth')) + 2 * int(self._paned.cget('sashpad') or 0)

    def _current_left_panel_width(self) -> int:
        try:
            return max(LEFT_PANEL_WIDTH, self._main_pane_sash_x(0))
        except (tk.TclError, AttributeError, IndexError):
            return self._saved_left_panel_width()

    def _current_right_panel_width(self) -> int:
        try:
            total = max(1, self._paned.winfo_width())
            pos = self._main_pane_sash_x(1)
            width = total - pos - self._main_paned_sash_width()
            return max(self._right_panel_min_width(), width)
        except (tk.TclError, AttributeError, IndexError):
            return self._saved_right_panel_width()

    def _current_right_pane_sash_positions(self) -> List[int]:
        try:
            return [int(self._right_paned.sashpos(0)), int(self._right_paned.sashpos(1))]
        except tk.TclError:
            return []

    def _apply_saved_pane_layout(self):
        """Restore main horizontal and right vertical splitter positions."""
        self._pane_layout_after_id = None
        if not self._paned.winfo_ismapped():
            self._schedule_pane_layout_retry()
            return

        left_w = self._saved_left_panel_width()
        right_w = self._saved_right_panel_width()
        try:
            self._set_main_pane_sash_x(0, left_w)
            self._left_wrap.config(width=left_w)

            total = self._paned.winfo_width()
            if total > 1:
                sash_extra = self._main_paned_sash_width()
                center_min = 400
                pos1 = total - right_w - sash_extra
                pos1 = min(pos1, total - center_min - sash_extra)
                pos1 = max(left_w + center_min + sash_extra, pos1)
                self._set_main_pane_sash_x(1, pos1)
                self._right_wrap.config(width=right_w)
        except (tk.TclError, AttributeError, IndexError):
            pass

        positions = list(getattr(self._config, 'right_pane_sash_positions', None) or [])
        if len(positions) < 2:
            self._pane_layout_retry_count = 0
            return
        if not self._right_paned.winfo_ismapped():
            self._schedule_pane_layout_retry()
            return

        total_h = self._right_paned.winfo_height()
        if total_h <= 1:
            self._schedule_pane_layout_retry()
            return

        min_label, min_threshold, min_box = 100, 52, 100
        y0 = max(min_label, min(int(positions[0]), total_h - min_threshold - min_box))
        y1 = max(y0 + min_threshold, min(int(positions[1]), total_h - min_box))
        try:
            self._right_paned.sashpos(0, y0)
            self._right_paned.sashpos(1, y1)
        except tk.TclError:
            pass
        self._pane_layout_retry_count = 0

    def _persist_pane_layout(self):
        left = self._current_left_panel_width()
        right = self._current_right_panel_width()
        sashes = self._current_right_pane_sash_positions()
        changed = False

        if left != self._config.left_panel_width:
            self._config.left_panel_width = left
            changed = True
        if right != self._config.right_panel_width:
            self._config.right_panel_width = right
            changed = True
        if sashes and sashes != list(self._config.right_pane_sash_positions or []):
            self._config.right_pane_sash_positions = sashes
            changed = True
        if changed:
            self._config_manager.save(self._config)

    def _on_main_paned_sash_release(self, event=None):
        self._persist_pane_layout()

    def _on_right_paned_sash_release(self, event=None):
        self._persist_pane_layout()
    
    def _setup_menus(self):
        """Setup menu bar."""
        menubar = tk.Menu(self)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label='打开目录...', command=self._open_directory, accelerator='Ctrl+O')
        file_menu.add_command(label='刷新目录', command=self._refresh_directory)
        file_menu.add_command(label='加载标签...', command=self._load_label_file)
        file_menu.add_command(label='加载预标注权重...', command=self._load_weights)
        file_menu.add_separator()
        file_menu.add_command(label='保存', command=self._save_current, accelerator='Ctrl+S')
        file_menu.add_command(label='导出...', command=self._export, accelerator='Ctrl+Shift+S')
        file_menu.add_separator()
        file_menu.add_command(label='统计信息...', command=self._show_statistics)
        file_menu.add_separator()
        file_menu.add_command(label='退出', command=self._on_close)
        menubar.add_cascade(label='文件', menu=file_menu)
        
        # Edit menu
        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label='撤销', command=self._undo, accelerator='Ctrl+Z')
        edit_menu.add_command(label='重做', command=self._redo, accelerator='Ctrl+Y')
        edit_menu.add_separator()
        edit_menu.add_command(label='全选', command=self._select_all, accelerator='Ctrl+A')
        edit_menu.add_command(label='反选', command=self._inverse_select, accelerator='Ctrl+I')
        edit_menu.add_command(label='删除选中', command=self._delete_selected, accelerator='Delete')
        menubar.add_cascade(label='编辑', menu=edit_menu)
        
        # Navigation menu
        nav_menu = tk.Menu(menubar, tearoff=0)
        nav_menu.add_command(label='上一张', command=self._prev_image, accelerator='←/A')
        nav_menu.add_command(label='下一张', command=self._next_image, accelerator='→/D')
        nav_menu.add_command(label='第一张', command=self._goto_first, accelerator='Home')
        nav_menu.add_command(label='最后一张', command=self._goto_last, accelerator='End')
        nav_menu.add_separator()
        nav_menu.add_command(label='跳转...', command=self._jump_to_image, accelerator='Ctrl+G')
        menubar.add_cascade(label='导航', menu=nav_menu)
        
        # View menu
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label='适应窗口', command=self._fit_window, accelerator='F')
        view_menu.add_separator()
        self._label_mode_var = tk.StringVar(value=LABEL_MODE_FULL)
        label_mode_menu = tk.Menu(view_menu, tearoff=0)
        label_mode_menu.add_radiobutton(
            label='全部', variable=self._label_mode_var, value=LABEL_MODE_FULL,
            command=lambda: self._set_label_display_mode(LABEL_MODE_FULL),
        )
        label_mode_menu.add_radiobutton(
            label='精简', variable=self._label_mode_var, value=LABEL_MODE_COMPACT,
            command=lambda: self._set_label_display_mode(LABEL_MODE_COMPACT),
        )
        label_mode_menu.add_radiobutton(
            label='隐藏', variable=self._label_mode_var, value=LABEL_MODE_HIDDEN,
            command=lambda: self._set_label_display_mode(LABEL_MODE_HIDDEN),
        )
        view_menu.add_cascade(label='标签显示 (T 循环)', menu=label_mode_menu)
        menubar.add_cascade(label='视图', menu=view_menu)
        
        # Annotation menu
        ann_menu = tk.Menu(menubar, tearoff=0)
        ann_menu.add_command(label='预标注当前图', command=self._pre_annotate_current, accelerator='Ctrl+X')
        ann_menu.add_command(label='批量预标注', command=self._batch_pre_annotate, accelerator='Ctrl+Shift+X')
        ann_menu.add_separator()
        ann_menu.add_command(label='加载已有标注', command=self._load_existing_annotations)
        menubar.add_cascade(label='标注', menu=ann_menu)

        # Image filter menu
        img_menu = tk.Menu(menubar, tearoff=0)
        self._image_filter_var = tk.StringVar(value='all')
        img_menu.add_radiobutton(
            label='全部图片', variable=self._image_filter_var, value='all',
            command=lambda: self._apply_image_filter(ImageFilter.ALL),
        )
        img_menu.add_radiobutton(
            label='已标注', variable=self._image_filter_var, value='annotated',
            command=lambda: self._apply_image_filter(ImageFilter.ANNOTATED),
        )
        img_menu.add_radiobutton(
            label='未标注', variable=self._image_filter_var, value='unannotated',
            command=lambda: self._apply_image_filter(ImageFilter.UNANNOTATED),
        )
        img_menu.add_radiobutton(
            label='不确定', variable=self._image_filter_var, value='uncertain',
            command=lambda: self._apply_image_filter(ImageFilter.UNCERTAIN),
        )
        menubar.add_cascade(label='状态分类', menu=img_menu)

        self._label_filter_menu = tk.Menu(menubar, tearoff=0)
        self._label_filter_var = tk.StringVar(value='全部标签')
        self._label_filter_menu_current = '全部标签'
        self._label_filter_values = []
        self._rebuild_label_filter_menu()
        menubar.add_cascade(label='标签分类', menu=self._label_filter_menu)
        
        # Help - inline entries at the end of menubar
        menubar.add_command(label='快捷键说明', command=self._show_shortcuts)
        menubar.add_command(label='关于', command=self._show_about)
        
        self.config(menu=menubar)
    
    def _setup_canvas_context_menu(self):
        """Right-click menu on canvas when bboxes are selected."""
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label='全选', command=self._select_all)
        menu.add_command(label='反选', command=self._inverse_select)
        menu.add_command(label='删除', command=self._delete_selected)
        menu.add_separator()
        menu.add_command(label='修改标签', command=self._focus_label_search)
        self._canvas.set_context_menu(menu)
    
    def _call_in_main(self, fn):
        """Schedule callback on the Tk main thread (safe from worker threads)."""
        self._main_thread_queue.put(fn)
    
    def _poll_main_thread_queue(self):
        """Drain cross-thread UI callbacks."""
        try:
            while True:
                fn = self._main_thread_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    print(f'Main thread callback error: {exc}')
        except queue.Empty:
            pass
        self.after(50, self._poll_main_thread_queue)
    
    def _focus_label_search(self):
        """Focus label search box to change class of selected boxes."""
        item = self._project.current_image
        if not item or not item.get_selected_annotations():
            return
        self._label_panel.focus_search(clear=True)

    def _on_canvas_image_context_menu(self, event):
        self._show_image_context_menu(event)

    def _on_image_context_menu(self, full_index: int, event):
        self._show_image_context_menu(event, full_index=full_index)

    def _show_image_context_menu(self, event, full_index: int = None):
        if full_index is None:
            full_index = self._project.current_index
        if not (0 <= full_index < len(self._project.image_list)):
            return

        item = self._project.image_list[full_index]
        menu = tk.Menu(self, tearoff=0)
        category = get_image_category(item)
        if category == IMAGE_CATEGORY_ANNOTATED:
            toggle_label = '设为未标注'
            toggle_target = IMAGE_CATEGORY_UNANNOTATED
        elif category == IMAGE_CATEGORY_UNANNOTATED:
            toggle_label = '设为已标注'
            toggle_target = IMAGE_CATEGORY_ANNOTATED
        else:
            toggle_label = '设为已标注'
            toggle_target = IMAGE_CATEGORY_ANNOTATED
        menu.add_command(
            label=toggle_label,
            command=lambda idx=full_index, cat=toggle_target: self._set_image_category(idx, cat),
        )
        menu.add_command(
            label='设为不确定',
            command=lambda idx=full_index: self._set_image_category(
                idx, IMAGE_CATEGORY_UNCERTAIN,
            ),
        )
        menu.add_separator()
        menu.add_command(
            label='删除本图',
            accelerator='Ctrl+Del',
            command=lambda idx=full_index: self._delete_image(idx),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _delete_current_image_shortcut(self, event=None):
        """Delete the currently displayed image (Ctrl+Del)."""
        if self._project.current_index >= 0:
            self._delete_image(self._project.current_index)
        return 'break'

    def _delete_image(self, full_index: int):
        if not (0 <= full_index < len(self._project.image_list)):
            return

        item = self._project.image_list[full_index]
        display_name = self._format_image_path(item)
        if not askyesno(
            self, '删除本图',
            f'确定从本地删除以下图片及其标注文件吗？\n\n{display_name}\n\n此操作不可撤销。',
        ):
            return

        if full_index != self._project.current_index:
            if not self._save_before_navigate():
                return

        ok, err = delete_image_and_labels(item)
        if not ok:
            showerror(self, '删除失败', err or '无法删除图片文件')
            return

        self._image_loader.evict_path(item.path)
        item._pil_image = None
        item.is_loaded = False
        self._filter_status_snapshot.pop(id(item), None)

        was_current = full_index == self._project.current_index
        self._project.remove_image_at(full_index)
        self._refresh_image_list_view(jump='keep', navigate=False)

        if not self._project.image_list:
            self._canvas.clear_all()
            self._box_list_panel.set_image(None)
            self._update_window_title()
            self._status_bar.warning('目录中已无图片')
            return

        if was_current:
            indices = self._project.get_filtered_indices()
            idx = self._project.current_index
            if self._project.image_filter != ImageFilter.ALL and idx not in indices and indices:
                next_idx = self._project.next_filtered_index_after(idx)
                if next_idx is not None:
                    self._project.goto_image(next_idx)
            self._show_current_image_async()
        else:
            self._thumb_panel.set_current_by_full_index(self._project.current_index)

        if err:
            self._status_bar.warning(f'已删除图片，但部分标注文件未删除: {err}')
        else:
            self._status_bar.success(f'已删除: {display_name}')

    def _set_image_category(self, full_index: int, category: str):
        if not (0 <= full_index < len(self._project.image_list)):
            return

        item = self._project.image_list[full_index]
        labels = {
            IMAGE_CATEGORY_ANNOTATED: '已标注',
            IMAGE_CATEGORY_UNANNOTATED: '未标注',
            IMAGE_CATEGORY_UNCERTAIN: '不确定',
        }
        if category == IMAGE_CATEGORY_UNANNOTATED:
            item.clear_annotations()
            delete_annotation_files(item.path)
            item.manual_annotation_status = None
            item._annotations_loaded = True
            item.mark_clean()
        elif category == IMAGE_CATEGORY_ANNOTATED:
            # Persist via a label file; an empty .txt marks a background sample.
            item.manual_annotation_status = None
            item._annotations_loaded = True
            item.mark_dirty()
            if not self._save_item_annotations(item):
                self._report_save_failure(item)
                return
        elif category == IMAGE_CATEGORY_UNCERTAIN:
            item.manual_annotation_status = IMAGE_CATEGORY_UNCERTAIN
        else:
            return

        invalidate_annotation_status(item)
        self._save_manual_statuses()
        msg = f'已设为{labels[category]}: {self._format_image_path(item)}'
        self._filter_status_snapshot[id(item)] = get_image_category(item)
        self._project.invalidate_filter_cache()
        self._refresh_image_list_view(jump='keep', navigate=False)

        if full_index == self._project.current_index:
            self._canvas.refresh()
            self._box_list_panel.refresh()
            self._on_annotation_changed()
        self._status_bar.set_info(msg)
        if category in (IMAGE_CATEGORY_ANNOTATED, IMAGE_CATEGORY_UNANNOTATED):
            self._mark_real_annotation_change(item)
            self._save_manual_statuses()
            if full_index == self._project.current_index:
                self._status_bar.set_info(self._compose_status_text(item))

    def _on_label_search_shortcut(self, event=None):
        """Press S with selection to jump to label search."""
        item = self._project.current_image
        if item and item.get_selected_annotations():
            self._focus_label_search()
            return 'break'
    
    def _setup_bindings(self):
        """Setup keyboard shortcuts."""
        self.bind('<Control-o>', lambda e: self._refresh_directory())
        self.bind('<Control-s>', lambda e: self._save_current())
        self.bind('<Control-S>', lambda e: self._export())  # Ctrl+Shift+S
        self.bind('<Control-z>', lambda e: self._undo())
        self.bind('<Control-y>', lambda e: self._redo())
        self.bind('<Control-a>', lambda e: self._select_all())
        self.bind('<Control-i>', lambda e: self._inverse_select())
        self.bind('<Delete>', lambda e: self._delete_selected())
        self.bind('<Control-Delete>', self._delete_current_image_shortcut)
        self.bind('<Left>', lambda e: self._prev_image())
        self.bind('<Right>', lambda e: self._next_image())
        self.bind('<a>', lambda e: self._prev_image())
        self.bind('<d>', lambda e: self._next_image())
        self.bind('<A>', lambda e: self._prev_image())
        self.bind('<D>', lambda e: self._next_image())
        self.bind('<Home>', lambda e: self._goto_first())
        self.bind('<End>', lambda e: self._goto_last())
        self.bind('<Control-g>', lambda e: self._jump_to_image())
        self.bind('<Control-G>', lambda e: self._jump_to_image())
        self.bind('<f>', lambda e: self._fit_window())
        self.bind('<F>', lambda e: self._fit_window())
        self.bind('<Escape>', lambda e: self._canvas.cancel_or_deselect())
        self.bind('<Control-x>', lambda e: self._pre_annotate_current())
        self.bind('<Control-X>', lambda e: self._batch_pre_annotate())
        
        # Q/E for prev/next annotation box
        self.bind('<q>', lambda e: self._prev_bbox())
        self.bind('<Q>', lambda e: self._prev_bbox())
        self.bind('<e>', lambda e: self._next_bbox())
        self.bind('<E>', lambda e: self._next_bbox())

        # T toggles the label display mode (declutter)
        self.bind('<t>', lambda e: self._cycle_label_mode())
        self.bind('<T>', lambda e: self._cycle_label_mode())
        
        # Number keys 1-9 for quick class selection
        for i in range(1, 10):
            self.bind(str(i), lambda e, idx=i-1: self._quick_select_class(idx))
        
        self._canvas.bind('<s>', self._on_label_search_shortcut)
        self._canvas.bind('<S>', self._on_label_search_shortcut)
        self._canvas.bind('<Control-Delete>', self._delete_current_image_shortcut)

        # Force English IME when the app window gains focus (Windows only)
        self.bind('<FocusIn>', self._force_english_ime)
        self.after(300, self._force_english_ime)

    def _force_english_ime(self, event=None):
        """Switch the IME to English (alphanumeric) when the app is focused.

        Windows-only and fully best-effort: any failure is ignored so it can
        never affect the rest of the app.
        """
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            from ctypes import wintypes

            WM_IME_CONTROL = 0x0283
            IMC_SETCONVERSIONMODE = 0x0002
            IME_CMODE_ALPHANUMERIC = 0x0000

            imm32 = ctypes.windll.imm32
            user32 = ctypes.windll.user32
            imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND
            imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
            user32.SendMessageW.restype = wintypes.LPARAM
            user32.SendMessageW.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            ]

            hwnd = wintypes.HWND(self.winfo_id())
            ime_wnd = imm32.ImmGetDefaultIMEWnd(hwnd)
            if ime_wnd:
                user32.SendMessageW(
                    ime_wnd, WM_IME_CONTROL,
                    IMC_SETCONVERSIONMODE, IME_CMODE_ALPHANUMERIC,
                )
        except Exception:
            pass
    
    # --- Session management ---
    
    def _restore_session(self):
        """Restore last session settings."""
        self._label_manager.sort_by_name = self._config.label_sort_by_name
        self._label_panel.set_sort_by_name(self._config.label_sort_by_name)
        
        # Restore labels
        if self._config.label_definitions:
            self._label_manager.from_dict_list(self._config.label_definitions)
            self._label_panel.refresh()
            self._refresh_label_filter_options()
        
        # Restore last directory
        if self._config.last_directory and Path(self._config.last_directory).is_dir():
            self._load_directory(self._config.last_directory)
        
        # Restore weights
        if self._config.last_weights_file and Path(self._config.last_weights_file).is_file():
            self._pre_annotator.load_weights(self._config.last_weights_file)
        
        self._threshold = self._config.confidence_threshold
        self._threshold_slider.set(self._threshold)
        if self._config.label_mode in LABEL_MODE_CYCLE:
            self._canvas.set_label_mode(self._config.label_mode)
            self._label_mode_var.set(self._config.label_mode)
        self._apply_saved_image_filter()
    
    def _apply_saved_image_filter(self):
        """Restore persisted image filter without navigating away."""
        value = getattr(self._config, 'image_filter', 'all')
        try:
            self._project.image_filter = ImageFilter(value)
        except ValueError:
            self._project.image_filter = ImageFilter.ALL
        self._image_filter_var.set(self._project.image_filter.value)
        self._sync_status_filter_combo()
    
    def _save_session(self):
        """Save current session settings."""
        self._config.window_geometry = self.geometry()
        self._config.confidence_threshold = self._threshold
        self._config.label_definitions = self._label_manager.to_dict_list()
        
        if self._project.image_dir:
            self._config.last_directory = str(self._project.image_dir)
            self._config.add_recent_dir(str(self._project.image_dir))
        
        if self._project.current_image:
            self._config.last_image_path = str(self._project.current_image.path)
            self._config.last_image_index = self._project.current_index
        
        if self._pre_annotator.model_path:
            self._config.last_weights_file = self._pre_annotator.model_path
        
        self._config.label_sort_by_name = self._label_manager.sort_by_name
        self._config.box_list_column_widths = dict(self._box_list_panel.get_column_widths())
        self._config.left_panel_width = self._current_left_panel_width()
        self._config.right_panel_width = self._current_right_panel_width()
        sashes = self._current_right_pane_sash_positions()
        if sashes:
            self._config.right_pane_sash_positions = sashes
        self._config.image_filter = self._project.image_filter.value
        
        self._config_manager.save(self._config)
    
    def _on_box_list_columns_changed(self, widths: dict):
        """Persist annotation list column widths."""
        self._config.box_list_column_widths = dict(widths)
        self._config_manager.save(self._config)
    
    def _apply_manual_statuses(self, dir_path: str):
        """Apply persisted 'uncertain' marks to the freshly-created image list.

        Only 'uncertain' is stored here; annotated/unannotated are derived from
        the presence of the label (.txt) file.
        """
        image_dir = Path(dir_path)
        statuses = load_manual_statuses(image_dir)
        if not statuses:
            return
        for item in self._project.image_list:
            try:
                rel = item.path.relative_to(image_dir).as_posix()
            except ValueError:
                rel = item.path.name
            if statuses.get(rel) == IMAGE_CATEGORY_UNCERTAIN:
                item.manual_annotation_status = IMAGE_CATEGORY_UNCERTAIN

    def _save_manual_statuses(self):
        """Persist only 'uncertain' marks for the current image directory."""
        if not self._project.image_dir:
            return
        image_dir = self._project.image_dir
        statuses: dict = {}
        for item in self._project.image_list:
            if item.manual_annotation_status == IMAGE_CATEGORY_UNCERTAIN:
                try:
                    rel = item.path.relative_to(image_dir).as_posix()
                except ValueError:
                    rel = item.path.name
                statuses[rel] = IMAGE_CATEGORY_UNCERTAIN
        save_manual_statuses(image_dir, statuses)

    def _on_label_sort_changed(self, sort_by_name: bool):
        """Persist label list sort mode."""
        self._config.label_sort_by_name = sort_by_name
        self._config_manager.save(self._config)
        mode = '首字母' if sort_by_name else '序号'
        self._status_bar.set_info(f'标签列表已切换为按{mode}排序')

    def _default_label_export_path(self) -> str:
        if self._config.last_label_file:
            return self._config.last_label_file
        if self._project.image_dir:
            return str(self._project.image_dir / 'classes.txt')
        return 'classes.txt'

    def _on_labels_file_exported(self, path: str):
        self._config.last_label_file = path
        self._config_manager.save(self._config)
        name = Path(path).name
        self._status_bar.set_info(
            f'已导出 {self._label_manager.count()} 个标签到 {name}'
        )
    
    def _load_item_annotations(self, item: ImageItem):
        """Load YOLO annotations for an image into memory."""
        if item._annotations_loaded or not item.is_loaded or item.width <= 0:
            return
        try:
            file_anns = load_annotation_file(
                item.path, self._label_manager,
                img_width=item.width, img_height=item.height,
            )
            if file_anns:
                item.annotations = file_anns
            if item.manual_annotation_status != IMAGE_CATEGORY_UNCERTAIN:
                item.manual_annotation_status = None
            item._annotations_loaded = True
        except Exception:
            item._annotations_loaded = True
    
    def _preload_window_async(self, center_index: int = None):
        """Preload images and annotations for nearby frames into memory."""
        import threading
        
        if not self._project.image_list:
            return
        if center_index is None:
            center_index = self._project.current_index
        
        self._image_loader.preload_neighbors(
            center_index, self._project.image_list,
            forward=PRELOAD_FORWARD, backward=PRELOAD_BACKWARD,
        )
        
        start = max(0, center_index - PRELOAD_BACKWARD)
        end = min(len(self._project.image_list), center_index + PRELOAD_FORWARD + 1)
        items = list(self._project.image_list[start:end])
        
        def bg():
            for item in items:
                try:
                    self._image_loader.load_image_sync(item)
                    self._load_item_annotations(item)
                except Exception:
                    pass
            self.after(0, self._start_label_cache_preload)
        
        threading.Thread(target=bg, daemon=True).start()
    
    def _start_label_cache_preload(self):
        if not self._project.image_list:
            return
        if self._label_cache_ready_generation == self._scan_generation:
            return
        if self._label_cache_job_generation == self._scan_generation and self._label_cache_jobs:
            return
        self._label_cache_job_generation = self._scan_generation
        self._label_cache_jobs.add(self._label_cache_job_generation)
        self._label_cache_progress = 0
        self._status_bar.set_overlay('标签缓存中... 0%')

        import threading
        def bg(gen=self._label_cache_job_generation):
            total = len(self._project.image_list)
            for idx, item in enumerate(self._project.image_list, start=1):
                if self._label_cache_job_generation != gen:
                    return
                try:
                    class_ids = set()
                    if item.annotations:
                        class_ids = {ann.class_id for ann in item.annotations}
                    else:
                        txt = preferred_annotation_txt_path(item.path)
                        if txt.exists():
                            with open(txt, 'r', encoding='utf-8') as f:
                                for line in f:
                                    parts = line.strip().split()
                                    if parts:
                                        class_ids.add(int(float(parts[0])))
                    self._project.cache_label_contains(item.path, class_ids)
                except Exception:
                    self._project.cache_label_contains(item.path, set())
                if total > 0:
                    progress = int(idx * 100 / total)
                    if progress != self._label_cache_progress:
                        self._label_cache_progress = progress
                        self.after(0, lambda p=progress: self._status_bar.set_overlay(f'标签缓存中... {p}%'))
            self.after(0, lambda gen=gen: self._finish_label_cache_preload(gen))
        threading.Thread(target=bg, daemon=True).start()

    def _finish_label_cache_preload(self, gen: int):
        self._label_cache_ready_generation = gen
        self._label_cache_jobs.discard(gen)
        self._label_cache_progress = 100
        self._status_bar.set_info('标签缓存完成')
    
    def _current_image_undo_manager(self) -> UndoRedoManager:
        item = self._project.current_image
        if not item:
            return self._undo_manager
        key = id(item)
        manager = self._image_undo_managers.get(key)
        if manager is None:
            manager = UndoRedoManager()
            self._image_undo_managers[key] = manager
        return manager

    def _save_before_navigate(self, persist_session: bool = False):
        """Save annotations before leaving current image.

        Keep routine image navigation lightweight: annotation files are saved
        immediately, while config/manual-status persistence is deferred to
        explicit save, filter changes, directory changes, and app close.
        """
        if not self._save_current_annotations():
            self._report_save_failure(self._project.current_image)
            return False
        self._persist_current_position()
        if persist_session:
            self._save_manual_statuses()
            self._save_session()
        return True

    def _mark_real_annotation_change(self, item: ImageItem):
        """Mark an item as having real content changes.

        This is intentionally narrower than selection-only UI changes: it is
        used when boxes are added/removed/edited or when image-level category
        changes happen, so an 'uncertain' item can be promoted only after an
        actual change.
        """
        if item and item.manual_annotation_status == IMAGE_CATEGORY_UNCERTAIN:
            item.manual_annotation_status = None
    
    # --- File operations ---
    
    def _open_directory(self):
        """Open image directory."""
        initial = self._config.last_directory or os.path.expanduser('~')
        d = filedialog.askdirectory(initialdir=initial, title='选择图片目录', parent=self)
        if d:
            self._load_directory(d)

    def _refresh_directory(self):
        """Rescan the open directory, clear decode cache, and keep the current image."""
        if not self._project.image_dir:
            showwarning(self, '提示', '请先打开一个目录')
            return

        dir_path = self._project.image_dir
        if not dir_path.is_dir():
            showerror(self, '错误', f'目录不存在或无法访问:\n{dir_path}')
            return

        current = self._project.current_image
        self._pending_refresh = {
            'prior_paths': [item.path for item in self._project.image_list],
            'current_path': current.path if current else None,
            'current_index': self._project.current_index,
        }
        self._load_directory(str(dir_path), from_refresh=True)

    def _load_directory(self, dir_path: str, from_refresh: bool = False):
        """Load images from directory without blocking the UI."""
        import threading
        
        self._status_bar.info(
            f'正在刷新目录: {dir_path}' if from_refresh else f'正在扫描目录: {dir_path}'
        )
        self.update_idletasks()
        
        failed_item = self._save_all_dirty_annotations()
        if failed_item is not None:
            self._report_save_failure(failed_item)
            return
        if from_refresh:
            self._save_manual_statuses()
        self._image_loader.clear_cache()
        
        if not from_refresh:
            # Keep restore hints from config before this load overwrites session fields
            self._restore_dir_path = self._config.last_directory
            self._restore_image_path = self._config.last_image_path
            self._restore_image_index = self._config.last_image_index
        else:
            self._restore_dir_path = ''
            self._restore_image_path = ''
            self._restore_image_index = -1
        
        self._scan_generation += 1
        scan_gen = self._scan_generation
        
        self._project.image_dir = Path(dir_path)
        self._project.image_list.clear()
        self._project.current_index = -1
        self._thumb_panel.clear()
        
        pending_refresh = self._pending_refresh if from_refresh else None

        def _bg_scan():
            try:
                paths = Project.scan_image_paths(dir_path)
            except Exception as e:
                self._call_in_main(lambda err=e: self._on_scan_failed(err, scan_gen))
                return
            
            preloaded = None
            preload_path = None
            if pending_refresh:
                target_idx = Project.resolve_refresh_index(
                    paths,
                    pending_refresh['prior_paths'],
                    pending_refresh['current_path'],
                    pending_refresh['current_index'],
                )
                if 0 <= target_idx < len(paths):
                    preload_path = paths[target_idx]
            else:
                preload_path = self._resolve_preload_path(dir_path, paths)

            if preload_path:
                preloaded = ImageItem(path=preload_path)
                if self._image_loader.load_image_sync(preloaded):
                    self._load_item_annotations(preloaded)
            
            self._call_in_main(
                lambda item=preloaded: self._on_scan_complete(
                    dir_path, paths, scan_gen, item,
                )
            )
        
        threading.Thread(target=_bg_scan, daemon=True).start()
    
    def _resolve_preload_path(self, dir_path: str, paths: list):
        """Pick the image to decode early while the UI thread stays responsive."""
        if not paths:
            return None
        
        if self._restore_dir_path == dir_path:
            if self._restore_image_path:
                target = Path(self._restore_image_path)
                if target.is_file():
                    return target
                for path in paths:
                    if path == target or str(path) == self._restore_image_path:
                        return path
            idx = self._restore_image_index
            if 0 <= idx < len(paths):
                return paths[idx]
        
        return paths[0]
    
    def _merge_preloaded_item(self, preloaded: ImageItem):
        """Copy decoded pixels/annotations from a preload stub onto the list item."""
        for item in self._project.image_list:
            if item.path == preloaded.path:
                item._pil_image = preloaded._pil_image
                item.is_loaded = preloaded.is_loaded
                item.width = preloaded.width
                item.height = preloaded.height
                item.annotations = preloaded.annotations
                item._annotations_loaded = preloaded._annotations_loaded
                return
    
    def _on_scan_failed(self, error: Exception, scan_gen: int):
        if scan_gen != self._scan_generation:
            return
        self._status_bar.error(f'加载目录失败: {error}')
        showerror(self, '错误', f'加载目录失败:\n{error}')
    
    def _on_scan_complete(self, dir_path: str, paths: list, scan_gen: int,
                          preloaded: ImageItem = None):
        if scan_gen != self._scan_generation:
            return
        
        count = self._project.set_image_paths(dir_path, paths)
        self._apply_manual_statuses(dir_path)
        self._filter_status_snapshot.clear()
        self._image_undo_managers.clear()
        self._navigation_undo_stack.clear()

        pending_refresh = self._pending_refresh
        self._pending_refresh = None
        
        if count > 0:
            if preloaded and preloaded.is_loaded:
                self._merge_preloaded_item(preloaded)
            
            self._thumb_panel.set_images(self._project.image_list)
            self._config.last_directory = dir_path
            if not pending_refresh:
                self._config.add_recent_dir(dir_path)
            
            if pending_refresh:
                restore_idx = Project.resolve_refresh_index(
                    paths,
                    pending_refresh['prior_paths'],
                    pending_refresh['current_path'],
                    pending_refresh['current_index'],
                )
                msg_prefix = '刷新完成'
            else:
                restore_idx = self._resolve_last_image_index(dir_path)
                msg_prefix = '扫描完成'

            if restore_idx > 0:
                self._project.goto_image(restore_idx)
                self._status_bar.info(
                    f'{msg_prefix}: {count} 张图片，正在恢复位置 ({restore_idx + 1}/{count})...'
                )
            else:
                self._status_bar.info(
                    f'{msg_prefix}: {count} 张图片，正在加载{"当前" if pending_refresh else "第一"}张...'
                )
            self._refresh_image_list_view(jump='keep', navigate=False)
            self._show_current_image_async()
            self.after(300, lambda: self._preload_window_async(self._project.current_index))
            self._try_auto_folder_labels(dir_path)
        else:
            self._status_bar.warning(f'目录中未找到图片: {dir_path}')
            showwarning(self, '提示', '目录中未找到支持的图片文件')
    
    def _resolve_last_image_index(self, dir_path: str) -> int:
        """Find saved image index when reopening the same directory."""
        if self._restore_dir_path != dir_path:
            return 0
        
        if self._restore_image_path:
            target = Path(self._restore_image_path)
            for i, item in enumerate(self._project.image_list):
                if item.path == target or str(item.path) == self._restore_image_path:
                    return i
        
        idx = self._restore_image_index
        if 0 <= idx < len(self._project.image_list):
            return idx
        return 0
    
    def _persist_current_position(self):
        """Save current image position for next session."""
        if not self._project.current_image:
            return
        self._config.last_image_path = str(self._project.current_image.path)
        self._config.last_image_index = self._project.current_index
        if self._project.image_dir:
            self._config.last_directory = str(self._project.image_dir)
    
    def _try_auto_folder_labels(self, dir_path: str):
        """Auto-import labels on directory open.

        Priority:
          1) An existing ``classes.txt`` in the opened root directory.
          2) Otherwise, class-per-subfolder name detection.
        """
        if self._try_import_root_classes_file(dir_path):
            return

        detection = detect_class_folder_layout(Path(dir_path))
        if not detection.detected:
            return
        
        if detection.confidence == 'high':
            self._apply_folder_labels(detection.class_names, dir_path)
            return
        
        if detection.confidence == 'medium':
            msg = (
                f'检测到 {len(detection.class_names)} 个子文件夹可能为类别名。\n'
                f'{detection.reason}\n\n{detection.tree_summary}\n\n'
                '是否从子文件夹名导入标签？'
            )
            if askyesno(self, '导入标签', msg):
                self._apply_folder_labels(detection.class_names, dir_path)
    
    def _try_import_root_classes_file(self, dir_path: str) -> bool:
        """Import a classes.txt found directly in the opened root directory.

        Returns True when labels were successfully imported from the file.
        """
        classes_file = Path(dir_path) / 'classes.txt'
        if not classes_file.is_file():
            return False

        backup = self._label_manager.to_dict_list()
        self._label_manager.clear()
        try:
            count = self._label_manager.load_from_txt(str(classes_file))
        except Exception as e:
            print(f'Failed to auto-load classes.txt: {e}')
            count = 0

        if count <= 0:
            # Invalid/empty file: restore previous labels and fall back.
            self._label_manager.clear()
            self._label_manager.from_dict_list(backup)
            return False

        self._label_panel.refresh()
        self._refresh_label_filter_options()
        self._config.last_label_file = str(classes_file)
        item = self._project.current_image
        if item:
            self._apply_folder_default_class(item)
        self._status_bar.success(f'已从 classes.txt 导入 {count} 个标签')
        return True

    def _apply_folder_labels(self, names: list, dir_path: str):
        """Load labels from folder names and write classes.txt beside images."""
        count = self._label_manager.load_from_folder_names(names, clear=True)
        self._label_panel.refresh()
        self._refresh_label_filter_options()
        
        label_path = Path(dir_path) / 'classes.txt'
        try:
            save_labels_txt(self._label_manager, label_path)
            self._config.last_label_file = str(label_path)
        except OSError as e:
            print(f'Failed to save classes.txt: {e}')
        
        item = self._project.current_image
        if item:
            self._apply_folder_default_class(item)
        
        self._status_bar.success(
            f'已从子文件夹导入 {count} 个标签，已保存 {label_path.name}'
        )
    
    def _apply_folder_default_class(self, item: ImageItem):
        """When image lives in a class subfolder, select matching label."""
        if not self._project.image_dir:
            return
        folder_name = infer_immediate_subfolder_name(item.path, self._project.image_dir)
        if not folder_name:
            return
        class_id = self._label_manager.find_class_id_by_name(folder_name)
        if class_id is not None:
            self._label_manager.current_class_id = class_id
            self._label_panel.highlight_class(class_id)
    
    def _load_label_file(self):
        """Load labels from subfolders or external file."""
        detection = None
        has_dir = bool(self._project.image_dir and self._project.image_dir.is_dir())
        if has_dir:
            detection = detect_class_folder_layout(self._project.image_dir)
        
        dialog = LabelLoadDialog(self, detection, has_open_directory=has_dir)
        if dialog.result == 'folder' and detection and detection.class_names:
            self._apply_folder_labels(detection.class_names, str(self._project.image_dir))
        elif dialog.result == 'file':
            self._load_label_file_from_disk()
    
    def _load_label_file_from_disk(self):
        """Load label definitions from a txt/yaml file."""
        filetypes = [
            ('标签文件', '*.txt *.yaml *.yml'),
            ('TXT文件', '*.txt'),
            ('YAML文件', '*.yaml *.yml'),
            ('所有文件', '*.*'),
        ]
        path = filedialog.askopenfilename(
            title='选择标签文件', filetypes=filetypes, parent=self,
        )
        if not path:
            return
        
        ext = Path(path).suffix.lower()
        if ext == '.txt':
            count = self._label_manager.load_from_txt(path)
        elif ext in ('.yaml', '.yml'):
            count = self._label_manager.load_from_yaml(path)
        else:
            showerror(self, '错误', '不支持的文件格式')
            return
        
        self._label_panel.refresh()
        self._refresh_label_filter_options()
        self._config.last_label_file = path
        self._status_bar.set_info(f'已加载 {count} 个标签')
    
    def _load_weights(self):
        """Load YOLO model for pre-annotation."""
        filetypes = [
            ('模型文件', '*.pt *.onnx *.engine *.trt'),
            ('PyTorch', '*.pt'),
            ('ONNX', '*.onnx'),
            ('TensorRT', '*.engine *.trt'),
            ('所有文件', '*.*'),
        ]
        path = filedialog.askopenfilename(
            title='选择预标注模型文件', filetypes=filetypes, parent=self,
        )
        if not path:
            return
        
        self._status_bar.set_info('正在加载模型...')
        self.update_idletasks()
        
        if self._pre_annotator.load_weights(path):
            self._config.last_weights_file = path
            self._status_bar.set_info(f'模型加载成功: {Path(path).name}')
        else:
            showerror(
                self,
                '错误',
                '模型加载失败。\n'
                '支持格式: .pt / .onnx / .engine / .trt\n\n'
                '· .onnx 需: pip install onnxruntime\n'
                '· .engine/.trt 需: NVIDIA GPU + CUDA + tensorrt\n'
                '  本机若无 NVIDIA 显卡，请改用 .pt 或 .onnx',
            )
            self._status_bar.set_info('模型加载失败')
    
    # --- Navigation ---

    def _format_image_path(self, item: ImageItem) -> str:
        return format_image_display_path(item.path, self._project.image_dir)

    def _update_window_title(self, item: ImageItem = None):
        if item is None:
            item = self._project.current_image
        if item:
            self.title(f'目标检测标注工具 - {self._format_image_path(item)}')
        else:
            self.title('目标检测标注工具')

    def _filter_hint_text(self) -> str:
        status = self._status_filter_label()
        label = self._label_filter_label()
        if label:
            return f'{status} - {label}' if status else label
        return status

    def _status_filter_label(self) -> str:
        if self._project.image_filter == ImageFilter.ANNOTATED:
            return '已标注'
        if self._project.image_filter == ImageFilter.UNANNOTATED:
            return '未标注'
        if self._project.image_filter == ImageFilter.UNCERTAIN:
            return '不确定'
        return ''

    def _label_filter_label(self) -> str:
        class_id = self._project.label_filter_class_id
        if class_id is None:
            return ''
        return self._label_manager.get_name(class_id)

    def _classification_status_label(self, item: ImageItem) -> str:
        status = self._image_category_label(item)
        label = self._label_filter_label()
        if label:
            return f'{status} - {label}' if status else label
        return status

    def _image_category_label(self, item: ImageItem) -> str:
        labels = {
            IMAGE_CATEGORY_ANNOTATED: '已标注',
            IMAGE_CATEGORY_UNANNOTATED: '未标注',
            IMAGE_CATEGORY_UNCERTAIN: '不确定',
        }
        return labels.get(get_image_category(item), '')

    def _status_image_counts(self) -> tuple:
        """Return (position in visible list, visible total, all total)."""
        indices = self._project.get_visible_indices()
        total_all = self._project.total_images
        if not indices:
            return 0, 0, total_all
        cur = self._project.current_index
        if cur in indices:
            return indices.index(cur) + 1, len(indices), total_all
        return 0, len(indices), total_all

    def _status_kwargs(self, item: ImageItem, **extra) -> dict:
        current_img, total_imgs, total_all = self._status_image_counts()
        filter_suffix = ''
        if self._project.image_filter != ImageFilter.ALL and total_all > 0:
            filter_suffix = f' (总{total_all})'
        return {
            'current_img': current_img,
            'total_imgs': total_imgs,
            'total_suffix': filter_suffix,
            'category': self._classification_status_label(item),
            'img_width': item.width,
            'img_height': item.height,
            'ann_count': item.annotation_count(),
            'zoom': self._canvas._scale,
            **extra,
        }

    def _undo_suffix_text(self) -> str:
        item = self._project.current_image
        if not item:
            return ''
        edit_steps = self._current_image_undo_manager().undo_description
        if edit_steps:
            return f'撤销编辑: {edit_steps}'
        if get_image_category(item) == IMAGE_CATEGORY_UNCERTAIN:
            nav_item = self._peek_navigation_undo()
            if nav_item:
                return f'撤销编辑 | 回退上一张: {nav_item.name}'
        return '撤销编辑'

    def _compose_status_text(self, item: ImageItem, mode: str = '') -> str:
        current_img, total_imgs, total_all = self._status_image_counts()
        filter_suffix = ''
        if self._project.image_filter != ImageFilter.ALL and total_all > 0:
            filter_suffix = f' (总{total_all})'

        parts = []
        if total_imgs > 0:
            parts.append(f'图片: {current_img}/{total_imgs}{filter_suffix}')
        category = self._classification_status_label(item)
        if category:
            parts.append(f'分类: {category}')
        if item.width > 0 and item.height > 0:
            parts.append(f'尺寸: {item.width}x{item.height}')
        parts.append(f'标注: {item.annotation_count()}')
        parts.append(f'缩放: {self._canvas._scale*100:.0f}%')
        if mode:
            parts.append(f'模式: {mode}')

        suffix = self._undo_suffix_text()
        if suffix:
            parts.append(suffix)
        return ' | '.join(parts) if parts else '就绪'

    def _refresh_image_list_view(self, jump: str = 'keep', navigate: bool = True):
        """Refresh left list for the active filter without changing sort order."""
        indices = self._project.get_filtered_indices()
        items = [self._project.image_list[i] for i in indices]
        hint = self._filter_hint_text()
        self._thumb_panel.set_images(items, full_indices=indices, filter_hint=hint)
        self._project.set_visible_indices(indices)

        cur = self._project.current_index
        if jump == 'first' and indices:
            target = indices[0]
            if target != cur:
                if navigate:
                    if not self._save_before_navigate():
                        return
                self._project.goto_image(target)
                if navigate:
                    self._show_current_image_async()
                else:
                    self._thumb_panel.set_current_by_full_index(target)
            else:
                self._thumb_panel.set_current_by_full_index(target)
        elif jump == 'keep':
            if cur in indices:
                self._thumb_panel.set_current_by_full_index(cur)
            elif navigate and indices:
                next_idx = self._project.next_filtered_index_after(cur)
                if next_idx is not None:
                    if not self._save_before_navigate():
                        return
                    self._project.goto_image(next_idx)
                    self._thumb_panel.set_current_by_full_index(next_idx)
                    self._show_current_image_async()
                else:
                    self._thumb_panel.set_current_by_full_index(cur)
            else:
                self._thumb_panel.set_current_by_full_index(cur)

    def _on_status_filter_combo_selected(self, event=None):
        mapping = {
            '全部图片': ImageFilter.ALL,
            '已标注': ImageFilter.ANNOTATED,
            '未标注': ImageFilter.UNANNOTATED,
            '不确定': ImageFilter.UNCERTAIN,
        }
        self._apply_image_filter(mapping.get(self._status_filter_combo.get(), ImageFilter.ALL))

    def _on_label_filter_combo_selected(self, event=None):
        display = self._label_filter_var.get()
        class_id = self._label_filter_display_to_id.get(display)
        self._apply_label_filter(class_id)

    def _sync_status_filter_combo(self):
        labels = {
            ImageFilter.ALL: '全部图片',
            ImageFilter.ANNOTATED: '已标注',
            ImageFilter.UNANNOTATED: '未标注',
            ImageFilter.UNCERTAIN: '不确定',
        }
        if hasattr(self, '_status_filter_combo'):
            self._status_filter_combo.set(labels.get(self._project.image_filter, '全部图片'))

    def _rebuild_label_filter_menu(self):
        self._label_filter_menu.delete(0, 'end')
        self._label_filter_display_to_id = {'全部标签': None}
        labels = self._label_manager.labels_for_display()
        current = self._project.label_filter_class_id

        self._label_filter_menu.add_radiobutton(
            label='全部标签', variable=self._label_filter_var, value='全部标签',
            command=lambda: self._apply_label_filter(None),
        )
        if labels:
            self._label_filter_menu.add_separator()
            if len(labels) <= 40:
                for label in labels:
                    text = f'[{label.class_id}] {label.name}'
                    self._label_filter_display_to_id[text] = label.class_id
                    self._label_filter_menu.add_radiobutton(
                        label=text, variable=self._label_filter_var, value=text,
                        command=lambda cid=label.class_id: self._apply_label_filter(cid),
                    )
            else:
                for start in range(0, len(labels), 40):
                    chunk = labels[start:start + 40]
                    sub_menu = tk.Menu(self._label_filter_menu, tearoff=0)
                    for label in chunk:
                        text = f'[{label.class_id}] {label.name}'
                        self._label_filter_display_to_id[text] = label.class_id
                        sub_menu.add_radiobutton(
                            label=text, variable=self._label_filter_var, value=text,
                            command=lambda cid=label.class_id: self._apply_label_filter(cid),
                        )
                    end = start + len(chunk)
                    self._label_filter_menu.add_cascade(label=f'{start + 1}-{end}', menu=sub_menu)
        self._label_filter_menu.configure(postcommand=self._sync_label_filter_menu)

        if current is not None and self._label_manager.has_class(current):
            current_text = next(
                text for text, class_id in self._label_filter_display_to_id.items()
                if class_id == current
            )
            self._label_filter_var.set(current_text)
            self._label_filter_menu_current = current_text
        else:
            self._project.label_filter_class_id = None
            self._label_filter_var.set('全部标签')
            self._label_filter_menu_current = '全部标签'

    def _sync_label_filter_menu(self):
        self._label_filter_var.set(self._label_filter_menu_current)

    def _refresh_label_filter_options(self):
        self._rebuild_label_filter_menu()

    def _apply_image_filter(self, image_filter: ImageFilter):
        """Switch visible images by annotation status."""
        if self._project.image_filter == image_filter:
            return

        if not self._save_before_navigate(persist_session=True):
            self._image_filter_var.set(self._project.image_filter.value)
            self._sync_status_filter_combo()
            return
        self._project.image_filter = image_filter
        self._project.invalidate_filter_cache()
        self._image_filter_var.set(image_filter.value)
        self._sync_status_filter_combo()
        self._config.image_filter = image_filter.value
        self._config_manager.save(self._config)

        visible = len(self._project.get_filtered_indices())
        total = self._project.total_images

        self._refresh_image_list_view(jump='first')

        self._status_bar.set_info(
            f'图片筛选: {self._filter_hint_text() or "全部图片"} ({visible}/{total})'
        )

    def _apply_label_filter(self, class_id: Optional[int]):
        if self._project.label_filter_class_id == class_id:
            return

        if not self._save_before_navigate(persist_session=True):
            self._sync_label_filter_menu()
            return
        self._project.label_filter_class_id = class_id
        self._project.invalidate_filter_cache()
        self._refresh_label_filter_options()
        self._label_filter_menu_current = '全部标签' if class_id is None else next(
            (text for text, cid in self._label_filter_display_to_id.items() if cid == class_id),
            '全部标签',
        )
        self._status_bar.set_info('标签筛选更新中...')

        visible = len(self._project.get_filtered_indices())
        total = self._project.total_images
        self._refresh_image_list_view(jump='first')
        self._status_bar.set_info(
            f'图片筛选: {self._filter_hint_text() or "全部图片"} ({visible}/{total})'
        )
    
    def _push_navigation_undo(self, item: ImageItem):
        if item and (not self._navigation_undo_stack or self._navigation_undo_stack[-1] is not item):
            self._navigation_undo_stack.append(item)

    def _pop_navigation_undo(self):
        while self._navigation_undo_stack:
            item = self._navigation_undo_stack.pop()
            if item in self._project.image_list:
                return item
        return None

    def _peek_navigation_undo(self):
        while self._navigation_undo_stack:
            item = self._navigation_undo_stack[-1]
            if item in self._project.image_list:
                return item
            self._navigation_undo_stack.pop()
        return None

    def _prev_image(self):
        current = self._project.current_image
        if current:
            if not self._save_before_navigate():
                return
        if self._project.prev_image():
            if current:
                self._push_navigation_undo(current)
            self._show_current_image_async()
    
    def _next_image(self):
        current = self._project.current_image
        if current:
            if not self._save_before_navigate():
                return
        if self._project.next_image():
            if current:
                self._push_navigation_undo(current)
            self._show_current_image_async()
    
    def _goto_first(self):
        current = self._project.current_image
        if current:
            if not self._save_before_navigate():
                return
        if self._project.goto_first():
            if current:
                self._push_navigation_undo(current)
            self._show_current_image_async()
    
    def _goto_last(self):
        current = self._project.current_image
        if current:
            if not self._save_before_navigate():
                return
        if self._project.goto_last():
            if current:
                self._push_navigation_undo(current)
            self._show_current_image_async()

    def _jump_to_image(self):
        """Jump to a 1-based index in the currently visible image list."""
        indices = self._project.get_visible_indices()
        if not indices:
            showwarning(self, '提示', '当前没有可跳转的图片')
            return

        current_pos, total, _ = self._status_image_counts()
        dialog = JumpToImageDialog(self, total=total, current=current_pos or 1)
        if not dialog.result:
            return

        target_pos = dialog.result
        if not (1 <= target_pos <= total):
            showwarning(self, '提示', f'请输入 1 到 {total} 之间的序号')
            return

        if not self._save_before_navigate():
            return
        if self._project.goto_image(indices[target_pos - 1]):
            self._show_current_image_async()
    
    def _prev_bbox(self):
        """Select the previous annotation box."""
        item = self._project.current_image
        if not item or not item.annotations:
            return
        
        # Find current selection
        selected = item.get_selected_annotations()
        if not selected:
            # Select last box
            item.deselect_all()
            item.annotations[-1].is_selected = True
        else:
            # Find the index of the first selected box and select previous
            current_idx = item.annotations.index(selected[0])
            item.deselect_all()
            prev_idx = (current_idx - 1) % len(item.annotations)
            item.annotations[prev_idx].is_selected = True
        
        self._canvas.refresh()
        self._box_list_panel.refresh()
        self._status_bar.set_info(
            f'已选择标注框 {item.annotations.index(item.get_selected_annotations()[0]) + 1}/{len(item.annotations)}'
        )
    
    def _next_bbox(self):
        """Select the next annotation box."""
        item = self._project.current_image
        if not item or not item.annotations:
            return
        
        # Find current selection
        selected = item.get_selected_annotations()
        if not selected:
            # Select first box
            item.deselect_all()
            item.annotations[0].is_selected = True
        else:
            # Find the index of the last selected box and select next
            current_idx = item.annotations.index(selected[-1])
            item.deselect_all()
            next_idx = (current_idx + 1) % len(item.annotations)
            item.annotations[next_idx].is_selected = True
        
        self._canvas.refresh()
        self._box_list_panel.refresh()
        self._status_bar.set_info(
            f'已选择标注框 {item.annotations.index(item.get_selected_annotations()[0]) + 1}/{len(item.annotations)}'
        )
    
    def _on_thumb_selected(self, index: int):
        if not self._save_before_navigate():
            self._thumb_panel.set_current_by_full_index(self._project.current_index)
            return
        if self._project.goto_image(index):
            self._show_current_image_async()
    
    def _show_current_image_async(self):
        """Display current image using async loading (non-blocking)."""
        item = self._project.current_image
        if not item:
            return
        
        # If already loaded, display immediately
        if item.is_loaded and item._pil_image:
            self._display_current_image()
            return
        
        # Load asynchronously
        self._canvas.clear_all()
        self._status_bar.info(f'正在加载: {item.name}')
        
        def _on_loaded(image_item, success):
            if success and self._project.current_image == image_item:
                self._display_current_image()
            elif not success:
                self._status_bar.error(f'加载失败: {image_item.name}')
        
        self._image_loader.load_image_async(
            item, _on_loaded, main_thread_schedule=self._call_in_main,
        )
    
    def _display_current_image(self):
        """Actually display the loaded image on canvas (must be called on main thread)."""
        item = self._project.current_image
        if not item:
            return

        self._last_displayed_index = self._project.current_index
        
        if not (item.is_loaded and item._pil_image):
            self._canvas.clear_all()
            return
        
        # Load annotations before drawing so boxes appear with the image
        self._load_item_annotations(item)
        
        self._canvas.set_image(item, item._pil_image)
        
        self._image_loader.release_outside_window(
            self._project.image_list,
            self._project.current_index,
            forward=PRELOAD_FORWARD,
            backward=PRELOAD_BACKWARD,
        )
        
        # Preload nearby images after the current frame is shown
        idx = self._project.current_index
        self.after(300, lambda i=idx: self._preload_window_async(i))
        
        # Update panels
        self._thumb_panel.set_current_by_full_index(self._project.current_index)
        self._box_list_panel.set_image(item)
        self._label_panel.refresh()
        self._apply_folder_default_class(item)
        self._persist_current_position()
        if self._project.image_filter != ImageFilter.ALL:
            self._filter_status_snapshot[id(item)] = get_image_category(item)
        
        # Update status
        mode_name = {
            CanvasMode.IDLE: '选择',
            CanvasMode.DRAWING: '绘制'
        }.get(self._canvas.mode, '')
        
        self._status_bar.set_info(
            self._compose_status_text(item, mode=mode_name)
        )
        
        # Update title
        self._update_window_title(item)
    
    # --- Annotation operations ---
    
    def _on_annotation_changed(self):
        """Called when annotations are modified."""
        item = self._project.current_image
        if item:
            selected = item.get_selected_annotations()
            if len(selected) == 1:
                self._label_panel.highlight_class(selected[0].class_id)
            if item.manual_annotation_status == IMAGE_CATEGORY_UNCERTAIN and item.is_dirty:
                item.manual_annotation_status = None
                self._save_manual_statuses()
            self._box_list_panel.refresh()
            self._status_bar.set_info(self._compose_status_text(item))
            if self._project.image_filter != ImageFilter.ALL:
                now = get_image_category(item)
                item_key = id(item)
                prev = self._filter_status_snapshot.get(item_key)
                self._filter_status_snapshot[item_key] = now
                if prev is not None and prev != now:
                    self._project.invalidate_filter_cache()
    
    def _on_new_bbox(self, bbox: BBox):
        """Handle new bbox drawn on canvas - with undo support."""
        item = self._project.current_image
        if not item:
            return
        
        new_bbox = bbox
        item.deselect_all()
        item.add_annotation(new_bbox)
        
        def execute():
            if new_bbox not in item.annotations:
                item.add_annotation(new_bbox)
            item.mark_dirty()
            self._canvas.refresh()
            self._on_annotation_changed()
        
        def undo():
            if new_bbox in item.annotations:
                item.annotations.remove(new_bbox)
            item.mark_dirty()
            self._canvas.refresh()
            self._on_annotation_changed()
        
        self._mark_real_annotation_change(item)
        cmd = Command(
            description=f'添加标注框 [{new_bbox.class_name}]',
            execute=execute, undo=undo
        )
        self._current_image_undo_manager().record(cmd)
        self._canvas.refresh()
        self._on_annotation_changed()
    
    def _on_geometry_changed(self, description: str, changes: list):
        """Register undo for bbox move/resize (already applied on canvas)."""
        item = self._project.current_image
        if not item or not changes:
            return
        
        def apply_coords(bbox, coords):
            bbox.x1, bbox.y1, bbox.x2, bbox.y2 = coords
        
        def execute():
            for bbox, _, after in changes:
                apply_coords(bbox, after)
            item.mark_dirty()
            self._canvas.refresh()
            self._on_annotation_changed()
        
        def undo():
            for bbox, before, _ in changes:
                apply_coords(bbox, before)
            item.mark_dirty()
            self._canvas.refresh()
            self._on_annotation_changed()
        
        self._mark_real_annotation_change(item)
        cmd = Command(description=description, execute=execute, undo=undo)
        self._current_image_undo_manager().record(cmd)
        self._on_annotation_changed()
    
    def _on_mode_changed(self, mode: CanvasMode):
        """Called when canvas mode changes."""
        mode_names = {
            CanvasMode.IDLE: '选择',
            CanvasMode.DRAWING: '绘制',
            CanvasMode.MOVING: '移动',
            CanvasMode.RESIZING: '缩放',
            CanvasMode.SELECTING: '框选',
            CanvasMode.PANNING: '平移'
        }
        item = self._project.current_image
        if item:
            self._status_bar.update_status(
                **self._status_kwargs(
                    item, mode=mode_names.get(mode, ''),
                )
            )
    
    def _on_zoom_changed(self, zoom: float):
        """Lightweight status update during zoom (no full panel refresh)."""
        item = self._project.current_image
        if not item:
            return
        self._status_bar.update_status(**self._status_kwargs(item, zoom=zoom))
    
    def _on_class_selected(self, class_id: int):
        """Apply selected label to selected bboxes, or set default for new boxes."""
        item = self._project.current_image
        if not item:
            return
        
        selected = item.get_selected_annotations()
        if not selected:
            return
        
        class_name = self._label_manager.get_name(class_id)
        snapshots = []
        for bbox in selected:
            if bbox.class_id != class_id or bbox.class_name != class_name:
                snapshots.append((bbox, bbox.class_id, bbox.class_name))
                bbox.class_id = class_id
                bbox.class_name = class_name
        
        if not snapshots:
            return
        
        item.mark_dirty()
        
        def execute():
            for bbox, _, _ in snapshots:
                bbox.class_id = class_id
                bbox.class_name = class_name
            item.mark_dirty()
            self._canvas.refresh()
            self._box_list_panel.refresh()
            self._on_annotation_changed()
        
        def undo():
            for bbox, old_id, old_name in snapshots:
                bbox.class_id = old_id
                bbox.class_name = old_name
            item.mark_dirty()
            self._canvas.refresh()
            self._box_list_panel.refresh()
            self._on_annotation_changed()
        
        self._mark_real_annotation_change(item)
        cmd = Command(
            description=f'修改类别为 [{class_id}] {class_name}',
            execute=execute, undo=undo,
        )
        self._current_image_undo_manager().record(cmd)
        self._canvas.refresh()
        self._box_list_panel.refresh()
        self._on_annotation_changed()
        self._status_bar.set_info(
            f'已将 {len(selected)} 个锚框类别改为 [{class_id}] {class_name}'
        )
    
    def _on_bbox_selected_from_list(self):
        item = self._project.current_image
        if item:
            selected = item.get_selected_annotations()
            if len(selected) == 1:
                self._label_panel.highlight_class(selected[0].class_id)
        self._canvas.refresh()
    
    def _on_threshold_changed(self, value: float):
        self._threshold = value
        self._config.confidence_threshold = value
    
    def _quick_select_class(self, index: int):
        """Quick select class by number key (1-9)."""
        labels = self._label_manager.labels_for_display()
        if index < len(labels):
            self._label_manager.current_class_id = labels[index].class_id
            self._label_panel.refresh()
            self._on_class_selected(labels[index].class_id)
    
    def _select_all(self):
        item = self._project.current_image
        if item:
            item.select_all()
            self._canvas.refresh()
            self._box_list_panel.refresh()
    
    def _inverse_select(self):
        item = self._project.current_image
        if item:
            item.inverse_selection()
            self._canvas.refresh()
            self._box_list_panel.refresh()
    
    def _delete_selected(self):
        item = self._project.current_image
        if not item:
            return
        
        selected = item.get_selected_annotations()
        if not selected:
            return
        
        # Create undo command
        indices = []
        for ann in selected:
            idx = item.annotations.index(ann)
            indices.append((idx, ann.copy()))
        
        def execute():
            item.remove_selected()
            self._canvas.refresh()
            self._on_annotation_changed()
        
        def undo():
            for idx, ann in sorted(indices):
                item.annotations.insert(idx, ann)
            item.mark_dirty()
            self._canvas.refresh()
            self._on_annotation_changed()
        
        self._mark_real_annotation_change(item)
        cmd = Command(
            description=f'删除 {len(selected)} 个标注框',
            execute=execute, undo=undo
        )
        self._current_image_undo_manager().execute(cmd)
    
    # --- Undo/Redo ---
    
    def _undo(self):
        manager = self._current_image_undo_manager()
        desc = manager.undo() if manager.can_undo() else None
        if desc:
            self._canvas.refresh()
            self._on_annotation_changed()
            if self._project.current_image:
                self._status_bar.set_info(self._compose_status_text(self._project.current_image, mode=''))
            return

        current = self._project.current_image
        if current and get_image_category(current) == IMAGE_CATEGORY_UNCERTAIN:
            item = self._peek_navigation_undo()
            if item and item in self._project.image_list:
                self._pop_navigation_undo()
                if self._project.goto_image(self._project.image_list.index(item)):
                    self._show_current_image_async()
                    return

        self._status_bar.set_info('没有可撤销内容')
    
    def _redo(self):
        manager = self._current_image_undo_manager()
        desc = manager.redo() if manager.can_redo() else None
        if desc:
            self._canvas.refresh()
            self._on_annotation_changed()
            if self._project.current_image:
                self._status_bar.set_info(self._compose_status_text(self._project.current_image, mode=''))
            return
        self._status_bar.set_info('当前图没有可重做内容')
    
    # --- Save ---
    
    def _save_current(self):
        """Save current annotations."""
        saved = self._save_current_annotations()
        if not saved:
            self._report_save_failure(self._project.current_image)
            return
        self._save_session()
        self._status_bar.set_info('已保存')
    
    def _save_item_annotations(self, item: ImageItem) -> bool:
        """Save YOLO format annotations for one image."""
        if not item or not item.is_dirty:
            return True

        self._last_save_error = ''
        
        w, h = item.width, item.height
        if w <= 0 or h <= 0:
            try:
                from PIL import Image
                with Image.open(item.path) as img:
                    w, h = img.size
            except Exception as e:
                self._last_save_error = f'无法读取图片尺寸: {e}'
                print(f"Failed to read image size for {item.path}: {e}")
                return False
        
        txt_path = preferred_annotation_txt_path(item.path)
        class_name = infer_label_category_from_annotations([ann.class_name for ann in item.annotations])
        class_dir = label_class_folder_for_image(item.path)
        new_image_path = item.path
        if class_name and class_dir is not None:
            txt_path = class_dir / f'{item.stem}.txt'
            if item.path.parent.parent.name.lower() == 'images':
                new_image_path = item.path.parent.parent.parent / 'images' / class_name / item.name
            elif item.path.parent.name.lower() == 'images':
                new_image_path = item.path.parent.parent / 'images' / class_name / item.name
            else:
                new_image_path = item.path.parent / class_name / item.name
        try:
            old_txt_path = preferred_annotation_txt_path(item.path)
            txt_path.parent.mkdir(parents=True, exist_ok=True)
            if new_image_path != item.path:
                new_image_path.parent.mkdir(parents=True, exist_ok=True)
                item.path.replace(new_image_path)
                item.path = new_image_path
            write_yolo_annotations_atomic(txt_path, item.annotations, w, h)
            if old_txt_path != txt_path and old_txt_path.exists():
                try:
                    old_txt_path.unlink()
                except Exception:
                    pass
            item.mark_clean()
            self._project.cache_label_contains(
                item.path, {annotation.class_id for annotation in item.annotations},
            )
            return True
        except Exception as e:
            self._last_save_error = str(e)
            print(f"Failed to save annotations for {item.path}: {e}")
            return False
    
    def _save_current_annotations(self):
        """Save current YOLO annotations and report whether the write succeeded."""
        item = self._project.current_image
        if not item:
            return True
        return self._save_item_annotations(item)

    def _save_all_dirty_annotations(self) -> Optional[ImageItem]:
        """Save every modified item, returning the first item that could not be saved."""
        for item in self._project.image_list:
            if item.is_dirty and not self._save_item_annotations(item):
                return item
        return None

    def _report_save_failure(self, item: Optional[ImageItem]):
        """Keep the current image active when its annotation file cannot be saved."""
        name = self._format_image_path(item) if item else '当前图片'
        detail = self._last_save_error or '未知写盘错误'
        message = f'标注保存失败，已取消切换或关闭操作。\n\n{name}\n\n{detail}'
        self._status_bar.error(f'保存失败: {name}')
        showerror(self, '保存失败', message)
    
    # --- View ---
    
    def _fit_window(self):
        self._canvas.fit_to_window()
        self._on_zoom_changed(self._canvas._scale)

    def _cycle_label_mode(self):
        """Cycle bbox label display mode (toolbar button / T key)."""
        prev = self._canvas.label_mode
        mode = self._canvas.cycle_label_mode()
        self._apply_label_mode_change(prev, mode)

    def _set_label_display_mode(self, mode: str):
        """Set a specific label display mode from the View menu."""
        prev = self._canvas.label_mode
        self._canvas.set_label_mode(mode)
        self._apply_label_mode_change(prev, mode)

    def _apply_label_mode_change(self, prev: str, mode: str):
        """Sync menu state, persist, and report the mode transition."""
        self._label_mode_var.set(mode)
        self._config.label_mode = mode
        self._config_manager.save(self._config)
        prev_name = LABEL_MODE_NAMES.get(prev, prev)
        name = LABEL_MODE_NAMES.get(mode, mode)
        if prev != mode:
            transition = f'{prev_name} → {name}'
            self._status_bar.set_info(f'标签显示: {transition}')
            self._show_center_toast(transition)
        else:
            self._status_bar.set_info(f'标签显示: {name}')

    def _show_center_toast(self, text: str, duration_ms: int = 1000):
        """Show a brief centered popup over the main window."""
        if self._toast_after_id is not None:
            try:
                self.after_cancel(self._toast_after_id)
            except Exception:
                pass
            self._toast_after_id = None
        if self._toast_win is not None:
            try:
                self._toast_win.destroy()
            except Exception:
                pass
            self._toast_win = None

        try:
            win = tk.Toplevel(self)
            win.overrideredirect(True)
            win.attributes('-topmost', True)
            try:
                win.attributes('-alpha', 0.92)
            except Exception:
                pass
            frame = tk.Frame(win, bg='#222222', highlightthickness=1,
                             highlightbackground='#555555')
            frame.pack(fill='both', expand=True)
            tk.Label(
                frame, text=text, font=('Microsoft YaHei UI', 18, 'bold'),
                bg='#222222', fg='#ffffff', padx=28, pady=16,
            ).pack()

            win.update_idletasks()
            w, h = win.winfo_width(), win.winfo_height()
            x = self.winfo_rootx() + (self.winfo_width() - w) // 2
            y = self.winfo_rooty() + (self.winfo_height() - h) // 2
            win.geometry(f'+{x}+{y}')

            self._toast_win = win
            self._toast_after_id = self.after(duration_ms, self._hide_center_toast)
        except Exception:
            self._toast_win = None
            self._toast_after_id = None

    def _hide_center_toast(self):
        self._toast_after_id = None
        if self._toast_win is not None:
            try:
                self._toast_win.destroy()
            except Exception:
                pass
            self._toast_win = None
    
    # --- Pre-annotation ---
    
    def _pre_annotate_current(self):
        """Run pre-annotation on current image (background thread)."""
        if self._pre_annotator.is_busy:
            self._status_bar.warning('预标注进行中，请稍候...')
            return
        
        if not self._pre_annotator.is_loaded:
            showinfo(self, '提示', '请先加载YOLOv8权重文件')
            self._load_weights()
            return
        
        item = self._project.current_image
        if not item or not item.is_loaded:
            return
        
        self._status_bar.set_info('正在预标注...')
        
        def on_done(annotations, elapsed):
            if self._project.current_image is not item:
                self._status_bar.info('预标注完成（当前已切换图片，结果未应用）')
                return
            
            count = len(annotations)
            time_text = format_duration(elapsed)
            
            if annotations:
                def execute():
                    for ann in annotations:
                        item.add_annotation(ann.copy())
                    self._canvas.refresh()
                    self._on_annotation_changed()
                
                def undo():
                    for ann in annotations:
                        if ann in item.annotations:
                            item.annotations.remove(ann)
                    item.mark_dirty()
                    self._canvas.refresh()
                    self._on_annotation_changed()
                
                self._mark_real_annotation_change(item)
                cmd = Command(
                    description=f'预标注 {count} 个框',
                    execute=execute, undo=undo,
                )
                self._current_image_undo_manager().execute(cmd)
            
            self._status_bar.success(
                f'预标注完成: {count} 个目标, 耗时 {time_text}'
            )
        
        self._pre_annotator.predict_async(
            item.path, self._threshold, self._label_manager,
            on_done, main_thread_schedule=self._call_in_main,
        )
    
    def _apply_batch_annotations_to_item(self, item: ImageItem, annotations: list):
        """Merge batch prediction results into one image item."""
        if not annotations:
            return
        for ann in annotations:
            item.add_annotation(ann.copy())
        item.mark_dirty()
        item._annotations_loaded = True
        self._save_item_annotations(item)

    def _refresh_current_image_view(self):
        """Redraw canvas and side panels for the current image."""
        item = self._project.current_image
        if not item:
            return
        self._canvas.refresh()
        self._box_list_panel.set_image(item)
        self._on_annotation_changed()
    
    def _batch_pre_annotate(self):
        """Run batch pre-annotation on images in the current filter (background thread)."""
        if self._pre_annotator.is_busy:
            self._status_bar.warning('预标注进行中，请稍候...')
            return
        
        if not self._pre_annotator.is_loaded:
            showinfo(self, '提示', '请先加载YOLOv8权重文件')
            self._load_weights()
            return
        
        if not self._project.has_images:
            showwarning(self, '提示', '请先打开图片目录')
            return
        
        filter_labels = {
            ImageFilter.ALL: '全部图片',
            ImageFilter.ANNOTATED: '已标注',
            ImageFilter.UNANNOTATED: '未标注',
            ImageFilter.UNCERTAIN: '不确定',
        }
        indices = self._project.get_visible_indices()
        targets = [self._project.image_list[i] for i in indices]
        total = len(targets)
        label = filter_labels[self._project.image_filter]
        
        if total == 0:
            showwarning(self, '提示', f'当前筛选「{label}」下没有可预标注的图片')
            return
        
        if not askyesno(self, '确认',
                                   f'确定对当前筛选「{label}」的 {total} 张图片进行预标注吗？'):
            return
        
        self._status_bar.show_progress(0)
        self._status_bar.set_info('批量预标注中...')
        self._toolbar.set_batch_running(True)
        
        def on_progress(current, total_count):
            pct = (current / total_count) * 100 if total_count else 0
            self._status_bar.update_progress(pct)
            self._status_bar.set_info(f'批量预标注: {current}/{total_count}')
        
        def on_item_done(item, annotations):
            self._apply_batch_annotations_to_item(item, annotations)
            if self._project.current_image is item:
                self._refresh_current_image_view()
        
        def on_done(results, elapsed, processed):
            self._status_bar.hide_progress()
            self._toolbar.set_batch_running(False)
            total_anns = sum(len(v) for v in results.values())
            
            time_total = format_duration(elapsed)
            if processed > 0:
                avg = elapsed / processed
                time_avg = format_duration(avg)
                cancelled = self._pre_annotator.was_cancelled
                prefix = '批量预标注已停止' if cancelled else '批量预标注完成'
                self._status_bar.success(
                    f'{prefix}: 共 {total_anns} 个标注, '
                    f'处理 {processed}/{total} 张, '
                    f'总耗时 {time_total}, 平均 {time_avg}/张'
                )
            else:
                self._status_bar.warning('批量预标注已取消或未处理任何图片')
            
            self._refresh_current_image_view()
        
        self._pre_annotator.batch_predict(
            targets, self._threshold,
            self._label_manager, on_progress, on_done,
            main_thread_schedule=self._call_in_main,
            item_done_callback=on_item_done,
        )
    
    def _stop_batch_pre_annotate(self):
        """Cancel running batch pre-annotation."""
        if not self._pre_annotator.is_busy:
            self._status_bar.info('当前没有进行中的批量预标注')
            return
        self._pre_annotator.cancel()
        self._status_bar.info('正在停止批量预标注...')
    
    # --- Export ---
    
    def _export(self):
        """Export annotations."""
        if not self._project.has_images:
            showwarning(self, '提示', '没有可导出的数据')
            return
        
        dialog = ExportDialog(self)
        if not dialog.result:
            return
        
        fmt = dialog.result['format']
        output_dir = dialog.result['output_dir']
        copy_images = dialog.result['copy_images']
        
        self._status_bar.set_info('正在导出...')
        self.update_idletasks()
        
        success = False
        if fmt == 'YOLO':
            success = export_yolo(
                self._project.image_list, self._label_manager,
                output_dir, copy_images
            )
        elif fmt == 'COCO JSON':
            output_file = Path(output_dir) / 'annotations.json'
            success = export_coco(
                self._project.image_list, self._label_manager,
                str(output_file)
            )
        elif fmt == 'Pascal VOC':
            success = export_voc(
                self._project.image_list, self._label_manager,
                output_dir, copy_images
            )
        
        if success:
            self._status_bar.set_info(f'导出成功: {output_dir}')
            showinfo(self, '成功', f'导出完成!\n目录: {output_dir}')
        else:
            self._status_bar.set_info('导出失败')
            showerror(self, '错误', '导出失败，请查看控制台日志')
    
    def _load_existing_annotations(self):
        """Load existing annotations for current image."""
        item = self._project.current_image
        if not item:
            return
        
        img_w = item.width if item.width > 0 else None
        img_h = item.height if item.height > 0 else None
        annotations = load_annotation_file(
            item.path, self._label_manager,
            img_width=img_w, img_height=img_h,
        )
        if annotations:
            item.annotations = annotations
            item._annotations_loaded = True
            item.mark_dirty()
            self._mark_real_annotation_change(item)
            self._canvas.refresh()
            self._box_list_panel.set_image(item)
            self._on_annotation_changed()
            self._save_manual_statuses()
            self._status_bar.set_info(f'加载了 {len(annotations)} 个已有标注')
        else:
            self._status_bar.set_info('未找到已有标注文件')
    
    # --- Dialogs ---
    
    def _show_statistics(self):
        StatisticsDialog(self, self._project, self._label_manager)
    
    def _show_shortcuts(self):
        shortcuts = """
快捷键说明:
═══════════════════════════
Ctrl+O      打开目录
Ctrl+S      保存
Ctrl+Shift+S 导出
Ctrl+Z      撤销（先当前图编辑，再恢复上一张图）
Ctrl+Y      重做（仅当前图）
Ctrl+A      全选
Ctrl+I      反选
Delete      删除选中锚框
Ctrl+Del    删除本图
← / A       上一张
→ / D       下一张
Home / End  首张/末张
Ctrl+G      跳转到指定图片
1~9         快速选标签
Q / E       上一个/下一个锚框
F           适应窗口
T           切换标签显示(全部→精简→隐藏)
滚轮        缩放
中键/空格+拖拽  平移
Ctrl+X      预标注当前图
Ctrl+Shift+X 批量预标注
Ctrl+点击   多选/取消选择
右键拖拽    框选多个锚框
右键菜单    全选/反选/删除/修改标签
S           修改标签(选中锚框后，焦点到标签搜索)
Escape      取消绘制 / 取消选中
"""
        showinfo(self, '快捷键说明', shortcuts)
    
    def _show_about(self):
        showinfo(self, '关于', '目标检测标注工具 v1.0\n\n支持格式: YOLO, COCO, Pascal VOC\n预标注: YOLOv8 (ultralytics)')
    
    # --- Cleanup ---
    
    def _on_close(self):
        """Handle window close."""
        self._cancel_pane_layout_apply()
        try:
            failed_item = self._save_all_dirty_annotations()
            if failed_item is not None:
                self._report_save_failure(failed_item)
                return
            self._save_manual_statuses()
            self._persist_current_position()
            self._save_session()
        except Exception:
            pass

        for item in self._project.image_list:
            item._pil_image = None
            item.is_loaded = False

        self._image_loader.shutdown()
        self.destroy()

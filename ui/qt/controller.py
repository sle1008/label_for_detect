"""界面无关的应用控制流程，保持成熟的数据保存、撤销与推理语义。

从稳定版本提取业务控制方法；窗口、画布和对话框由 Qt 实现。
"""
import os
import math
import sys
import queue
import threading
import time
from pathlib import Path
from typing import List, Optional
from core.annotation import BBox
from core.image_item import ImageItem
from core.video_item import VideoItem
from core.label_manager import LabelManager
from core.label_notes import LabelNoteStore
from core.project import Project, ImageFilter
from core.undo_redo import UndoRedoManager, Command, CompoundCommand
from core.config import AppConfig, ConfigManager
from io_ops.image_loader import AsyncImageLoader
from io_ops.video_player import VideoPlayer, DEFAULT_VIDEO_FPS
from io_ops.video_export import (
    export_annotated_video, validate_video_export_path, VideoExportCancelled,
)
from io_ops.label_file_parser import load_annotation_file
from io_ops.label_set_detector import iter_label_set_candidates
from io_ops.label_cache import iter_label_cache_batches
from io_ops.yolo_exporter import export_yolo
from io_ops.coco_exporter import export_coco
from io_ops.voc_exporter import export_voc
from io_ops.pre_annotator import (
    PreAnnotator,
    VideoPrediction,
    DEFAULT_INFERENCE_MODE,
    INFERENCE_MODE_BACKEND_AUTO,
    INFERENCE_MODE_ONE_TO_ONE,
    INFERENCE_MODE_ONE_TO_MANY_NMS,
)
from io_ops.annotation_status import (
    get_image_category, is_image_annotated, invalidate_annotation_status,
    load_manual_statuses, save_manual_statuses, preferred_annotation_txt_path,
    resolve_annotation_txt_path,
    IMAGE_CATEGORY_ANNOTATED, IMAGE_CATEGORY_UNANNOTATED, IMAGE_CATEGORY_UNCERTAIN,
)
from io_ops.image_files import delete_image_and_labels, delete_annotation_files
from io_ops.image_export import (
    CONFLICT_SUFFIX, EXPORT_MODE_COPY, EXPORT_MODE_MOVE,
    export_images_and_labels, find_export_conflicts,
)
from io_ops.annotation_writer import write_yolo_annotations_atomic
from io_ops.folder_labels import (
    detect_class_folder_layout,
    infer_immediate_subfolder_name,
    save_labels_txt,
)
from utils.time_format import format_duration
from utils.paths import get_icon_path, format_image_display_path
from utils.constants import (
    DEFAULT_CONFIDENCE_THRESHOLD, UI_BG_COLOR, UI_PANEL_BG,
    UI_SURFACE_BG, UI_TEXT_COLOR, UI_BORDER, UI_TOOLBAR_BG, UI_ACCENT,
    LEFT_PANEL_WIDTH, RIGHT_PANEL_WIDTH, RIGHT_PANEL_MIN_WIDTH_2K, RIGHT_PANEL_MIN_WIDTH_4K,
    PRELOAD_FORWARD, PRELOAD_BACKWARD,
)
from ui.canvas_state import CanvasMode, LABEL_MODE_CYCLE, LABEL_MODE_NAMES, LABEL_MODE_FULL, LABEL_MODE_COMPACT, LABEL_MODE_HIDDEN
from ui.qt.bridge import Menu, Value, filedialog
from ui.qt.dialogs import (ExportDialog, StatisticsDialog, LabelLoadDialog, JumpToImageDialog, RestoreDirectoryDialog, ImageExportModeDialog, ImageExportConflictDialog, ImageExportProgressDialog, showinfo, showwarning, showerror, askyesno)

# ==================== 界面参数设置区 ====================
# 右键菜单保留的最近标签数量，使用会话内状态，不写入配置文件。
RECENT_LABEL_LIMIT = 4
LABEL_FILTER_ITEMS_PER_COLUMN = 40
VIDEO_FRAME_POLL_MS = 15
VIDEO_FILETYPES = [('视频文件', '*.mp4 *.avi *.mov *.mkv *.wmv *.m4v'), ('所有文件', '*.*')]
VIDEO_SUPPORTED_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.wmv', '.m4v'}
VIDEO_DELETE_JOIN_SECONDS = 5.0
# 视频逐帧键独立于图片导航，输入控件中保留标点输入的原有含义。
VIDEO_FRAME_SHORTCUTS = (('comma', -1), ('period', 1))
VIDEO_SHORTCUT_INPUT_CLASSES = {'Entry', 'TEntry', 'Text', 'TCombobox', 'Spinbox', 'TSpinbox'}
# 图片显示方向的快捷键，便于在画布聚焦时快速操作。
ROTATE_IMAGE_SHORTCUT = 'r'


def update_recent_class_ids(recent_class_ids: List[int], class_id: int,
                            limit: int = RECENT_LABEL_LIMIT) -> List[int]:
    """按新标签入队规则更新最近标签，重复使用已有标签不改变顺序。"""
    recent = list(recent_class_ids or [])[:max(0, limit)]
    if class_id in recent or limit <= 0:
        return recent
    return [class_id] + recent[:limit - 1]



class ApplicationController:
    """业务操作只依赖面板接口，后台任务通过主线程调度更新界面。"""

    def _setup_menus(self):
        """Setup menu bar."""
        menubar = Menu(self)

        # File menu
        file_menu = Menu(menubar, tearoff=0)
        file_menu.add_command(label='打开目录...', command=self._open_directory, accelerator='Ctrl+O')
        file_menu.add_command(label='打开视频...', command=self._open_video, accelerator='Ctrl+Shift+O')
        file_menu.add_command(label='刷新列表', command=self._refresh_list, accelerator='F5')
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
        edit_menu = Menu(menubar, tearoff=0)
        edit_menu.add_command(label='撤销', command=self._undo, accelerator='Ctrl+Z')
        edit_menu.add_command(label='重做', command=self._redo, accelerator='Ctrl+Y')
        edit_menu.add_separator()
        edit_menu.add_command(label='全选', command=self._select_all, accelerator='Ctrl+A')
        edit_menu.add_command(label='反选', command=self._inverse_select, accelerator='Ctrl+I')
        edit_menu.add_command(label='删除选中', command=self._delete_selected, accelerator='Delete')
        menubar.add_cascade(label='编辑', menu=edit_menu)

        # Navigation menu
        nav_menu = Menu(menubar, tearoff=0)
        nav_menu.add_command(label='上一张', command=self._prev_image, accelerator='←/A')
        nav_menu.add_command(label='下一张', command=self._next_image, accelerator='→/D')
        nav_menu.add_command(label='第一张', command=self._goto_first, accelerator='Home')
        nav_menu.add_command(label='最后一张', command=self._goto_last, accelerator='End')
        nav_menu.add_separator()
        nav_menu.add_command(label='跳转...', command=self._jump_to_image, accelerator='Ctrl+G')
        menubar.add_cascade(label='导航', menu=nav_menu)

        # View menu
        view_menu = Menu(menubar, tearoff=0)
        view_menu.add_command(label='适应窗口', command=self._fit_window, accelerator='F')
        view_menu.add_separator()
        self._label_mode_var = Value(value=LABEL_MODE_FULL)
        label_mode_menu = Menu(view_menu, tearoff=0)
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
        view_menu.add_separator()
        self._theme_var = Value(value=self._config.ui_theme)
        for text, theme in [('深色主题', 'dark'), ('浅色主题', 'light')]:
            view_menu.add_radiobutton(label=text, variable=self._theme_var, value=theme,
                                      command=lambda value=theme: self._set_theme(value))
        view_menu.add_separator()
        view_menu.add_command(label='切换到旧版界面外观（保存并重开）', command=self._request_ui_switch)
        menubar.add_cascade(label='视图', menu=view_menu)

        # Annotation menu
        ann_menu = Menu(menubar, tearoff=0)
        ann_menu.add_command(label='预标注当前图', command=self._pre_annotate_current, accelerator='Ctrl+X')
        ann_menu.add_command(label='批量预标注', command=self._batch_pre_annotate, accelerator='Ctrl+Shift+X')
        ann_menu.add_separator()
        self._inference_mode_var = Value(value=DEFAULT_INFERENCE_MODE)
        ann_menu.add_radiobutton(
            label=INFERENCE_MODE_ONE_TO_ONE,
            variable=self._inference_mode_var,
            value=INFERENCE_MODE_ONE_TO_ONE,
            command=lambda: self._set_pre_annotation_inference_mode(
                INFERENCE_MODE_ONE_TO_ONE
            ),
        )
        ann_menu.add_radiobutton(
            label=INFERENCE_MODE_ONE_TO_MANY_NMS,
            variable=self._inference_mode_var,
            value=INFERENCE_MODE_ONE_TO_MANY_NMS,
            command=lambda: self._set_pre_annotation_inference_mode(
                INFERENCE_MODE_ONE_TO_MANY_NMS
            ),
        )
        ann_menu.add_separator()
        ann_menu.add_command(label='加载已有标注', command=self._load_existing_annotations)
        menubar.add_cascade(label='标注', menu=ann_menu)

        # Image filter menu
        img_menu = Menu(menubar, tearoff=0)
        self._image_filter_var = Value(value='all')
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

        self._label_filter_menu = Menu(menubar, tearoff=0)
        self._label_filter_var = Value(value='全部标签')
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
        menu = Menu(self, tearoff=0)
        menu.add_command(label='全选', command=self._select_all)
        menu.add_command(label='反选', command=self._inverse_select)
        menu.add_command(label='删除', command=self._delete_selected)
        menu.add_separator()
        menu.add_command(label='修改标签', command=self._focus_label_search)
        self._canvas_context_menu = menu
        self._recent_label_menu_start = menu.index('end') + 1
        self._canvas.set_context_menu(menu)

    def _refresh_recent_label_menu(self):
        """Refresh recent-label commands while preserving the base menu items."""
        menu = getattr(self, '_canvas_context_menu', None)
        if menu is None:
            return

        start = getattr(self, '_recent_label_menu_start', None)
        if start is None:
            return
        end = menu.index('end')
        if end is not None and end >= start:
            menu.delete(start, 'end')

        recent_ids = [
            class_id for class_id in self._recent_class_ids
            if self._label_manager.has_class(class_id)
        ]
        if not recent_ids:
            return

        menu.add_separator()
        menu.add_command(label='最近使用标签', state='disabled')
        for class_id in recent_ids:
            label = self._label_manager.get_name(class_id)
            menu.add_command(
                label=f'[{class_id}] {label}',
                command=lambda cid=class_id: self._apply_recent_class(cid),
            )

    def _remember_recent_class(self, class_id: int):
        """Record a newly used label and update the canvas context menu."""
        if not self._label_manager.has_class(class_id):
            return
        updated = update_recent_class_ids(
            self._recent_class_ids, class_id, RECENT_LABEL_LIMIT,
        )
        if updated == self._recent_class_ids:
            return
        self._recent_class_ids = updated
        self._refresh_recent_label_menu()

    def _apply_recent_class(self, class_id: int):
        """Apply a recent label through the normal label-change workflow."""
        if not self._label_manager.has_class(class_id):
            self._refresh_recent_label_menu()
            return
        self._label_manager.current_class_id = class_id
        self._label_panel.highlight_class(class_id)
        self._on_class_selected(class_id)

    def _call_in_main(self, fn):
        """Schedule callback on the GUI main thread (safe from worker threads)."""
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

    def _on_image_context_menu(self, full_index: int, event, selected_indices=None):
        self._show_image_context_menu(
            event, full_index=full_index, selected_indices=selected_indices,
        )

    def _show_image_context_menu(self, event, full_index: int = None, selected_indices=None):
        if self._video_path:
            self._status_bar.info('视频播放期间不可修改图片列表')
            return
        if full_index is None:
            full_index = self._project.current_index
        if not (0 <= full_index < len(self._project.image_list)):
            return

        item = self._project.image_list[full_index]
        selected_indices = sorted(set(selected_indices or [full_index]))
        selected_indices = [
            index for index in selected_indices
            if 0 <= index < len(self._project.image_list)
        ]
        menu = Menu(self, tearoff=0)
        if len(selected_indices) > 1:
            count = len(selected_indices)
            menu.add_command(
                label=f'将所选 {count} 张设为已标注',
                command=lambda indices=selected_indices: self._set_images_category(
                    indices, IMAGE_CATEGORY_ANNOTATED,
                ),
            )
            menu.add_command(
                label=f'将所选 {count} 张设为未标注',
                command=lambda indices=selected_indices: self._set_images_category(
                    indices, IMAGE_CATEGORY_UNANNOTATED,
                ),
            )
            menu.add_command(
                label=f'将所选 {count} 张设为不确定',
                command=lambda indices=selected_indices: self._set_images_category(
                    indices, IMAGE_CATEGORY_UNCERTAIN,
                ),
            )
            menu.add_separator()
            menu.add_command(
                label=f'导出所选 {count} 张图片...',
                command=lambda indices=selected_indices: self._choose_image_export(indices),
            )
            menu.add_separator()
            menu.add_command(
                label=f'删除所选 {count} 张',
                command=lambda indices=selected_indices: self._delete_images(indices),
            )
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return

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
            label='导出本图...',
            command=lambda idx=full_index: self._choose_image_export([idx]),
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

    def _choose_image_export(self, indices):
        """Choose export mode and destination for listbox-selected images."""
        if self._image_export_running:
            showwarning(self, '正在导出', '已有图片导出任务正在进行，请稍候。')
            return
        indices = sorted(set(indices))
        items = [
            self._project.image_list[index]
            for index in indices
            if 0 <= index < len(self._project.image_list)
        ]
        if not items:
            return

        mode_dialog = ImageExportModeDialog(
            self,
            len(items),
            default_mode=self._config.last_image_export_mode,
        )
        mode = mode_dialog.result
        if not mode:
            return
        self._config.last_image_export_mode = mode
        self._config_manager.save(self._config)
        initial = self._initial_image_export_directory()
        output_dir = filedialog.askdirectory(
            parent=self,
            initialdir=initial,
            title='选择导出位置（将自动创建 images 和 labels）',
        )
        if not output_dir:
            return
        self._config.last_image_export_directory = output_dir
        self._config_manager.save(self._config)
        conflict_policy = CONFLICT_SUFFIX
        if mode == EXPORT_MODE_COPY:
            conflicts = find_export_conflicts(items, Path(output_dir))
            if conflicts:
                conflict_dialog = ImageExportConflictDialog(self, len(conflicts))
                conflict_policy = conflict_dialog.result
                if not conflict_policy:
                    return
        if not self._save_before_navigate():
            return

        visible_items = [
            self._project.image_list[index]
            for index in self._project.get_visible_indices()
            if 0 <= index < len(self._project.image_list)
        ]
        current_item = self._project.current_image
        progress_dialog = ImageExportProgressDialog(self, len(items), mode)
        self._image_export_running = True

        import threading

        def report_progress(completed, total, item):
            self._main_thread_queue.put(
                lambda done=completed, count=total, name=item.name:
                progress_dialog.update_progress(done, count, name)
            )

        def worker():
            try:
                results = export_images_and_labels(
                    items,
                    Path(output_dir),
                    mode=mode,
                    progress=report_progress,
                    conflict_policy=conflict_policy,
                )
                error = ''
            except Exception as exc:
                results = []
                error = str(exc)
            self._main_thread_queue.put(
                lambda: self._finish_image_export(
                    results, mode, Path(output_dir), visible_items,
                    current_item, progress_dialog, error,
                )
            )

        threading.Thread(target=worker, daemon=True).start()
        progress_dialog.wait()

    def _initial_image_export_directory(self) -> str:
        """Return the last valid image-export destination, with safe fallbacks."""
        candidates = (
            self._config.last_image_export_directory,
            self._config.last_directory,
        )
        for candidate in candidates:
            if candidate and Path(candidate).is_dir():
                return candidate
        return os.path.expanduser('~')

    def _finish_image_export(
        self, results, mode, output_dir, visible_items, current_item,
        progress_dialog, worker_error='',
    ):
        """Apply completed export results on the GUI main thread."""
        self._image_export_running = False
        progress_dialog.close()
        if worker_error:
            showerror(self, '导出失败', f'无法完成图片导出：\n\n{worker_error}')
            return

        succeeded = [result for result in results if result.success]
        skipped = [result for result in results if result.skipped]
        failed = [
            result for result in results
            if not result.success and not result.skipped
        ]
        warnings = [result for result in succeeded if result.warning]
        if mode == EXPORT_MODE_MOVE:
            moved_items = [result.item for result in succeeded if result.source_removed]
            self._remove_moved_export_items(moved_items, visible_items, current_item)
            self._save_manual_statuses()

        action = '移动' if mode == EXPORT_MODE_MOVE else '复制'
        destination_image_count = len(Project.scan_image_paths(str(output_dir)))
        summary = (
            f'已{action} {len(succeeded)} 张图片到：\n{output_dir}'
            f'\n\n此目录内现有 {destination_image_count} 张图片。'
        )
        details = []
        if failed:
            details.append(f'{len(failed)} 张失败')
            details.extend(
                f'{result.item.name}: {result.error}' for result in failed[:5]
            )
        if skipped:
            details.append(f'{len(skipped)} 张因同名被跳过')
            details.extend(
                result.item.name for result in skipped[:5]
            )
        if warnings:
            details.append(f'{len(warnings)} 张存在源标签清理警告')
            details.extend(
                f'{result.item.name}: {result.warning}' for result in warnings[:3]
            )
        if details:
            showwarning(self, '导出完成（部分异常）', summary + '\n\n' + '\n'.join(details))
            self._status_bar.warning(
                f'图片导出完成：成功 {len(succeeded)}，跳过 {len(skipped)}，'
                f'异常 {len(failed) + len(warnings)}'
            )
        else:
            showinfo(self, '导出完成', summary)
            self._status_bar.success(f'已{action} {len(succeeded)} 张图片')

    def _remove_moved_export_items(self, moved_items, visible_items, current_item):
        """Remove moved sources from project state and choose a visible neighbor."""
        moved_ids = {id(item) for item in moved_items}
        if not moved_ids:
            return
        current_was_moved = current_item is not None and id(current_item) in moved_ids
        replacement = current_item if not current_was_moved else None
        if current_was_moved and current_item in visible_items:
            position = visible_items.index(current_item)
            project_item_ids = {id(item) for item in self._project.image_list}
            remaining_visible_ids = {
                id(item) for item in visible_items
                if id(item) not in moved_ids and id(item) in project_item_ids
            }
            for item in visible_items[position + 1:]:
                if id(item) in remaining_visible_ids:
                    replacement = item
                    break
            if replacement is None:
                for item in reversed(visible_items[:position]):
                    if id(item) in remaining_visible_ids:
                        replacement = item
                        break

        for index in range(len(self._project.image_list) - 1, -1, -1):
            item = self._project.image_list[index]
            if id(item) not in moved_ids:
                continue
            self._image_loader.evict_path(item.path)
            item._pil_image = None
            item.is_loaded = False
            self._filter_status_snapshot.pop(id(item), None)
            self._image_undo_managers.pop(id(item), None)
            self._project.remove_image_at(index)
        self._navigation_undo_stack = [
            item for item in self._navigation_undo_stack if id(item) not in moved_ids
        ]

        replacement_index = next(
            (
                index for index, item in enumerate(self._project.image_list)
                if item is replacement
            ),
            -1,
        )
        if replacement_index >= 0:
            self._project.current_index = replacement_index
        elif current_was_moved:
            self._project.current_index = -1
        self._refresh_image_list_view(jump='keep', navigate=False)

        if not self._project.image_list or self._project.current_image is None:
            self._canvas.clear_all()
            self._box_list_panel.set_image(None)
            self._update_window_title()
            return
        if current_was_moved:
            self._thumb_panel.select_only_current()
            self._show_current_image_async(clear_canvas=False)
        else:
            self._thumb_panel.set_current_by_full_index(self._project.current_index)
            self._thumb_panel.select_only_current()

    @staticmethod
    def _batch_removal_replacement(visible_items, current_item, removed_ids):
        """Choose the next visible item after a batch removes the current one."""
        if current_item is None or id(current_item) not in removed_ids:
            return current_item
        try:
            position = visible_items.index(current_item)
        except ValueError:
            return None
        for item in visible_items[position + 1:]:
            if id(item) not in removed_ids:
                return item
        for item in reversed(visible_items[:position]):
            if id(item) not in removed_ids:
                return item
        return None

    def _delete_images(self, indices):
        indices = sorted(set(indices), reverse=True)
        if not indices:
            return
        if not askyesno(
            self, '删除所选图片',
            f'确定从本地删除所选 {len(indices)} 张图片及其标注文件吗？\n\n此操作不可撤销。',
        ):
            return
        if not self._save_before_navigate():
            return

        visible_items = [
            self._project.image_list[index]
            for index in self._project.get_visible_indices()
            if 0 <= index < len(self._project.image_list)
        ]
        current_item = self._project.current_image
        failed = []
        removed_ids = set()
        for index in indices:
            if not (0 <= index < len(self._project.image_list)):
                continue
            item = self._project.image_list[index]
            ok, err = delete_image_and_labels(item)
            if not ok:
                failed.append(self._format_image_path(item))
                continue
            self._image_loader.evict_path(item.path)
            self._filter_status_snapshot.pop(id(item), None)
            removed_ids.add(id(item))
            self._project.remove_image_at(index)

        replacement = self._batch_removal_replacement(
            visible_items, current_item, removed_ids,
        )
        replacement_index = (
            next((
                index for index, item in enumerate(self._project.image_list)
                if item is replacement
            ), -1)
            if replacement is not None else -1
        )
        self._project.current_index = replacement_index

        if self._project.image_list:
            self._refresh_image_list_view(jump='keep', navigate=False)
            if replacement_index >= 0:
                self._thumb_panel.select_only_current()
                self._show_current_image_async(clear_canvas=False)
            else:
                self._canvas.clear_all()
                self._box_list_panel.set_image(None)
                self._update_window_title()
        else:
            self._canvas.clear_all()
            self._box_list_panel.set_image(None)
            self._thumb_panel.clear()
            self._update_window_title()
        if failed:
            self._status_bar.warning(f'已删除部分图片，{len(failed)} 张删除失败')
        else:
            self._status_bar.success(f'已删除 {len(indices)} 张图片')

    def _delete_current_image_shortcut(self, event=None):
        """Delete the currently displayed image (Ctrl+Del)."""
        if self._project.current_index >= 0:
            self._delete_image(self._project.current_index)
        return 'break'

    def _delete_image(self, full_index: int):
        if not (0 <= full_index < len(self._project.image_list)):
            return

        item = self._project.image_list[full_index]
        was_current = full_index == self._project.current_index
        adjacent_item = (
            self._project.adjacent_visible_item_for_removal(full_index)
            if was_current else None
        )
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

        self._project.remove_image_at(full_index)
        pending_indices = [
            index - 1 if index > full_index else index
            for index in self._thumb_panel.full_indices()
            if index != full_index
        ]
        self._project.set_visible_indices(pending_indices)
        if was_current:
            self._thumb_panel.set_interaction_enabled(False)

        if was_current:
            if adjacent_item is None:
                self._project.current_index = -1
            else:
                adjacent_index = next(
                    (
                        index for index, candidate in enumerate(self._project.image_list)
                        if candidate is adjacent_item
                    ),
                    -1,
                )
                self._project.current_index = adjacent_index
        if not self._project.image_list:
            # There is no replacement image.  Rebuild the visible list from
            # the post-delete project state so a one-item filtered list cannot
            # retain its stale row mapping.
            self._refresh_image_list_view(jump='keep', navigate=False)
            self._thumb_panel.set_interaction_enabled(True)
            self._canvas.clear_all()
            self._box_list_panel.set_image(None)
            self._update_window_title()
            self._status_bar.warning('目录中已无图片')
            return

        if was_current:
            if self._project.current_image is None:
                # The deleted image was the only visible match.  The full
                # project may still contain non-matching images, so refresh
                # the active list rather than removing by the old index.
                self._refresh_image_list_view(jump='keep', navigate=False)
                self._thumb_panel.set_interaction_enabled(True)
                self._canvas.clear_all()
                self._box_list_panel.set_image(None)
                self._update_window_title()
            else:
                self._show_current_image_async(clear_canvas=False)
                self.after(
                    50, self._finish_single_image_list_removal, item,
                )
        else:
            self._finish_single_image_list_removal(item)
            self._thumb_panel.set_current_by_full_index(self._project.current_index)

        if err:
            self._status_bar.warning(f'已删除图片，但部分标注文件未删除: {err}')
        else:
            self._status_bar.success(f'已删除: {display_name}')

    def _finish_single_image_list_removal(self, removed_item: ImageItem):
        """Synchronize the left list after a single-image deletion."""
        try:
            if self._thumb_panel.remove_item(removed_item):
                self._project.set_visible_indices(self._thumb_panel.full_indices())
            else:
                self._refresh_image_list_view(jump='keep', navigate=False)
            if self._project.current_image is not None:
                self._thumb_panel.set_current_by_full_index(self._project.current_index)
        finally:
            self._thumb_panel.set_interaction_enabled(True)

    def _set_images_category(self, indices, category: str):
        indices = sorted(set(indices))
        labels = {
            IMAGE_CATEGORY_ANNOTATED: '已标注',
            IMAGE_CATEGORY_UNANNOTATED: '未标注',
            IMAGE_CATEGORY_UNCERTAIN: '不确定',
        }
        changed = 0
        for index in indices:
            if not (0 <= index < len(self._project.image_list)):
                continue
            item = self._project.image_list[index]
            if category == IMAGE_CATEGORY_UNANNOTATED:
                item.clear_annotations()
                delete_annotation_files(item.path)
                item.manual_annotation_status = None
                item._annotations_loaded = True
                item.mark_clean()
            elif category == IMAGE_CATEGORY_ANNOTATED:
                item.manual_annotation_status = None
                item._annotations_loaded = True
                item.mark_dirty()
                if not self._save_item_annotations(item):
                    self._report_save_failure(item)
                    continue
            elif category == IMAGE_CATEGORY_UNCERTAIN:
                item.manual_annotation_status = IMAGE_CATEGORY_UNCERTAIN
            else:
                return
            invalidate_annotation_status(item)
            self._filter_status_snapshot[id(item)] = get_image_category(item)
            changed += 1

        self._save_manual_statuses()
        self._project.invalidate_filter_cache()
        self._refresh_image_list_view(jump='keep', navigate=False)
        current = self._project.current_image
        if current:
            self._canvas.refresh()
            self._box_list_panel.refresh()
            self._status_bar.set_info(f'已将 {changed} 张图片设为{labels[category]}')

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

    def _restore_session(self):
        """Restore last session settings."""
        self._label_manager.sort_by_name = self._config.label_sort_by_name
        self._label_panel.set_sort_by_name(self._config.label_sort_by_name)

        # 类别定义由当前目录识别或手动导入，启动时不恢复历史类别以免误用。

        # Ask before restoring the last directory. A command-line directory or
        # another early opener takes precedence and suppresses this prompt.
        last_directory = self._config.last_directory
        if (
            not self._project.image_dir
            and last_directory
            and Path(last_directory).is_dir()
        ):
            dialog = RestoreDirectoryDialog(self, last_directory)
            if dialog.result == 'browse':
                self._reset_image_filters()
                self._open_directory()
            else:
                self._reset_image_filters()
                self._load_directory(last_directory)

        # Restore weights only after the directory/list/first preview is ready.
        if self._config.last_weights_file and Path(self._config.last_weights_file).is_file():
            if (
                not self._pending_weights_path
                and not self._weights_load_in_progress
                and not self._pre_annotator.is_loaded
            ):
                self._defer_weights_load_until_images_ready(self._config.last_weights_file)

        self._threshold_input.setValue(self._config.confidence_threshold)
        self._threshold = self._threshold_input.value()
        if self._config.label_mode in LABEL_MODE_CYCLE:
            self._canvas.set_label_mode(self._config.label_mode)
            self._label_mode_var.set(self._config.label_mode)

    def _defer_weights_load_until_images_ready(self, path: str):
        """Queue an initial model load until directory loading has completed."""
        if not path:
            return
        self._pending_weights_path = path
        if not self._directory_loading:
            self.after_idle(self._start_pending_weights_load)

    def _start_pending_weights_load(self):
        """Start a queued initial model load without blocking the GUI thread."""
        if self._directory_loading or self._weights_load_in_progress:
            return
        path = self._pending_weights_path
        if not path:
            return
        self._pending_weights_path = ''
        self._load_weights_async(path, show_error=False)

    def _load_weights_async(self, path: str, show_error: bool = True) -> bool:
        """Load pre-annotation weights on a daemon thread."""
        if self._weights_load_in_progress:
            self._status_bar.warning('预标注权重正在加载，请稍候...')
            return False
        if self._pre_annotator.is_busy:
            self._status_bar.warning('预标注进行中，暂时无法更换权重')
            return False

        import threading

        self._weights_load_in_progress = True
        model_name = Path(path).name
        self._status_bar.set_info(f'正在后台加载模型: {model_name}')

        def worker():
            try:
                success = self._pre_annotator.load_weights(path)
            except Exception as exc:
                print(f'Failed to load model ({path}): {exc}')
                success = False
            self._call_in_main(
                lambda: self._finish_weights_load(path, success, show_error)
            )

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _finish_weights_load(self, path: str, success: bool, show_error: bool):
        """Finish an asynchronous model load on the GUI main thread."""
        self._weights_load_in_progress = False
        if success:
            self._config.last_weights_file = path
            self._status_bar.set_info(f'模型加载成功: {Path(path).name}')
            return

        if show_error:
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

    def _apply_saved_image_filters(self):
        """Restore persisted status and label filters for session recovery."""
        value = getattr(self._config, 'image_filter', 'all')
        try:
            self._project.image_filter = ImageFilter(value)
        except ValueError:
            self._project.image_filter = ImageFilter.ALL
        class_id = getattr(self._config, 'label_filter_class_id', None)
        self._project.label_filter_class_id = class_id if isinstance(class_id, int) else None
        self._project.invalidate_filter_cache()
        self._image_filter_var.set(self._project.image_filter.value)
        self._sync_status_filter_combo()
        self._refresh_label_filter_options()

    def _reset_image_filters(self):
        """Reset directory-specific filters when opening another directory."""
        self._project.image_filter = ImageFilter.ALL
        self._project.label_filter_class_id = None
        self._project.invalidate_filter_cache()
        self._image_filter_var.set(ImageFilter.ALL.value)
        self._sync_status_filter_combo()
        self._refresh_label_filter_options()

    def _save_session(self):
        """Save current session settings."""
        self._config.window_geometry = self.saved_window_geometry()
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
        self._config.label_filter_class_id = self._project.label_filter_class_id

        self._config_manager.save(self._config)

    def _on_box_list_columns_changed(self, widths: dict):
        """Persist annotation list column widths."""
        self._config.box_list_column_widths = dict(widths)
        self._config_manager.save(self._config)

    def _apply_manual_statuses(self, dir_path: str, project=None):
        """Apply persisted 'uncertain' marks to the freshly-created image list.

        Only 'uncertain' is stored here; annotated/unannotated are derived from
        the presence of the label (.txt) file.
        """
        image_dir = Path(dir_path)
        statuses = load_manual_statuses(image_dir)
        if not statuses:
            return
        for item in (self._project if project is None else project).image_list:
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

    def _can_edit_label_note(self) -> bool:
        item = self._project.current_image
        return item is None or not item.get_selected_annotations()

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

    def _load_item_annotations(self, item: ImageItem, label_manager=None):
        """Load YOLO annotations for an image into memory."""
        if (
            item._annotations_loaded
            or item.is_dirty
            or not item.is_loaded
            or item.width <= 0
        ):
            return
        try:
            txt_path = resolve_annotation_txt_path(item.path)
            item._annotation_path_checked = True
            item._annotation_file_path = txt_path
            file_anns = load_annotation_file(
                item.path, self._label_manager if label_manager is None else label_manager,
                img_width=item.width, img_height=item.height,
                txt_path=txt_path, annotation_path_resolved=True,
            )
            # Disk IO can overlap a main-thread edit. Once the in-memory item
            # becomes dirty, the just-read disk snapshot is stale and must not
            # replace newly drawn or predicted annotations.
            if item._annotations_loaded or item.is_dirty:
                return
            item.annotations = file_anns
            if item.manual_annotation_status != IMAGE_CATEGORY_UNCERTAIN:
                item.manual_annotation_status = None
            item._annotations_loaded = True
        except Exception:
            item._annotations_loaded = True

    def _preload_window_async(self, center_index: int = None):
        """Preload nearby visible image pixels without mutating annotation state."""
        if not self._project.image_list:
            return
        if center_index is None:
            center_index = self._project.current_index

        image_list, center_index = self._visible_preload_window(center_index)
        if center_index is None:
            return
        self._image_loader.preload_neighbors(
            center_index, image_list,
            forward=PRELOAD_FORWARD, backward=PRELOAD_BACKWARD,
        )
        self._start_label_cache_preload()

    def _visible_preload_window(self, full_index: int):
        """Return the displayed image sequence and current position within it."""
        visible_indices = self._project.get_visible_indices()
        try:
            center_index = visible_indices.index(full_index)
        except ValueError:
            return [], None
        return (
            [self._project.image_list[index] for index in visible_indices],
            center_index,
        )

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
        project = self._project
        items = list(self._project.image_list)

        import threading
        def bg(gen=self._label_cache_job_generation):
            total = len(items)
            processed = 0
            cancelled = lambda: self._scan_generation != gen or self._closing
            for records in iter_label_cache_batches(items, is_cancelled=cancelled):
                processed += len(records)
                self._call_in_main(
                    lambda batch=records, p=int(processed * 100 / total):
                        self._apply_label_cache_batch(project, gen, batch, p)
                )
            if cancelled():
                self._call_in_main(lambda: self._label_cache_jobs.discard(gen))
                return
            self._call_in_main(lambda gen=gen: self._finish_label_cache_preload(gen))
        threading.Thread(target=bg, daemon=True).start()

    def _apply_label_cache_batch(self, project, generation, records, progress):
        if generation != self._scan_generation or project is not self._project or self._closing:
            return
        project.merge_label_cache_batch(records)
        self._update_label_cache_progress(generation, progress)

    def _update_label_cache_progress(self, generation, progress):
        if generation == self._scan_generation and not self._closing:
            self._label_cache_progress = progress
            self._status_bar.set_overlay(f'标签缓存中... {progress}%')

    def _finish_label_cache_preload(self, gen: int):
        self._label_cache_jobs.discard(gen)
        if gen != self._scan_generation:
            return
        self._label_cache_ready_generation = gen
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

    def _open_directory(self):
        """Open image directory with filters reset for the new selection."""
        if self._video_path or self._pending_video_generation is not None:
            self._stop_video(clear_canvas=False)
        initial = self._config.last_directory or os.path.expanduser('~')
        d = filedialog.askdirectory(initialdir=initial, title='选择图片目录', parent=self)
        if d:
            self._reset_image_filters()
            self._load_directory(d)

    def _refresh_list(self):
        """Rescan disk and rebuild the list using the active filters."""
        if self._video_path or self._pending_video_generation is not None:
            self._stop_video(clear_canvas=False)
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

    def _refresh_directory(self):
        return self._refresh_list()

    def _load_directory(self, dir_path: str, from_refresh: bool = False):
        """Load images from directory without blocking the UI."""
        if self._video_path or self._pending_video_generation is not None:
            self._stop_video(clear_canvas=False)
        import threading

        self._status_bar.info(
            f'正在刷新列表: {dir_path}' if from_refresh else f'正在扫描目录: {dir_path}'
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
        self._directory_loading = True

        self._project.image_dir = Path(dir_path)
        refresh_image_filter = self._project.image_filter
        refresh_label_filter_class_id = self._project.label_filter_class_id
        self._project.image_list.clear()
        self._project.current_index = -1
        self._canvas.clear_all()
        self._box_list_panel.set_image(None)
        self._thumb_panel.clear()
        self._thumb_panel.set_interaction_enabled(False)
        if not from_refresh:
            self._pending_refresh = None
            self._clear_directory_labels()

        pending_refresh = self._pending_refresh if from_refresh else None
        label_definitions = self._label_manager.to_dict_list()

        def _bg_scan():
            try:
                cancelled = lambda: scan_gen != self._scan_generation or self._closing
                paths = Project.scan_image_paths(
                    dir_path,
                    on_preview=lambda preview: self._call_in_main(
                        lambda: self._on_scan_preview(preview, scan_gen)),
                    is_cancelled=cancelled,
                )
                if cancelled():
                    return
                detected_labels = (next(iter_label_set_candidates(Path(dir_path)), None)
                                   if not from_refresh else None)
                prepared = Project(
                    image_filter=refresh_image_filter,
                    label_filter_class_id=refresh_label_filter_class_id,
                )
                # 文件索引及状态读取在后台完成，主线程只接收准备好的模型。
                prepared.set_image_paths(dir_path, paths)
                self._apply_manual_statuses(dir_path, project=prepared)
                filtered_indices = prepared.get_filtered_indices()
                refreshed_indices = filtered_indices if pending_refresh else None
                if cancelled():
                    return

                preload_path = self._resolve_preload_path(dir_path, paths)
                if pending_refresh:
                    current_path = pending_refresh['current_path']
                    preload_path = next(
                        (paths[index] for index in refreshed_indices
                         if current_path is not None and str(paths[index]) == str(current_path)),
                        paths[refreshed_indices[0]] if refreshed_indices else None,
                    )
                preloaded = ImageItem(path=preload_path) if preload_path else None
                if preloaded and self._image_loader.load_image_sync(preloaded):
                    # 首图标注也在后台读取，类别快照不受其他目录或手动导入影响。
                    labels = LabelManager()
                    labels.from_dict_list(detected_labels[1] if detected_labels else label_definitions)
                    self._load_item_annotations(preloaded, label_manager=labels)
                if cancelled():
                    return
                self._call_in_main(
                    lambda: self._on_scan_complete(
                        dir_path, paths, scan_gen, preloaded, refreshed_indices,
                        prepared_project=prepared, detected_labels=detected_labels,
                        from_refresh=from_refresh,
                    )
                )
            except Exception as e:
                self._call_in_main(lambda err=e: self._on_scan_failed(err, scan_gen))

        threading.Thread(target=_bg_scan, daemon=True).start()

    def _on_scan_preview(self, paths: list, scan_gen: int):
        """展示首批扫描结果；最终序号确定前只预览，不向业务控制器发送选择。"""
        if scan_gen != self._scan_generation or not self._directory_loading or self._closing:
            return
        self._thumb_panel.set_images([ImageItem(path=path) for path in paths])
        self._thumb_panel.set_count_text(f'已发现 {len(paths)} 张图片 · 后台扫描中…')
        self._status_bar.info(f'已显示首批 {len(paths)} 张图片，正在后台加载完整目录…')

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
                item._annotation_path_checked = preloaded._annotation_path_checked
                item._annotation_file_path = preloaded._annotation_file_path
                return

    def _on_scan_failed(self, error: Exception, scan_gen: int):
        if scan_gen != self._scan_generation:
            return
        self._directory_loading = False
        self._thumb_panel.clear()
        self._thumb_panel.set_interaction_enabled(True)
        self._status_bar.error(f'加载目录失败: {error}')
        showerror(self, '错误', f'加载目录失败:\n{error}')
        self.after_idle(self._start_pending_weights_load)

    def _on_scan_complete(self, dir_path: str, paths: list, scan_gen: int,
                          preloaded: ImageItem = None,
                          refreshed_indices: list = None, prepared_project=None,
                          detected_labels=None, from_refresh=False):
        if scan_gen != self._scan_generation:
            return
        self._directory_loading = False

        if prepared_project is None:
            count = self._project.set_image_paths(dir_path, paths)
            self._apply_manual_statuses(dir_path)
        else:
            if (prepared_project.image_filter != self._project.image_filter
                    or prepared_project.label_filter_class_id != self._project.label_filter_class_id):
                prepared_project.invalidate_filter_cache()
            prepared_project.image_filter = self._project.image_filter
            prepared_project.label_filter_class_id = self._project.label_filter_class_id
            self._project = prepared_project
            count = prepared_project.total_images
        # 扫描期间用户手动导入的类别优先，后台识别结果不覆盖新的选择。
        if not from_refresh and not self._label_manager.count() and not self._config.last_label_file:
            if detected_labels:
                self._apply_detected_label_set(*detected_labels)
            else:
                self._status_bar.warning('未找到本目录的标签集，请手动加载标签')
        self._thumb_panel.set_interaction_enabled(True)
        self._filter_status_snapshot.clear()
        self._image_undo_managers.clear()
        self._navigation_undo_stack.clear()

        pending_refresh = self._pending_refresh
        self._pending_refresh = None

        if count > 0:
            if preloaded and preloaded.is_loaded:
                for annotation in preloaded.annotations:
                    annotation.class_name = self._label_manager.get_name(annotation.class_id)
                self._merge_preloaded_item(preloaded)

            self._config.last_directory = dir_path
            if not pending_refresh:
                self._config.add_recent_dir(dir_path)

            if pending_refresh:
                indices = list(refreshed_indices or [])
                current_path = pending_refresh['current_path']
                restore_idx = next(
                    (
                        index for index in indices
                        if current_path is not None
                        and (
                            paths[index] == current_path
                            or str(paths[index]) == str(current_path)
                        )
                    ),
                    indices[0] if indices else -1,
                )
                items = [self._project.image_list[index] for index in indices]
                self._thumb_panel.set_images(
                    items,
                    full_indices=indices,
                    filter_hint=self._filter_hint_text(),
                )
                self._project.set_visible_indices(indices)
                if restore_idx >= 0:
                    self._project.goto_image(restore_idx)
                    self._thumb_panel.set_current_by_full_index(restore_idx)
                    self._status_bar.info(
                        f'列表刷新完成: {len(indices)}/{count} 张图片，正在加载当前列表图片...'
                    )
                    self._show_current_image_async()
                    self.after(
                        300,
                        lambda: self._preload_window_async(self._project.current_index),
                    )
                else:
                    self._project.current_index = -1
                    self._canvas.clear_all()
                    self._box_list_panel.set_image(None)
                    self._update_window_title()
                    self._status_bar.warning(
                        f'列表刷新完成: 当前筛选下没有匹配图片 (0/{count})'
                    )
                self.after_idle(self._start_pending_weights_load)
                return
            else:
                self._thumb_panel.set_images(self._project.image_list)
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
        else:
            self._status_bar.warning(f'目录中未找到图片: {dir_path}')
            showwarning(self, '提示', '目录中未找到支持的图片文件')

        # Let Tk paint the completed image list/preview before model loading
        # begins on its background thread.
        self.after_idle(self._start_pending_weights_load)

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

    def _clear_directory_labels(self):
        """清除上一目录的类别，防止异步加载期间沿用旧标签。"""
        self._label_manager.clear()
        self._config.last_label_file = ''
        self._config.label_definitions = []
        self._label_panel.refresh()
        self._refresh_label_filter_options()

    def _try_auto_folder_labels(self, dir_path: str):
        """每次打开目录仅识别本地标签集，未匹配时保留空类别供手动选择。"""
        self._clear_directory_labels()
        if self._try_import_root_classes_file(dir_path):
            return
        self._label_panel.refresh()
        self._refresh_label_filter_options()
        self._status_bar.warning('未找到本目录的标签集，请手动加载标签')

    def _try_import_root_classes_file(self, dir_path: str) -> bool:
        """自动识别当前图片目录直属 TXT 中的类别定义文件。"""
        for label_path, definitions in iter_label_set_candidates(Path(dir_path)):
            self._apply_detected_label_set(label_path, definitions)
            return True
        return False

    def _apply_detected_label_set(self, label_path: Path, definitions: list):
        """将已完整校验的标签集应用到界面及会话状态，不改写源文件。"""
        self._label_manager.clear()
        self._label_manager.from_dict_list(definitions)
        self._label_panel.refresh()
        self._refresh_label_filter_options()
        self._config.last_label_file = str(label_path)
        self._config.label_definitions = self._label_manager.to_dict_list()
        item = self._project.current_image
        if item:
            self._apply_folder_default_class(item)
        self._status_bar.success(f'已从 {label_path.name} 导入 {len(definitions)} 个标签')

    def _apply_folder_labels(self, names: list, dir_path: str, manual_choice: bool = False):
        """Load labels from folder names and write classes.txt beside images."""
        if manual_choice:
            self._config.forget_directory_label_file(dir_path)
            self._config_manager.save(self._config)
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
            self._apply_folder_labels(
                detection.class_names,
                str(self._project.image_dir),
                manual_choice=True,
            )
        elif dialog.result == 'file':
            self._load_label_file_from_disk()

    def _replace_labels_from_file(self, path: str) -> tuple:
        """Replace current labels from TXT/YAML, restoring them on any failure."""
        ext = Path(path).suffix.lower()
        if ext not in ('.txt', '.yaml', '.yml'):
            return 0, '不支持的文件格式'

        backup = self._label_manager.to_dict_list()
        self._label_manager.clear()
        try:
            if ext == '.txt':
                count = self._label_manager.load_from_txt(path)
            else:
                count = self._label_manager.load_from_yaml(path)
        except Exception as e:
            count = 0
            error = f'无法读取标签文件：{e}'
        else:
            error = ''

        if count <= 0:
            self._label_manager.clear()
            self._label_manager.from_dict_list(backup)
            if not error:
                error = '文件中没有可用标签'
            return 0, error

        self._label_panel.refresh()
        self._refresh_label_filter_options()
        item = self._project.current_image
        if item:
            self._apply_folder_default_class(item)
        return count, ''

    def _load_label_file_from_disk(self):
        """Load label definitions from a txt/yaml file."""
        # 文件选择优先定位到当前图片目录，未打开图片目录时沿用历史位置。
        initial_dir = (
            str(self._project.image_dir.resolve())
            if self._project.image_dir
            else self._last_label_file_directory()
        )
        filetypes = [
            ('标签文件', '*.txt *.yaml *.yml'),
            ('TXT文件', '*.txt'),
            ('YAML文件', '*.yaml *.yml'),
            ('所有文件', '*.*'),
        ]
        path = filedialog.askopenfilename(
            title='选择标签文件（TXT / YAML）',
            filetypes=filetypes,
            initialdir=initial_dir,
            parent=self,
        )
        if not path:
            return

        ext = Path(path).suffix.lower()
        count, error = self._replace_labels_from_file(path)
        if count <= 0:
            hint = error or '未在文件中找到可用标签。'
            if ext in ('.yaml', '.yml'):
                hint += '\n\nYAML 文件需要包含顶层 names 字段。'
            showwarning(self, '未找到标签', hint)
            return

        self._config.last_label_file = path
        self._config.label_definitions = self._label_manager.to_dict_list()
        self._config_manager.save(self._config)
        source = 'YAML' if ext in ('.yaml', '.yml') else 'TXT'
        self._status_bar.set_info(f'已从 {source} 文件加载 {count} 个标签')

    def _last_label_file_directory(self) -> str:
        if self._config.last_label_file:
            parent = Path(self._config.last_label_file).parent
            if parent.is_dir():
                return str(parent)
        return os.path.expanduser('~')

    def _load_weights(self):
        """Load YOLO model for pre-annotation."""
        if self._weights_load_in_progress:
            self._status_bar.warning('预标注权重正在加载，请稍候...')
            return
        if self._pre_annotator.is_busy:
            self._status_bar.warning('预标注进行中，暂时无法更换权重')
            return

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

        # An explicit manual choice supersedes any queued session restore.
        self._pending_weights_path = ''
        self._load_weights_async(path, show_error=True)

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
        from core.project import LABEL_FILTER_BACKGROUND
        class_id = self._project.label_filter_class_id
        if class_id is None:
            return ''
        if class_id == LABEL_FILTER_BACKGROUND:
            return '背景（无标签）'
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
        from core.project import LABEL_FILTER_BACKGROUND
        BACKGROUND_TEXT = '背景（无标签）'

        self._label_filter_menu.delete(0, 'end')
        self._label_filter_display_to_id = {'全部标签': None, BACKGROUND_TEXT: LABEL_FILTER_BACKGROUND}
        labels = self._label_manager.labels_for_display()
        current = self._project.label_filter_class_id

        self._label_filter_menu.add_radiobutton(
            label='全部标签', variable=self._label_filter_var, value='全部标签',
            command=lambda: self._apply_label_filter(None),
        )
        self._label_filter_menu.add_radiobutton(
            label=BACKGROUND_TEXT, variable=self._label_filter_var, value=BACKGROUND_TEXT,
            command=lambda: self._apply_label_filter(LABEL_FILTER_BACKGROUND),
        )
        if labels:
            self._label_filter_menu.add_separator()
            for index, label in enumerate(labels):
                text = f'[{label.class_id}] {label.name}'
                self._label_filter_display_to_id[text] = label.class_id
                self._label_filter_menu.add_radiobutton(
                    label=text,
                    variable=self._label_filter_var,
                    value=text,
                    command=lambda cid=label.class_id: self._apply_label_filter(cid),
                    columnbreak=(
                        index > 0
                        and index % LABEL_FILTER_ITEMS_PER_COLUMN == 0
                    ),
                )
        self._label_filter_menu.configure(postcommand=self._sync_label_filter_menu)

        if current == LABEL_FILTER_BACKGROUND:
            self._label_filter_var.set(BACKGROUND_TEXT)
            self._label_filter_menu_current = BACKGROUND_TEXT
        elif current is not None and self._label_manager.has_class(current):
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
        self._config.label_filter_class_id = class_id
        self._config_manager.save(self._config)
        self._refresh_label_filter_options()
        from core.project import LABEL_FILTER_BACKGROUND
        if class_id is None:
            self._label_filter_menu_current = '全部标签'
        elif class_id == LABEL_FILTER_BACKGROUND:
            self._label_filter_menu_current = '背景（无标签）'
        else:
            self._label_filter_menu_current = next(
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
        if self._video_path or self._pending_video_generation is not None:
            self._stop_video(clear_canvas=False)
        current = self._project.current_image
        if current:
            if not self._save_before_navigate():
                return
        if self._project.prev_image():
            if current:
                self._push_navigation_undo(current)
            self._show_current_image_async()

    def _next_image(self):
        if self._video_path or self._pending_video_generation is not None:
            self._stop_video(clear_canvas=False)
        current = self._project.current_image
        if current:
            if not self._save_before_navigate():
                return
        if self._project.next_image():
            if current:
                self._push_navigation_undo(current)
            self._show_current_image_async()

    def _goto_first(self):
        if self._video_path or self._pending_video_generation is not None:
            self._stop_video(clear_canvas=False)
        current = self._project.current_image
        if current:
            if not self._save_before_navigate():
                return
        if self._project.goto_first():
            if current:
                self._push_navigation_undo(current)
            self._show_current_image_async()

    def _goto_last(self):
        if self._video_path or self._pending_video_generation is not None:
            self._stop_video(clear_canvas=False)
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
        if self._video_path or self._pending_video_generation is not None:
            self._stop_video(clear_canvas=False)
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
        if self._video_path:
            return
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
        if self._video_path:
            return
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
        if self._video_path or self._pending_video_generation is not None:
            self._stop_video(clear_canvas=False)
        if not self._save_before_navigate():
            self._thumb_panel.set_current_by_full_index(self._project.current_index)
            return
        if self._project.goto_image(index):
            self._show_current_image_async()

    def _open_video(self):
        """读取视频并预览首帧，逐帧推理由 Ctrl+X 单独启动。"""
        path = filedialog.askopenfilename(
            title='选择视频文件', filetypes=VIDEO_FILETYPES, parent=self,
        )
        if not path:
            return
        if Path(path).suffix.lower() not in VIDEO_SUPPORTED_EXTENSIONS:
            self._status_bar.warning('所选文件格式不是支持的视频格式')
            return
        self._load_video_path(path)

    def _load_video_path(self, path, on_opened=None):
        """列表选择和文件对话框共用打开流程，图片保存仍走原有入口。"""
        if self._video_delete_running:
            self._status_bar.warning('正在删除视频，请稍候')
            return
        if (self._video_path or self._pending_video_generation is not None) and self._project.current_image:
            if not self._save_before_navigate():
                return
        if (not self._video_path and self._pending_video_generation is None
                and self._project.current_image):
            if not self._save_before_navigate():
                return
        self._stop_video(clear_canvas=False)
        self._video_open_action = on_opened
        self._video_opening_path = path
        self._status_bar.set_info(f'正在打开视频: {Path(path).name}')
        generation = self._video_generation
        self._pending_video_generation = generation
        open_stop_event = threading.Event()
        self._video_open_stop_event = open_stop_event

        def worker():
            capture = None
            try:
                import cv2

                capture = cv2.VideoCapture(path)
                if open_stop_event.is_set():
                    capture.release()
                    return
                if not capture.isOpened():
                    raise RuntimeError('无法打开视频')
                fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
                frame_count = max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0))
                if not math.isfinite(fps) or fps <= 0:
                    fps = DEFAULT_VIDEO_FPS
                duration = frame_count / fps if frame_count > 0 else 0.0
                if open_stop_event.is_set():
                    capture.release()
                    return
                self._call_in_main(
                    lambda cap=capture, rate=fps, length=duration, count=frame_count:
                    self._video_opened(generation, path, cap, rate, length, count)
                )
            except Exception as exc:
                if capture is not None:
                    capture.release()
                print(f'Video open error: {exc}')
                self._call_in_main(lambda: self._video_open_failed(generation, path))

        threading.Thread(target=worker, daemon=True).start()

    def _video_open_failed(self, generation, path):
        if generation != self._video_generation or self._pending_video_generation != generation:
            return
        self._pending_video_generation = None
        self._video_open_stop_event = None
        self._video_open_action = None
        self._video_opening_path = ''
        showerror(self, '视频打开失败', f'无法打开视频文件：\n{path}')
        self._status_bar.error(f'无法打开视频: {Path(path).name}')

    def _video_opened(self, generation, path, capture, fps, duration, frame_count):
        if generation != self._video_generation or self._pending_video_generation != generation:
            capture.release()
            return
        self._pending_video_generation = None
        self._video_open_stop_event = None
        self._video_opening_path = ''
        if self._video_item is None or self._video_item.path != Path(path):
            self._video_item = VideoItem(Path(path))
        self._video_item.fps = fps
        self._video_item.frame_count = frame_count
        self._video_annotations = self._video_item.annotations
        self._thumb_panel.set_video(path, selected=True)
        self._video_player = VideoPlayer(capture, fps, frame_count)
        self._video_path = path
        self._video_fps = fps
        self._video_duration = duration
        self._video_frame_index = 0
        self._video_frame_count = frame_count
        self._video_playing = False
        self._video_speed = 1.0
        self._video_controls.pack(fill='x', side='bottom')
        self._video_controls.set_duration(duration)
        self._video_controls.set_enabled(True)
        self._video_controls.set_playing(False)
        self._video_controls.set_speed(self._video_speed)
        self._status_bar.set_info(f'视频已读取: {Path(path).name}，按 Ctrl+X 开始逐帧推理')
        self._schedule_video_result_poll()
        action = self._video_open_action
        self._video_open_action = None
        if action is not None:
            action()

    def _on_video_selected(self):
        item = self._video_item
        if item is None:
            return
        path = str(item.path)
        if self._video_path == path:
            return
        if self._pending_video_generation is not None and self._video_opening_path == path:
            return
        self._load_video_path(path)

    def _ensure_listed_video_active(self, action):
        item = self._video_item
        if item is None:
            return
        path = str(item.path)
        if self._video_path == path:
            action()
        elif self._pending_video_generation is not None and self._video_opening_path == path:
            self._video_open_action = action
        else:
            self._load_video_path(path, on_opened=action)

    def _infer_listed_video(self):
        self._ensure_listed_video_active(self._pre_annotate_current)

    def _show_video_context_menu(self, event):
        if self._video_item is None:
            return
        busy = self._pre_annotator.is_busy or self._weights_load_in_progress
        menu = Menu(self, tearoff=0)
        menu.add_command(label='从列表中删除', command=self._remove_video_from_list)
        menu.add_command(
            label='删除视频', command=self._delete_video,
            state='disabled' if busy or self._video_export_running or self._video_delete_running
            or self._pending_video_generation is not None else 'normal',
        )
        menu.add_separator()
        playback_state = 'disabled' if self._video_inference_running or self._video_delete_running else 'normal'
        menu.add_command(label='上一帧', accelerator=',', state=playback_state,
                         command=lambda: self._step_listed_video(-1))
        menu.add_command(label='下一帧', accelerator='.', state=playback_state,
                         command=lambda: self._step_listed_video(1))
        menu.add_command(label='播放原视频', state=playback_state,
                         command=lambda: self._play_listed_video(False))
        if self._video_item.annotations is not None and not self._video_show_annotations:
            menu.add_command(label='播放标注视频', state=playback_state,
                             command=lambda: self._play_listed_video(True))
        menu.add_separator()
        menu.add_command(label='推理视频', command=self._infer_listed_video,
                         state='disabled' if busy or self._video_delete_running else 'normal')
        menu.add_command(
            label='导出视频', command=self._export_video,
            state='disabled' if self._video_export_running or self._video_export_pending
            or self._video_delete_running else 'normal',
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _show_image_after_video(self):
        """视频视图退出后只恢复当前图片，不修改项目中的图片列表或筛选。"""
        if self._project.current_image is not None:
            self._show_current_image_async()
        else:
            self._canvas.clear_all()
            self._box_list_panel.set_image(None)
            self._update_window_title()

    def _remove_video_from_list(self):
        item = self._video_item
        if item is None:
            return
        was_active = bool(self._video_path or self._pending_video_generation is not None)
        if was_active:
            self._stop_video(clear_canvas=False)
        self._video_item = None
        self._thumb_panel.clear_video()
        if was_active:
            self._show_image_after_video()
        self._status_bar.info(f'视频已从列表中移除，原文件保留: {item.path.name}')

    def _delete_video(self):
        item = self._video_item
        if item is None:
            return
        if (self._pre_annotator.is_busy or self._video_export_running
                or self._video_delete_running or self._pending_video_generation is not None):
            self._status_bar.warning('请等待视频读取、推理或导出任务完成后再删除视频')
            return
        if not askyesno(self, '删除视频', f'确定从磁盘删除此视频？此操作不可撤销。\n\n{item.path}'):
            return
        player = self._video_player
        if player is not None:
            self._stop_video(clear_canvas=False)
            self._show_image_after_video()
        self._video_delete_running = True

        def worker():
            error = ''
            try:
                if player is not None:
                    player._thread.join(VIDEO_DELETE_JOIN_SECONDS)
                    if player._thread.is_alive():
                        raise OSError('视频解码器尚未释放，请稍后重试')
                item.path.unlink()
            except OSError as exc:
                error = str(exc)

            def finish():
                self._video_delete_running = False
                if error:
                    showerror(self, '删除视频失败', error)
                    return
                if self._video_item is item:
                    self._video_item = None
                    self._thumb_panel.clear_video()
                self._status_bar.success(f'视频已从磁盘删除: {item.path.name}')

            self._call_in_main(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _export_video(self):
        item = self._video_item
        if item is None:
            return
        if self._video_delete_running:
            self._status_bar.warning('正在删除视频，请稍候')
            return
        if self._video_export_running or self._video_export_pending is not None:
            self._status_bar.info('视频导出任务已在进行中')
            return
        if item.annotations is None:
            if self._weights_load_in_progress:
                self._status_bar.warning('请等待模型权重加载完成后再导出视频')
                return
            if not self._pre_annotator.is_loaded:
                showinfo(self, '导出视频', '导出前需要完成逐帧推理，请先加载模型权重')
                self._load_weights()
                return
            if self._pre_annotator.is_busy and not self._video_inference_running:
                self._status_bar.warning('请等待当前预标注任务完成后再导出视频')
                return
        output = filedialog.asksaveasfilename(
            parent=self, title='导出带标注框的视频', initialdir=str(item.path.parent),
            initialfile=f'{item.path.stem}_annotated.mp4', defaultextension='.mp4',
            filetypes=[('MP4 视频', '*.mp4')],
        )
        if not output:
            return
        try:
            validate_video_export_path(item.path, output)
        except ValueError as exc:
            showerror(self, '导出视频', str(exc))
            return
        if item.annotations is not None:
            self._start_video_export(item, output)
            return

        def infer_then_export():
            self._video_export_pending = (item, output)
            if self._video_inference_running:
                self._status_bar.info('逐帧推理完成后将自动导出视频')
            else:
                self._pre_annotate_current()
                # 打开视频期间模型状态可能变化，未能启动时解除导出等待状态。
                if not self._video_inference_running:
                    self._video_export_pending = None

        self._ensure_listed_video_active(infer_then_export)

    def _start_video_export(self, item, output):
        """导出使用视频条目的标注快照，与图片导出和预览时钟相互独立。"""
        self._video_export_running = True
        self._video_export_token += 1
        token = self._video_export_token
        stop = threading.Event()
        self._video_export_stop_event = stop
        annotations = item.annotations
        self._video_controls.set_export_busy(True)
        self._status_bar.show_progress(0)
        self._status_bar.info('正在导出带检测框和置信度的视频...')

        def progress(current, total):
            def update():
                if token == self._video_export_token and self._video_export_running:
                    self._status_bar.update_progress(100 * current / total if total else 0)
                    self._status_bar.set_overlay(f'视频导出中: {current}/{total} 帧')
            self._call_in_main(update)

        def worker():
            result, error, cancelled = None, '', False
            try:
                result = export_annotated_video(
                    item.path, output, annotations, stop_event=stop, progress_callback=progress,
                )
            except VideoExportCancelled:
                cancelled = True
            except Exception as exc:
                error = str(exc)

            def finish():
                if token != self._video_export_token:
                    return
                self._video_export_running = False
                self._video_export_stop_event = None
                self._video_controls.set_export_busy(False)
                self._status_bar.hide_progress()
                if cancelled:
                    self._status_bar.info('视频导出已取消')
                elif error:
                    self._status_bar.error(f'视频导出失败: {error}')
                    showerror(self, '视频导出失败', error)
                else:
                    self._status_bar.success(f'视频导出完成: {result.output_path}')

            self._call_in_main(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _schedule_video_result_poll(self):
        if self._video_poll_after_id is None:
            self._video_poll_after_id = self.after(VIDEO_FRAME_POLL_MS, self._poll_video_result)

    def _poll_video_result(self):
        self._video_poll_after_id = None
        player = self._video_player
        if not player or not self._video_path:
            return
        result = player.take_frame()
        if result is not None:
            self._video_frame_index = result.index
            position = result.index / player.fps
            annotations = self._video_annotations_for_frame(result.index)
            self._canvas.show_video_frame(result.image, annotations)
            self._video_controls.set_position(position)
            if not self._video_inference_running and not self._video_export_running:
                state = ('原视频' if not self._video_show_annotations else
                         '推理完成' if self._video_annotations is not None else '未推理，按 Ctrl+X')
                count = str(self._video_frame_count) if self._video_frame_count else '?'
                self._status_bar.set_overlay(
                    f'视频: {Path(self._video_path).name} | {result.image.width}x{result.image.height} | '
                    f'{self._video_controls._format_time(position)} / '
                    f'{self._video_controls._format_time(self._video_duration)} | '
                    f'帧: {result.index + 1}/{count} | '
                    f'{state} | 检测框: {len(annotations)} | {self._video_speed:g}x'
                )
        playing = player.playing
        if playing != self._video_playing:
            self._video_playing = playing
            self._video_controls.set_playing(playing)
        if player.error and not self._video_error_reported:
            self._video_error_reported = True
            self._status_bar.error(f'视频播放失败: {player.error}')
        self._schedule_video_result_poll()

    def _video_annotations_for_frame(self, index):
        """检测框与解码画面通过原始帧号对应，空帧也占用缓存位置。"""
        if not self._video_show_annotations:
            return []
        cache = self._video_annotations
        return cache[index] if cache is not None and 0 <= index < len(cache) else []

    def _step_listed_video(self, delta):
        self._ensure_listed_video_active(lambda: self._step_video_frame(delta))

    def _step_video_frame(self, delta):
        """逐帧操作总是暂停；连续按键以最近请求的帧号累加，不丢失步数。"""
        player = self._video_player
        if player is None or self._video_inference_running:
            return
        target = max(0, self._video_frame_index + delta)
        if self._video_frame_count:
            target = min(target, self._video_frame_count - 1)
        self._video_seek_active = False
        self._video_resume_after_seek = False
        self._video_frame_index = target
        player.seek_frame(target, playing=False)
        self._video_playing = False
        self._video_controls.set_playing(False)
        self._video_controls.set_position(target / player.fps)

    def _play_listed_video(self, show_annotations):
        self._ensure_listed_video_active(lambda: self._play_video_view(show_annotations))

    def _play_video_view(self, show_annotations):
        """仅切换预览叠加；原视频播放和带框导出共用已完成的推理缓存。"""
        player = self._video_player
        if player is None or self._video_inference_running:
            return
        self._video_show_annotations = show_annotations
        if (not show_annotations and self._canvas._video_mode
                and self._canvas._source_pil_image is not None):
            self._canvas.show_video_frame(self._canvas._source_pil_image, [])
        self._video_seek_active = False
        self._video_resume_after_seek = False
        player.play(self._video_frame_index)
        self._video_playing = player.playing
        self._video_controls.set_playing(self._video_playing)
        self._status_bar.info('正在播放标注视频' if show_annotations else '正在播放原视频（不显示推理标注）')

    def _toggle_video_play(self):
        player = self._video_player
        if not player or self._video_inference_running:
            return
        if player.playing:
            player.pause(self._video_frame_index)
        else:
            player.play(self._video_frame_index)
        self._video_playing = player.playing
        self._video_controls.set_playing(self._video_playing)

    def _begin_video_seek(self):
        player = self._video_player
        if not player or self._video_inference_running:
            return
        self._video_seek_active = True
        self._video_resume_after_seek = player.playing
        player.pause(self._video_frame_index)
        self._video_playing = False
        self._video_controls.set_playing(False)

    def _seek_video(self, position):
        player = self._video_player
        if not player or self._video_inference_running:
            return
        playing = self._video_resume_after_seek if self._video_seek_active else player.playing
        self._video_seek_active = False
        position = max(0.0, min(position, self._video_duration))
        self._video_frame_index = min(int(position * player.fps),
                                      max(0, self._video_frame_count - 1))
        player.seek(position, playing=playing)
        self._video_playing = playing
        self._video_controls.set_playing(playing)
        return 'break'

    def _set_video_speed(self, speed):
        if not self._video_player or self._video_inference_running:
            return
        self._video_speed = speed
        self._video_player.set_speed(speed, self._video_frame_index)
        self._video_controls.set_speed(speed)

    def _stop_video(self, clear_canvas=True):
        """停止后台解码并释放视频资源。"""
        self._video_generation += 1
        self._video_open_action = None
        self._video_opening_path = ''
        self._video_export_pending = None
        self._thumb_panel.select_video(False)
        if self._video_inference_stop_event is not None:
            self._video_inference_stop_event.set()
            self._video_inference_stop_event = None
        if self._video_inference_running:
            self._status_bar.hide_progress()
            self._toolbar.set_batch_running(False)
        self._video_inference_running = False
        if self._video_open_stop_event is not None:
            self._video_open_stop_event.set()
            self._video_open_stop_event = None
        self._pending_video_generation = None
        player = self._video_player
        self._video_player = None
        if player is not None:
            player.close()
        self._video_path = ''
        self._video_playing = False
        self._video_seek_active = False
        self._video_resume_after_seek = False
        self._video_annotations = None
        self._video_show_annotations = True
        self._video_frame_count = 0
        self._video_frame_index = 0
        self._video_error_reported = False
        self._video_duration = 0.0
        self._video_fps = 0.0
        self._video_controls.set_playing(False)
        self._video_controls.set_duration(0.0)
        self._video_controls.pack_forget()
        if self._video_poll_after_id is not None:
            try:
                self.after_cancel(self._video_poll_after_id)
            except RuntimeError:
                pass
            self._video_poll_after_id = None
        if clear_canvas:
            self._canvas.clear_all()
        self._update_window_title()

    def _remove_missing_image_item(self, missing_item: ImageItem) -> bool:
        """Remove an externally deleted image from project and visible-list state."""
        if missing_item.path.is_file():
            return False
        try:
            full_index = next(
                index for index, item in enumerate(self._project.image_list)
                if item is missing_item
            )
        except StopIteration:
            return False

        visible_items = [
            self._project.image_list[index]
            for index in self._project.get_visible_indices()
            if 0 <= index < len(self._project.image_list)
        ]
        replacement = self._batch_removal_replacement(
            visible_items, missing_item, {id(missing_item)},
        )
        self._image_loader.evict_path(missing_item.path)
        self._filter_status_snapshot.pop(id(missing_item), None)
        self._image_undo_managers.pop(id(missing_item), None)
        self._project.remove_image_at(full_index)
        self._navigation_undo_stack = [
            item for item in self._navigation_undo_stack if item is not missing_item
        ]

        replacement_index = next(
            (
                index for index, item in enumerate(self._project.image_list)
                if item is replacement
            ),
            -1,
        )
        self._project.current_index = replacement_index
        self._refresh_image_list_view(jump='keep', navigate=False)
        if replacement_index >= 0:
            self._thumb_panel.select_only_current()
            self._show_current_image_async(clear_canvas=False)
        else:
            self._canvas.clear_all()
            self._box_list_panel.set_image(None)
            self._update_window_title()
        return True

    def _show_current_image_async(self, clear_canvas: bool = True, on_displayed=None):
        """Display current image using async loading (non-blocking)."""
        item = self._project.current_image
        if not item:
            if on_displayed:
                on_displayed()
            return

        # If already loaded, display immediately
        if item.is_loaded and item._pil_image:
            self._display_current_image(on_displayed=on_displayed)
            return

        # Load asynchronously
        if clear_canvas:
            self._canvas.clear_all()
        self._status_bar.info(f'正在加载: {item.name}')

        def _on_loaded(image_item, success):
            if success and self._project.current_image == image_item:
                self._display_current_image(on_displayed=on_displayed)
            elif not success:
                if (
                    self._project.current_image is image_item
                    and self._remove_missing_image_item(image_item)
                ):
                    self._status_bar.warning(
                        f'图片文件已不存在，已从列表移除: {image_item.name}'
                    )
                    if on_displayed:
                        on_displayed()
                    return
                if self._project.current_image is image_item:
                    self._canvas.clear_all()
                    self._box_list_panel.set_image(None)
                self._status_bar.error(f'加载失败: {image_item.name}')
                if on_displayed:
                    on_displayed()
            elif on_displayed:
                on_displayed()

        self._image_loader.load_image_async(
            item, _on_loaded, main_thread_schedule=self._call_in_main,
        )

    def _display_current_image(self, on_displayed=None):
        """Actually display the loaded image on canvas (must be called on main thread)."""
        if self._video_path or self._pending_video_generation is not None:
            self._stop_video(clear_canvas=False)
        item = self._project.current_image
        if not item:
            if on_displayed:
                on_displayed()
            return

        self._last_displayed_index = self._project.current_index

        if not (item.is_loaded and item._pil_image):
            self._canvas.clear_all()
            if on_displayed:
                on_displayed()
            return

        # Load annotations before drawing so boxes appear with the image
        self._load_item_annotations(item)

        self._canvas.set_image(item, item._pil_image)

        image_list, center_index = self._visible_preload_window(self._project.current_index)
        self._image_loader.release_outside_window(
            self._project.image_list,
            center_index,
            forward=PRELOAD_FORWARD,
            backward=PRELOAD_BACKWARD,
            window_image_list=image_list,
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
        if on_displayed:
            on_displayed()

    def _on_annotation_changed(self):
        """Called when annotations are modified."""
        if self._video_path:
            return
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
        if self._video_path:
            return
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
        if self._video_path:
            return
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
        if self._video_path or self._pending_video_generation is not None:
            return
        item = self._project.current_image
        if not item:
            return

        selected = item.get_selected_annotations()
        if not selected:
            return

        self._remember_recent_class(class_id)

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
        if self._video_path:
            self._status_bar.warning('阈值已更新，按 Ctrl+X 重新推理视频以应用新阈值')

    def _quick_select_class(self, index: int):
        """Quick select class by number key (1-9)."""
        if self._video_path:
            return
        labels = self._label_manager.labels_for_display()
        if index < len(labels):
            self._label_manager.current_class_id = labels[index].class_id
            self._label_panel.refresh()
            self._on_class_selected(labels[index].class_id)

    def _select_all(self):
        if self._video_path:
            return
        item = self._project.current_image
        if item:
            item.select_all()
            self._canvas.refresh()
            self._box_list_panel.refresh()

    def _inverse_select(self):
        if self._video_path:
            return
        item = self._project.current_image
        if item:
            item.inverse_selection()
            self._canvas.refresh()
            self._box_list_panel.refresh()

    def _delete_selected(self):
        if self._video_path:
            self._status_bar.warning('视频播放期间不可删除图片列表项目')
            return
        item = self._project.current_image
        if not item:
            return

        selected = item.get_selected_annotations()
        if not selected:
            return

        # 保留原对象与顺序，让撤销可恢复选择，也保持其他编辑命令的对象引用有效。
        indices = [(idx, ann) for idx, ann in enumerate(item.annotations) if ann.is_selected]
        removed_ids = {ann.id for _, ann in indices}
        remaining = [ann for ann in item.annotations if ann.id not in removed_ids]
        # 批量删除后从最后一个被删框向后选择；没有后续框时回退到剩余末尾框。
        following = item.annotations[indices[-1][0] + 1:]
        replacement = following[0] if following else remaining[-1] if remaining else None

        def execute():
            # 删除目标固定在命令创建时；重做不依赖用户后来改变的选择。
            item.annotations = [ann for ann in item.annotations if ann.id not in removed_ids]
            item.deselect_all()
            if replacement is not None:
                replacement.is_selected = True
            item.mark_dirty()
            self._canvas.refresh()
            self._on_annotation_changed()

        def undo():
            item.deselect_all()
            for idx, ann in indices:
                ann.is_selected = True
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

    def _undo(self):
        if self._video_path:
            return
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
        if self._video_path:
            return
        manager = self._current_image_undo_manager()
        desc = manager.redo() if manager.can_redo() else None
        if desc:
            self._canvas.refresh()
            self._on_annotation_changed()
            if self._project.current_image:
                self._status_bar.set_info(self._compose_status_text(self._project.current_image, mode=''))
            return
        self._status_bar.set_info('当前图没有可重做内容')

    def _save_current(self):
        if self._video_path:
            self._status_bar.info('视频检测结果为预览内容，不保存为图片标注')
            return
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
        try:
            txt_path.parent.mkdir(parents=True, exist_ok=True)
            write_yolo_annotations_atomic(txt_path, item.annotations, w, h)
            item._annotation_path_checked = True
            item._annotation_file_path = txt_path
            item.mark_clean()
            class_ids = {annotation.class_id for annotation in item.annotations}
            if class_ids:
                self._project.cache_label_contains(item.path, class_ids)
            else:
                self._project.cache_background(item.path)
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

    def _fit_window(self):
        if self._video_path and self._canvas._source_pil_image is not None:
            self._canvas.show_video_frame(
                self._canvas._source_pil_image, self._canvas._video_annotations,
            )
            return
        self._canvas.fit_to_window()
        self._on_zoom_changed(self._canvas._scale)

    def _rotate_current_image(self):
        """切换当前静态图片的显示方向，标注仍对应原图坐标。"""
        self._canvas.toggle_rotation_180()

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

    def _set_pre_annotation_inference_mode(self, mode: str):
        """应用菜单选择的预标注检测分支，后续单张和批量任务共用。"""
        effective_mode = self._pre_annotator.set_inference_mode(mode)
        if (
            mode == INFERENCE_MODE_ONE_TO_ONE
            and self._pre_annotator.is_loaded
            and not self._pre_annotator.is_busy
            and effective_mode == INFERENCE_MODE_ONE_TO_MANY_NMS
        ):
            self._status_bar.warning(
                '当前权重不支持 one-to-one，已回落到 one-to-many+NMS'
            )
        elif (
            self._pre_annotator.is_loaded
            and effective_mode == INFERENCE_MODE_BACKEND_AUTO
        ):
            self._status_bar.warning(
                '当前导出权重的检测分支已固化，将按权重内置模式推理'
            )
        elif (
            mode == INFERENCE_MODE_ONE_TO_MANY_NMS
            and self._pre_annotator.is_loaded
            and not self._pre_annotator.is_busy
            and effective_mode == INFERENCE_MODE_ONE_TO_ONE
        ):
            self._status_bar.warning(
                '当前权重不包含 one-to-many 分支，将继续使用 one-to-one'
            )

    def _pre_annotate_current(self):
        """Run pre-annotation on current image (background thread)."""
        if self._pending_video_generation is not None:
            self._status_bar.warning('视频正在读取，请稍候...')
            return
        if self._weights_load_in_progress:
            self._status_bar.warning('预标注权重正在加载，请稍候...')
            return
        if self._pre_annotator.is_busy:
            self._status_bar.warning('预标注进行中，请稍候...')
            return

        if not self._pre_annotator.is_loaded:
            showinfo(self, '提示', '请先加载权重文件')
            self._load_weights()
            return

        if self._video_path:
            self._pre_annotate_video()
            return

        item = self._project.current_image
        if not item or not item.is_loaded:
            return

        self._status_bar.set_info('正在预标注...')

        def on_done(annotations, elapsed):
            if self._project.current_image is not item:
                self._status_bar.info(
                    f'预标注完成（当前已切换图片，结果未应用），权重: '
                    f'{Path(self._pre_annotator.model_path).name}'
                )
                return

            count = len(annotations)
            time_text = format_duration(elapsed)
            previous_annotations = [ann.copy() for ann in item.annotations]

            def replace_annotations(values):
                item.annotations = [ann.copy() for ann in values]
                item.mark_dirty()
                self._canvas.refresh()
                self._on_annotation_changed()

            def execute():
                replace_annotations(annotations)

            def undo():
                replace_annotations(previous_annotations)

            self._mark_real_annotation_change(item)
            cmd = Command(
                description=f'预标注替换为 {count} 个框',
                execute=execute, undo=undo,
            )
            self._current_image_undo_manager().execute(cmd)

            if not self._save_item_annotations(item):
                self._report_save_failure(item)
                return

            self._status_bar.success(
                f'预标注完成并已覆盖保存: {count} 个目标, 耗时 {time_text}，'
                f'权重: {Path(self._pre_annotator.model_path).name}'
            )

        self._pre_annotator.predict_async(
            item.path, self._threshold, self._label_manager,
            on_done, main_thread_schedule=self._call_in_main,
        )

    def _pre_annotate_video(self):
        """完成整个视频的逐帧推理后发布缓存，预览始终使用对应帧的结果。"""
        player = self._video_player
        if not player or self._video_inference_running:
            return
        generation = self._video_generation
        player.pause(self._video_frame_index)
        self._video_playing = False
        self._video_seek_active = False
        self._video_inference_running = True
        self._video_annotations = None
        item = self._video_item
        if item is not None:
            item.annotations = None
        self._video_controls.set_playing(False)
        self._video_controls.set_enabled(False)
        self._toolbar.set_batch_running(True)
        self._status_bar.show_progress(0)
        self._status_bar.set_info('正在逐帧推理视频，完成后可播放带检测框和置信度的画面...')

        def on_progress(current, total):
            if generation != self._video_generation or not self._video_inference_running:
                return
            self._status_bar.update_progress(100 * current / total if total else 0)
            count = f'{current}/{total}' if total else str(current)
            self._status_bar.set_overlay(f'视频逐帧推理中: {count} 帧')

        def on_done(result):
            if generation != self._video_generation or self._video_player is not player:
                return
            self._video_inference_running = False
            self._video_inference_stop_event = None
            self._video_controls.set_enabled(True)
            self._toolbar.set_batch_running(False)
            self._status_bar.hide_progress()
            pending_export = self._video_export_pending
            self._video_export_pending = None
            if result.cancelled:
                self._status_bar.warning('视频推理已停止，未应用不完整结果；按 Ctrl+X 可重新推理')
                return
            if result.error:
                self._status_bar.error(f'视频推理失败: {result.error}')
                return
            self._video_annotations = result.annotations
            self._video_show_annotations = True
            if item is not None:
                item.annotations = result.annotations
                item.fps = player.fps
                item.frame_count = len(result.annotations)
            self._video_frame_count = len(result.annotations)
            player.set_frame_count(self._video_frame_count)
            self._video_duration = self._video_frame_count / player.fps
            self._video_controls.set_duration(self._video_duration)
            self._video_frame_index = 0
            player.seek(0.0, playing=False)
            self._status_bar.success(
                f'视频推理完成: 共 {self._video_frame_count} 帧，'
                f'耗时 {format_duration(result.elapsed)}；点击播放查看检测框和置信度'
            )
            if pending_export is not None and pending_export[0] is item:
                self._start_video_export(item, pending_export[1])

        try:
            self._video_inference_stop_event = self._pre_annotator.predict_video_async(
                self._video_path, self._threshold, self._label_manager, on_done,
                progress_callback=on_progress, main_thread_schedule=self._call_in_main,
            )
        except Exception as exc:
            on_done(VideoPrediction([], 0.0, error=str(exc)))

    def _apply_batch_annotations_to_item(self, item: ImageItem, annotations: list):
        """Replace one image's annotations with its batch prediction result."""
        item.annotations = [ann.copy() for ann in annotations]
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
        if self._video_path or self._pending_video_generation is not None:
            self._pre_annotate_current()
            return
        if self._weights_load_in_progress:
            self._status_bar.warning('预标注权重正在加载，请稍候...')
            return
        if self._pre_annotator.is_busy:
            self._status_bar.warning('预标注进行中，请稍候...')
            return

        if not self._pre_annotator.is_loaded:
            showinfo(self, '提示', '请先加载权重文件')
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
                    f'总耗时 {time_total}, 平均 {time_avg}/张，'
                    f'权重: {Path(self._pre_annotator.model_path).name}'
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
        self._status_bar.info('正在停止视频推理...' if self._video_inference_running
                              else '正在停止批量预标注...')

    def _export(self):
        if self._video_path:
            self._status_bar.warning('请先返回图片，再执行图片导出')
            return
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

    def _show_statistics(self):
        StatisticsDialog(self, self._project, self._label_manager)

    def _show_shortcuts(self):
        shortcuts = """
快捷键说明:
═══════════════════════════
Ctrl+O      打开目录
Ctrl+Shift+O 打开视频
F5          刷新当前图片目录
, / .       视频上一帧/下一帧（暂停在目标帧）
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
Tab         点击图片空白处后遍历全部框；点击重复框后只遍历该组（现代画布）
F           适应窗口
T           切换标签显示(全部→精简→隐藏)
R           当前图片旋转180°(再次按下恢复)
滚轮        缩放
中键/空格+拖拽  平移
Ctrl+X      预标注当前图 / 视频逐帧推理
Ctrl+Shift+X 批量预标注
Ctrl+点击   多选/取消选择
右键拖拽    框选多个锚框
右键菜单    全选/反选/删除/修改标签
S           修改标签(选中锚框后，焦点到标签搜索)
Escape      取消绘制 / 取消选中
"""
        showinfo(self, '快捷键说明', shortcuts)

    def _show_about(self):
        showinfo(self, '关于', '目标检测标注工具 v1.0\n\n支持格式: YOLO, COCO, Pascal VOC\n预标注: YOLO (ultralytics)')

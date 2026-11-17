"""现代标注工作台主窗口，Qt 控件与业务控制器通过明确接口连接。"""

import queue
import re
import sys
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QEvent, QSize, QRect, QPoint
from PySide6.QtGui import QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QToolButton, QSplitter, QLineEdit, QTextEdit, QPlainTextEdit,
    QComboBox, QAbstractSpinBox, QSizePolicy, QSlider, QLayout,
)
from ui.qt.controller import ApplicationController
from ui.qt.state import initialize_state
from ui.qt.canvas import AnnotationCanvas
from ui.qt.panels import (ThumbnailPanel, LabelPanel, BoxListPanel, ThresholdSlider,
                           VideoControls, StatusBar, FilterButton, section)
from ui.qt.theme import FONT_FAMILY, FONT_SIZE, icon, apply_theme, set_button_icon
from ui.qt.dialogs import showerror
from utils.paths import get_icon_path
from utils.dpi import configure_ui_dpi
from io_ops.pre_annotator import DEFAULT_INFERENCE_MODE, INFERENCE_MODE_ONE_TO_ONE, INFERENCE_MODE_ONE_TO_MANY_NMS

# ==================== 工作台参数设置区 ====================
DEFAULT_WINDOW_SIZE = (1600, 960)
MINIMUM_WINDOW_SIZE = (1100, 680)
DEFAULT_PANE_WIDTHS = (270, 940, 350)
MAIN_QUEUE_INTERVAL_MS = 16
COMMAND_BUTTON_SPACING = 5
INPUT_WIDGET_TYPES = (QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QAbstractSpinBox, QSlider)
INPUT_GLOBAL_SHORTCUTS = {'Ctrl+S', 'Ctrl+Shift+S', 'Ctrl+O', 'Ctrl+Shift+O', 'Ctrl+G'}
CANVAS_CYCLE_SHORTCUT = 'Tab'
_application = None


def ensure_application():
    global _application
    configure_ui_dpi('modern')
    _application = QApplication.instance() or QApplication(sys.argv[:1])
    _application.setStyle('Fusion')
    _application.setFont(QFont(FONT_FAMILY, FONT_SIZE))
    return _application


class FlowLayout(QLayout):
    """按可用宽度自动换行，让带文字的操作按钮在窄窗口中仍完整可见。"""
    def __init__(self, parent):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(COMMAND_BUTTON_SPACING)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < self.count() else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < self.count() else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), measure=True)

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def sizeHint(self):
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect)

    def _arrange(self, rect, measure=False):
        x, y, row_height = rect.x(), rect.y(), 0
        for item in self._items:
            size = item.sizeHint()
            if x > rect.x() and x + size.width() > rect.right() + 1:
                x = rect.x()
                y += row_height + self.spacing()
                row_height = 0
            if not measure:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + self.spacing()
            row_height = max(row_height, size.height())
        return y + row_height - rect.y()


class CommandBar(QFrame):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._batch_running = False
        self.setObjectName('commands')
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = FlowLayout(self)
        for text, graphic, tooltip, action in [
            ('前框 (Q)', 'prev', '选择前一个标注框（Q）', app._prev_bbox),
            ('后框 (E)', 'next', '选择后一个标注框（E）', app._next_bbox),
            ('撤回', 'undo', '撤回（Ctrl+Z）', app._undo),
            ('重做', 'redo', '重做（Ctrl+Y）', app._redo),
            ('删除', 'trash', '删除选中标注（Delete）', app._delete_selected),
            ('适应', 'fit', '适应窗口（F）', app._fit_window),
            ('旋转', 'rotate', '旋转 180°（R）', app._rotate_current_image),
            ('标签', 'label', '切换标签显示（T）', app._cycle_label_mode),
        ]:
            button = QPushButton(text)
            set_button_icon(button, graphic)
            button.setToolTip(tooltip)
            button.clicked.connect(action)
            layout.addWidget(button)
        self.infer = QPushButton('预标注')
        set_button_icon(self.infer, 'spark')
        self.infer.setToolTip('推理当前图片或完整视频（Ctrl+X）')
        self.infer.clicked.connect(app._pre_annotate_current)
        self.batch = QPushButton('批量推理')
        self.batch.setToolTip('批量推理当前筛选中的图片（Ctrl+Shift+X）')
        self.batch.clicked.connect(lambda: app._stop_batch_pre_annotate() if self._batch_running else app._batch_pre_annotate())

    def set_batch_running(self, running):
        self._batch_running = running
        self.batch.setText('停止推理' if running else '批量推理')
        self.batch.setToolTip('停止批量推理' if running else '批量推理当前筛选中的图片（Ctrl+Shift+X）')
        self.infer.setEnabled(not running)


class AnnotationApp(QMainWindow, ApplicationController):
    def __init__(self, restore_session=True, config_directory=None):
        ensure_application()
        super().__init__()
        self._timers = {}
        self._next_timer = 0
        self._closing = False
        self._callback_errors = []
        self._requested_ui = None
        initialize_state(self, config_directory)
        apply_theme(QApplication.instance(), self._config.ui_theme)
        self.setWindowTitle('视觉标注工作台')
        self.setMinimumSize(*MINIMUM_WINDOW_SIZE)
        self.resize(*DEFAULT_WINDOW_SIZE)
        if get_icon_path().is_file():
            self.setWindowIcon(QIcon(str(get_icon_path())))
        match = re.match(r'(\d+)x(\d+)', self._config.window_geometry or '')
        if match:
            self.resize(max(MINIMUM_WINDOW_SIZE[0], int(match[1])), max(MINIMUM_WINDOW_SIZE[1], int(match[2])))
        self._setup_ui()
        self._setup_menus()
        self.class_filter.setMenu(self._label_filter_menu.widget)
        self._label_filter_var.callbacks.append(lambda text: self.class_filter.setText(str(text)))
        self._inference_mode_var.callbacks.append(self.inference_combo.setCurrentText)
        self._setup_canvas_context_menu()
        self._rebuild_label_filter_menu()
        apply_theme(QApplication.instance(), self._config.ui_theme)
        self._toast = QLabel(self._canvas)
        self._toast.setObjectName('toast')
        self._toast.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._toast.hide()
        self._toast_after_id = None
        self._setup_shortcuts()
        QApplication.instance().installEventFilter(self)
        self._poll_main_thread_queue()
        if restore_session:
            self.after(100, self._restore_session)

    def _setup_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 10, 18, 6)
        layout.setSpacing(12)
        # 左侧工作区自带操作行；右栏从同一顶部起排，避免被项目标题挤占高度。
        self._paned = QSplitter(Qt.Orientation.Horizontal)
        self._paned.setChildrenCollapsible(False)
        self._paned.setHandleWidth(12)
        layout.addWidget(self._paned, 1)
        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        for text, graphic, callback, tooltip in [
            ('打开目录', 'folder', self._open_directory, '选择图片目录（Ctrl+O）'),
            ('打开视频', 'video', self._open_video, '打开视频（Ctrl+Shift+O）'),
            ('加载标签', 'label', self._load_label_file, '从文件或目录名称导入标签类别'),
            ('保存', 'save', self._save_current, '保存当前标注（Ctrl+S）'),
            ('导出数据集', 'export', self._export, '导出数据集（Ctrl+Shift+S）'),
        ]:
            button = QPushButton(text)
            set_button_icon(button, graphic)
            button.clicked.connect(callback)
            button.setToolTip(tooltip)
            header.addWidget(button)
        header.addStretch()
        workspace_layout.addLayout(header)
        self._workspace_paned = QSplitter(Qt.Orientation.Horizontal)
        self._workspace_paned.setChildrenCollapsible(False)
        self._workspace_paned.setHandleWidth(12)
        workspace_layout.addWidget(self._workspace_paned, 1)
        self._paned.addWidget(workspace)
        left = QWidget()
        left.setMinimumWidth(220)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        filters = QFrame()
        filters.setObjectName('panel')
        filter_layout = QVBoxLayout(filters)
        filter_layout.setContentsMargins(12, 14, 12, 14)
        filter_layout.addLayout(section('筛选资源', 'FILTERS'))
        filter_row = QHBoxLayout()
        self._status_filter_combo = FilterButton()
        self._status_filter_combo.addItems(['全部图片', '已标注', '未标注', '不确定'])
        self._status_filter_combo.textActivated.connect(self._on_status_filter_combo_selected)
        filter_row.addWidget(self._status_filter_combo, 1)
        self.class_filter = QToolButton()
        self.class_filter.setObjectName('filter')
        self.class_filter.setMinimumWidth(0)
        self.class_filter.setText('全部标签')
        self.class_filter.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.class_filter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        filter_row.addWidget(self.class_filter, 1)
        filter_layout.addLayout(filter_row)
        left_layout.addWidget(filters)
        self._thumb_panel = ThumbnailPanel(self)
        left_layout.addWidget(self._thumb_panel, 1)
        navigation = QHBoxLayout()
        for text, callback, tooltip in [
            ('‹ 上一张', self._prev_image, '上一张图片（A / ←）'),
            ('跳转', self._jump_to_image, '跳转到指定图片（Ctrl+G）'),
            ('下一张 ›', self._next_image, '下一张图片（D / →）'),
        ]:
            button = QPushButton(text)
            button.setToolTip(tooltip)
            button.clicked.connect(callback)
            navigation.addWidget(button)
        left_layout.addLayout(navigation)
        self._workspace_paned.addWidget(left)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(8)
        self._toolbar = CommandBar(self)
        # 推理操作紧随标签导入，编辑栏集中保留画布和标注框操作。
        header.insertWidget(3, self._toolbar.infer)
        header.insertWidget(4, self._toolbar.batch)
        center_layout.addWidget(self._toolbar)
        canvas_frame = QFrame()
        canvas_frame.setObjectName('panel')
        canvas_layout = QVBoxLayout(canvas_frame)
        canvas_layout.setContentsMargins(1, 1, 1, 1)
        self._canvas = AnnotationCanvas(self)
        canvas_layout.addWidget(self._canvas)
        center_layout.addWidget(canvas_frame, 1)
        hint = QLabel('拖动绘制  ·  单击选择  ·  滚轮缩放  ·  空格拖动平移  ·  R 旋转  ·  F 适应窗口')
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setObjectName('eyebrow')
        center_layout.addWidget(hint)
        self._video_controls = VideoControls(self)
        center_layout.addWidget(self._video_controls)
        self._video_controls.hide()
        self._workspace_paned.addWidget(center)
        right = QWidget()
        right.setMinimumWidth(310)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        model_frame = QFrame()
        model_frame.setObjectName('panel')
        model_layout = QVBoxLayout(model_frame)
        model_layout.setContentsMargins(14, 14, 14, 14)
        model_layout.addLayout(section('推理引擎', 'INFERENCE'))
        self.model_name = QLabel('未加载模型')
        self.model_name.setObjectName('muted')
        self.model_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        model_layout.addWidget(self.model_name)
        engine_row = QHBoxLayout()
        self.load_model = load_model = QPushButton('选择权重…')
        load_model.setToolTip('选择模型权重（.pt / .onnx / .engine / .trt）')
        set_button_icon(load_model, 'spark')
        load_model.clicked.connect(self._load_weights)
        engine_row.addWidget(load_model)
        self.inference_combo = QComboBox()
        self.inference_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.inference_combo.setMinimumWidth(0)
        self.inference_combo.setToolTip('选择推理分支：one-to-one / one-to-many+NMS')
        self.inference_combo.addItems([INFERENCE_MODE_ONE_TO_ONE, INFERENCE_MODE_ONE_TO_MANY_NMS])
        self.inference_combo.setCurrentText(DEFAULT_INFERENCE_MODE)
        self.inference_combo.textActivated.connect(self._set_pre_annotation_inference_mode)
        engine_row.addWidget(self.inference_combo, 1)
        model_layout.addLayout(engine_row)
        right_layout.addWidget(model_frame)
        self._right_paned = QSplitter(Qt.Orientation.Vertical)
        self._right_paned.setChildrenCollapsible(False)
        self._right_paned.setHandleWidth(10)
        self._label_panel = LabelPanel(self)
        self._right_paned.addWidget(self._label_panel)
        self._threshold_slider = ThresholdSlider(self)
        self._threshold_slider.setFixedHeight(52)
        self._right_paned.addWidget(self._threshold_slider)
        self._box_list_panel = BoxListPanel(self)
        self._right_paned.addWidget(self._box_list_panel)
        self._right_paned.setSizes([430, 52, 230])
        saved_sashes = self._config.right_pane_sash_positions
        if len(saved_sashes) >= 2:
            self._right_paned.setSizes([max(240, saved_sashes[0]), 52, 230])
        right_layout.addWidget(self._right_paned, 1)
        self._paned.addWidget(right)
        left_width = self._config.left_panel_width or DEFAULT_PANE_WIDTHS[0]
        self._workspace_paned.setSizes([left_width, DEFAULT_PANE_WIDTHS[1]])
        self._workspace_paned.setStretchFactor(1, 1)
        self._paned.setSizes([left_width + DEFAULT_PANE_WIDTHS[1],
                              self._config.right_panel_width or DEFAULT_PANE_WIDTHS[2]])
        self._paned.setStretchFactor(0, 1)
        self._status_bar = StatusBar()
        layout.addWidget(self._status_bar)

    def _setup_shortcuts(self):
        self.shortcuts = {
            'Ctrl+O': self._open_directory, 'Ctrl+Shift+O': self._open_video,
            'Ctrl+S': self._save_current, 'Ctrl+Shift+S': self._export,
            'Ctrl+Z': self._undo, 'Ctrl+Y': self._redo, 'Ctrl+A': self._select_all,
            'Ctrl+I': self._inverse_select, 'Del': self._delete_selected,
            'Ctrl+Del': lambda: self._delete_image(self._project.current_index),
            'Left': self._prev_image, 'A': self._prev_image, 'Right': self._next_image, 'D': self._next_image,
            'Home': self._goto_first, 'End': self._goto_last, 'Ctrl+G': self._jump_to_image,
            'F': self._fit_window, 'R': self._rotate_current_image, 'T': self._cycle_label_mode,
            'Esc': self._canvas.cancel_or_deselect, 'Ctrl+X': self._pre_annotate_current,
            'Ctrl+Shift+X': self._batch_pre_annotate, 'Q': self._prev_bbox, 'E': self._next_bbox,
            'S': self._on_label_search_shortcut,
            'F5': self._refresh_list,
        }
        for i in range(1, 10):
            self.shortcuts[str(i)] = lambda cid=i - 1: self._quick_select_class(cid)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and isinstance(watched, QWidget):
            if watched is self or self.isAncestorOf(watched):
                # 模态窗口和弹出菜单独占键盘，避免操作设置时触发主画布命令。
                if QApplication.activeModalWidget() or QApplication.activePopupWidget():
                    return super().eventFilter(watched, event)
                focus = QApplication.focusWidget()
                key = QKeySequence(event.keyCombination()).toString(QKeySequence.SequenceFormat.PortableText)
                if isinstance(focus, INPUT_WIDGET_TYPES) and key not in INPUT_GLOBAL_SHORTCUTS:
                    return False
                # 画布按点击位置限定 Tab 循环范围，其余控件保留常规焦点导航。
                if (focus is self._canvas and key == CANVAS_CYCLE_SHORTCUT
                        and self._canvas.cycle_annotation_selection()):
                    return True
                if self._video_path and key in (',', '.'):
                    self._step_video_frame(-1 if key == ',' else 1)
                    return True
                if key in self.shortcuts:
                    self.shortcuts[key]()
                    return True
        return super().eventFilter(watched, event)

    def config(self, menu=None):
        if menu:
            self.menuBar().clear()
            self.menuBar().addActions(menu.widget.actions())
            self._menubar_owner = menu

    def after(self, milliseconds, callback, *args):
        self._next_timer += 1
        key = self._next_timer
        timer = QTimer(self)
        timer.setSingleShot(True)
        self._timers[key] = timer
        def invoke():
            self._timers.pop(key, None)
            timer.deleteLater()
            if not self._closing:
                callback(*args)
        timer.timeout.connect(invoke)
        timer.start(milliseconds)
        return key

    def after_idle(self, callback, *args):
        return self.after(0, callback, *args)

    def after_cancel(self, key):
        timer = self._timers.pop(key, None)
        if timer:
            timer.stop()
            timer.deleteLater()

    def _poll_main_thread_queue(self):
        try:
            while True:
                callback = self._main_thread_queue.get_nowait()
                try:
                    callback()
                except Exception as error:
                    self._callback_errors.append(error)
                    self._status_bar.error(f'界面更新失败: {error}')
                    import traceback
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.after(MAIN_QUEUE_INTERVAL_MS, self._poll_main_thread_queue)

    def title(self, text):
        self.setWindowTitle(text)

    def saved_window_geometry(self):
        return f'{self.width()}x{self.height()}+{max(0, self.x())}+{max(0, self.y())}'

    def update_idletasks(self):
        """同步提交待绘制事件，不在业务操作中递归处理输入或后台结果。"""
        QApplication.sendPostedEvents(None, QEvent.Type.UpdateRequest)

    def _current_left_panel_width(self):
        return self._workspace_paned.sizes()[0]

    def _current_right_panel_width(self):
        return self._paned.sizes()[1]

    def _current_right_pane_sash_positions(self):
        sizes = self._right_paned.sizes()
        return [sizes[0], sizes[0] + sizes[1]]

    def _show_center_toast(self, text, duration_ms=1000):
        self._toast.setText(text)
        self._toast.adjustSize()
        self._toast.move((self._canvas.width() - self._toast.width()) // 2,
                          (self._canvas.height() - self._toast.height()) // 2)
        self._toast.show()
        self._toast.raise_()
        if self._toast_after_id:
            self.after_cancel(self._toast_after_id)
        self._toast_after_id = self.after(duration_ms, self._toast.hide)

    def _hide_center_toast(self):
        self._toast.hide()

    def _finish_weights_load(self, path, success, show_error):
        ApplicationController._finish_weights_load(self, path, success, show_error)
        self.model_name.setText(Path(path).name if success else '模型加载失败')
        self.model_name.setToolTip(str(path))

    def _on_close(self):
        self.close()

    def _set_theme(self, theme):
        self._config.ui_theme = theme
        self._theme_var.set(theme)
        apply_theme(QApplication.instance(), theme)
        self._config_manager.save(self._config)

    def _request_ui_switch(self):
        """完成保存和资源清理后，由启动器进入另一种界面的事件循环。"""
        if (self._pre_annotator.is_busy or self._weights_load_in_progress
                or self._video_export_running or self._image_export_running or self._directory_loading
                or self._pending_video_generation is not None or self._video_delete_running):
            self._status_bar.warning('请等待当前推理、导出或加载任务完成，再切换界面')
            return
        previous = self._config.ui_backend
        self._config.ui_backend = 'legacy'
        if self.close():
            self._requested_ui = 'legacy'
        else:
            self._config.ui_backend = previous

    def closeEvent(self, event):
        try:
            failed = self._save_all_dirty_annotations()
            if failed is not None:
                self._report_save_failure(failed)
                event.ignore()
                return
            self._save_manual_statuses()
            self._persist_current_position()
            self._save_session()
        except Exception as error:
            showerror(self, '保存失败', str(error))
            event.ignore()
            return
        if self._video_export_stop_event:
            self._video_export_stop_event.set()
        self._stop_video(clear_canvas=False)
        self._pre_annotator.cancel()
        self._scan_generation += 1
        self._closing = True
        for key in list(self._timers):
            self.after_cancel(key)
        self._image_loader.shutdown()
        QApplication.instance().removeEventFilter(self)
        event.accept()

    def mainloop(self):
        self.showMaximized()
        QApplication.instance().exec()

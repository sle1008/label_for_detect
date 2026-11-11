"""现代标注工作台主窗口，Qt 控件与业务控制器通过明确接口连接。"""

import queue
import re
import sys
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QToolButton, QSplitter, QLineEdit, QTextEdit, QPlainTextEdit,
    QComboBox, QAbstractSpinBox, QSizePolicy, QSlider,
)
from ui.qt.controller import ApplicationController
from ui.qt.state import initialize_state
from ui.qt.canvas import AnnotationCanvas
from ui.qt.panels import (ThumbnailPanel, LabelPanel, BoxListPanel, ThresholdSlider,
                           VideoControls, StatusBar, Combo, section)
from ui.qt.theme import STYLESHEET, FONT_FAMILY, FONT_SIZE, icon
from ui.qt.dialogs import showerror
from utils.paths import get_icon_path
from io_ops.pre_annotator import DEFAULT_INFERENCE_MODE, INFERENCE_MODE_ONE_TO_ONE, INFERENCE_MODE_ONE_TO_MANY_NMS

# ==================== 工作台参数设置区 ====================
DEFAULT_WINDOW_SIZE = (1600, 960)
MINIMUM_WINDOW_SIZE = (1100, 680)
DEFAULT_PANE_WIDTHS = (270, 940, 350)
MAIN_QUEUE_INTERVAL_MS = 16
INPUT_WIDGET_TYPES = (QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QAbstractSpinBox, QSlider)
INPUT_GLOBAL_SHORTCUTS = {'Ctrl+S', 'Ctrl+Shift+S', 'Ctrl+O', 'Ctrl+Shift+O', 'Ctrl+G'}
_application = None


def ensure_application():
    global _application
    _application = QApplication.instance() or QApplication(sys.argv[:1])
    _application.setStyle('Fusion')
    _application.setFont(QFont(FONT_FAMILY, FONT_SIZE))
    _application.setStyleSheet(STYLESHEET)
    return _application


class CommandBar(QFrame):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._batch_running = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 8)
        for text, graphic, action in [('撤销', 'undo', app._undo), ('重做', 'redo', app._redo),
                                      ('删除', 'trash', app._delete_selected),
                                      ('适应', 'fit', app._fit_window), ('旋转 180°', 'rotate', app._rotate_current_image),
                                      ('标签显示', 'label', app._cycle_label_mode)]:
            button = QPushButton(text)
            button.setIcon(icon(graphic))
            button.setToolTip(text)
            button.setText('')
            button.setFixedWidth(36)
            button.clicked.connect(action)
            layout.addWidget(button)
        layout.addStretch()
        self.infer = QPushButton('预标注  Ctrl+X')
        self.infer.setIcon(icon('spark'))
        self.infer.setObjectName('primary')
        self.infer.clicked.connect(app._pre_annotate_current)
        layout.addWidget(self.infer)
        self.batch = QPushButton('批量推理')
        self.batch.clicked.connect(lambda: app._stop_batch_pre_annotate() if self._batch_running else app._batch_pre_annotate())
        layout.addWidget(self.batch)

    def set_batch_running(self, running):
        self._batch_running = running
        self.batch.setText('停止推理' if running else '批量推理')
        self.infer.setEnabled(not running)


class AnnotationApp(QMainWindow, ApplicationController):
    def __init__(self, restore_session=True, config_directory=None):
        ensure_application()
        super().__init__()
        self._timers = {}
        self._next_timer = 0
        self._closing = False
        self._callback_errors = []
        initialize_state(self, config_directory)
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
        self._toast = QLabel(self._canvas)
        self._toast.setStyleSheet('background:#2b3f60;color:white;border:1px solid #648ac2;border-radius:8px;padding:10px 20px;')
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
        header = QHBoxLayout()
        brand = QLabel('ANNOTATE')
        brand.setObjectName('brand')
        header.addWidget(brand)
        badge = QLabel('视觉标注工作台')
        badge.setObjectName('badge')
        header.addWidget(badge)
        self.project_label = QLabel('未打开项目')
        self.project_label.setObjectName('muted')
        self.project_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(self.project_label, 1)
        for text, graphic, callback, primary in [
            ('打开目录', 'folder', self._open_directory, False),
            ('打开视频', 'video', self._open_video, False),
            ('加载标签', 'label', self._load_label_file, False),
            ('保存', 'save', self._save_current, True),
            ('导出数据集', 'export', self._export, False),
        ]:
            button = QPushButton(text)
            button.setIcon(icon(graphic))
            button.clicked.connect(callback)
            if primary:
                button.setObjectName('primary')
            header.addWidget(button)
        layout.addLayout(header)
        self._paned = QSplitter(Qt.Orientation.Horizontal)
        self._paned.setChildrenCollapsible(False)
        self._paned.setHandleWidth(12)
        layout.addWidget(self._paned, 1)
        left = QWidget()
        left.setMinimumWidth(220)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        filters = QFrame()
        filters.setObjectName('panel')
        filter_layout = QVBoxLayout(filters)
        filter_layout.setContentsMargins(12, 14, 12, 14)
        filter_layout.addLayout(section('筛选资源', 'FILTERS'))
        self._status_filter_combo = Combo()
        self._status_filter_combo.addItems(['全部图片', '已标注', '未标注', '不确定'])
        self._status_filter_combo.textActivated.connect(self._on_status_filter_combo_selected)
        filter_layout.addWidget(self._status_filter_combo)
        self.class_filter = QToolButton()
        self.class_filter.setText('全部标签')
        self.class_filter.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.class_filter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        filter_layout.addWidget(self.class_filter)
        left_layout.addWidget(filters)
        self._thumb_panel = ThumbnailPanel(self)
        left_layout.addWidget(self._thumb_panel, 1)
        navigation = QHBoxLayout()
        for text, callback in [('‹ 上一张', self._prev_image), ('跳转', self._jump_to_image), ('下一张 ›', self._next_image)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            navigation.addWidget(button)
        left_layout.addLayout(navigation)
        self._paned.addWidget(left)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(8)
        self._toolbar = CommandBar(self)
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
        self._paned.addWidget(center)
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
        self.model_name.setWordWrap(True)
        model_layout.addWidget(self.model_name)
        load_model = QPushButton('选择模型权重…')
        load_model.setIcon(icon('spark'))
        load_model.clicked.connect(self._load_weights)
        model_layout.addWidget(load_model)
        self.inference_combo = QComboBox()
        self.inference_combo.addItems([INFERENCE_MODE_ONE_TO_ONE, INFERENCE_MODE_ONE_TO_MANY_NMS])
        self.inference_combo.setCurrentText(DEFAULT_INFERENCE_MODE)
        self.inference_combo.textActivated.connect(self._set_pre_annotation_inference_mode)
        model_layout.addWidget(self.inference_combo)
        right_layout.addWidget(model_frame)
        self._right_paned = QSplitter(Qt.Orientation.Vertical)
        self._right_paned.setChildrenCollapsible(False)
        self._right_paned.setHandleWidth(10)
        self._label_panel = LabelPanel(self)
        self._right_paned.addWidget(self._label_panel)
        self._threshold_slider = ThresholdSlider(self)
        self._threshold_slider.setFixedHeight(102)
        self._right_paned.addWidget(self._threshold_slider)
        self._box_list_panel = BoxListPanel(self)
        self._right_paned.addWidget(self._box_list_panel)
        self._right_paned.setSizes([350, 102, 260])
        saved_sashes = self._config.right_pane_sash_positions
        if len(saved_sashes) >= 2:
            self._right_paned.setSizes([max(150, saved_sashes[0]), 102, 260])
        right_layout.addWidget(self._right_paned, 1)
        self._paned.addWidget(right)
        self._paned.setSizes([self._config.left_panel_width or DEFAULT_PANE_WIDTHS[0],
                              DEFAULT_PANE_WIDTHS[1], self._config.right_panel_width or DEFAULT_PANE_WIDTHS[2]])
        self._paned.setStretchFactor(1, 1)
        self._status_bar = StatusBar()
        layout.addWidget(self._status_bar)

    def _setup_shortcuts(self):
        self.shortcuts = {
            'Ctrl+O': self._refresh_list, 'Ctrl+Shift+O': self._open_video,
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

    def after(self, milliseconds, callback):
        self._next_timer += 1
        key = self._next_timer
        timer = QTimer(self)
        timer.setSingleShot(True)
        self._timers[key] = timer
        def invoke():
            self._timers.pop(key, None)
            timer.deleteLater()
            if not self._closing:
                callback()
        timer.timeout.connect(invoke)
        timer.start(milliseconds)
        return key

    def after_idle(self, callback):
        return self.after(0, callback)

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
        self.project_label.setText(Path(str(self._project.image_dir)).name if self._project.image_dir else '未打开项目')
        self.project_label.setToolTip(str(self._project.image_dir or ''))

    def saved_window_geometry(self):
        return f'{self.width()}x{self.height()}+{max(0, self.x())}+{max(0, self.y())}'

    def update_idletasks(self):
        """同步提交待绘制事件，不在业务操作中递归处理输入或后台结果。"""
        QApplication.sendPostedEvents(None, QEvent.Type.UpdateRequest)

    def _current_left_panel_width(self):
        return self._paned.sizes()[0]

    def _current_right_panel_width(self):
        return self._paned.sizes()[2]

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

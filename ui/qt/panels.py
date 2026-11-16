"""资源列表、类别管理、标注表格与播放控制的原生 Qt 组件。"""

from pathlib import Path
from datetime import datetime
from PySide6.QtCore import Qt, QAbstractListModel, QModelIndex, QSize, QItemSelectionModel, QTimer, QPersistentModelIndex, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QAbstractItemView, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox, QSlider, QProgressBar, QPlainTextEdit,
    QInputDialog, QSizePolicy, QToolButton, QStyle, QStyleOptionSlider, QMenu,
)
from ui.qt.bridge import VisibilityMixin, event_position, filedialog
from ui.qt.dialogs import askyesno, showerror
from ui.qt.theme import icon, theme_color, set_button_icon
from io_ops.folder_labels import save_labels_txt

# ==================== 面板参数设置区 ====================
VIDEO_SLIDER_UNITS = 1000
LOG_LINE_LIMIT = 200
BOX_COLUMNS = ('id', 'class', 'conf', 'coords')
# 统一采用逻辑像素，保持高分屏下的文字清晰与列表密度。
IMAGE_ROW_HEIGHT = 26
CLASS_ROW_HEIGHT = 26
BOX_ROW_HEIGHT = 26
REFRESH_BUTTON_SIZE = (66, 26)


def section(title, subtitle=None):
    row = QHBoxLayout()
    heading = QLabel(title)
    heading.setObjectName('sectionTitle')
    row.addWidget(heading)
    row.addStretch()
    if subtitle:
        badge = QLabel(subtitle)
        badge.setObjectName('eyebrow')
        row.addWidget(badge)
    return row


class Combo(QComboBox):
    def get(self):
        return self.currentText()

    def set(self, text):
        self.setCurrentText(text)


class FilterButton(QToolButton):
    """与类别筛选使用一致的弹出菜单外观，并保持控制器的取值接口。"""
    textActivated = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName('filter')
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setMenu(QMenu(self))

    def addItems(self, texts):
        for text in texts:
            self.menu().addAction(text).triggered.connect(lambda checked=False, value=text: self.choose(value))
        if texts:
            self.set(texts[0])

    def choose(self, text):
        self.set(text)
        self.textActivated.emit(text)

    def get(self):
        return self.text()

    def set(self, text):
        self.setText(text)
        self.setToolTip(text + '：点击展开筛选选项')


class ImageListModel(QAbstractListModel):
    """只为可见行提供文本，数万张图片不创建逐行控件。"""
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.owner.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.owner.items):
            return None
        item = self.owner.items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return f'{index.row() + 1:04d}   {item.name}'
        if role == Qt.ItemDataRole.ToolTipRole:
            return str(item.path)
        if role == Qt.ItemDataRole.SizeHintRole:
            return QSize(120, IMAGE_ROW_HEIGHT)
        if role == Qt.ItemDataRole.ForegroundRole and item.manual_annotation_status == 'uncertain':
            return QColor(theme_color('uncertain', self.owner.app._config.ui_theme))
        return None


class VideoPathButton(QPushButton):
    """路径过长时省略开头，保留文件名并在悬停时显示完整路径。"""
    def set_path(self, path):
        self._path = str(path)
        self.setToolTip(self._path)
        self._update_text()

    def _update_text(self):
        path = getattr(self, '_path', '')
        visible = self.fontMetrics().elidedText(path, Qt.TextElideMode.ElideLeft, max(30, self.width() - 70))
        self.setText('视频  ' + visible)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_text()


class ThumbnailPanel(QFrame):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName('panel')
        self.items, self.indices = [], []
        self._syncing_current = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 12)
        self.count = QLabel('0 张')
        self.count.setObjectName('muted')
        title = section('媒体资源', 'LIBRARY')
        self.refresh_button = QToolButton()
        self.refresh_button.setObjectName('compact')
        self.refresh_button.setFixedSize(*REFRESH_BUTTON_SIZE)
        self.refresh_button.setIconSize(QSize(16, 16))
        set_button_icon(self.refresh_button, 'refresh')
        self.refresh_button.setText('刷新')
        self.refresh_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.refresh_button.setToolTip('刷新图片列表（F5）：重新扫描当前目录')
        self.refresh_button.setAccessibleName('刷新图片列表')
        self.refresh_button.clicked.connect(app._refresh_list)
        title.addWidget(self.refresh_button)
        layout.addLayout(title)
        layout.addWidget(self.count)
        self.video = VideoPathButton()
        set_button_icon(self.video, 'video')
        self.video.setCheckable(True)
        self.video.setMinimumWidth(0)
        self.video.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.video.setToolTip('当前视频')
        self.video.clicked.connect(app._on_video_selected)
        self.video.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.video.customContextMenuRequested.connect(
            lambda p: app._show_video_context_menu(event_position(self.video.mapToGlobal(p))))
        self.video.hide()
        layout.addWidget(self.video)
        self.view = QListView()
        self.view.setUniformItemSizes(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.model = ImageListModel(self)
        self.view.setModel(self.model)
        self.view.selectionModel().currentChanged.connect(self.queue_image_selection)
        self.view.clicked.connect(lambda index: self.select_image(index) if app._video_path else None)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.context_menu)
        layout.addWidget(self.view, 1)

    def select_image(self, index):
        if index.isValid():
            self.select_video(False)
            self.app._on_thumb_selected(self.indices[index.row()])

    def queue_image_selection(self, current, previous):
        if self._syncing_current:
            return
        # Qt 完成 Ctrl/Shift 的选择处理后再导航，防止预览回调覆盖本次多选。
        index = QPersistentModelIndex(current)
        def select():
            if index.isValid() and self.view.currentIndex() == index and not self.app._closing:
                self.select_image(index)
        QTimer.singleShot(0, select)

    def context_menu(self, position):
        index = self.view.indexAt(position)
        if not index.isValid():
            return
        selection = self.view.selectionModel()
        if not selection.isSelected(index):
            self.view.setCurrentIndex(index)
        indices = [self.indices[i.row()] for i in selection.selectedIndexes()]
        self.app._on_image_context_menu(self.indices[index.row()],
                                       event_position(self.view.viewport().mapToGlobal(position)), indices)

    def set_images(self, items, full_indices=None, filter_hint=''):
        self.model.beginResetModel()
        self.items = list(items)
        self.indices = list(full_indices) if full_indices is not None else list(range(len(items)))
        self.model.endResetModel()
        self.count.setText(f'{filter_hint + " · " if filter_hint else ""}{len(items):,} 张图片')

    def full_indices(self):
        return list(self.indices)

    def set_current_by_full_index(self, index):
        self.select_video(False)
        if index in self.indices:
            model_index = self.model.index(self.indices.index(index))
            selection = self.view.selectionModel()
            # 同步当前位置时只跳过业务导航，保留 Qt 选择信号以重绘旧、新高亮行。
            self._syncing_current = True
            try:
                if selection.isSelected(model_index) or selection.currentIndex() == model_index:
                    selection.setCurrentIndex(model_index, QItemSelectionModel.SelectionFlag.NoUpdate)
                else:
                    self.view.setCurrentIndex(model_index)
            finally:
                self._syncing_current = False
            self.view.scrollTo(model_index)

    def remove_item(self, item):
        if item not in self.items:
            return False
        row = self.items.index(item)
        full = self.indices[row]
        self.model.beginRemoveRows(QModelIndex(), row, row)
        self.items.pop(row)
        self.indices.pop(row)
        self.indices = [i - 1 if i > full else i for i in self.indices]
        self.model.endRemoveRows()
        self.count.setText(f'{len(self.items):,} 张图片')
        return True

    def clear(self):
        self.set_images([])

    def set_interaction_enabled(self, enabled):
        self.view.setEnabled(enabled)

    def select_only_current(self):
        index = self.view.currentIndex()
        self.view.clearSelection()
        self.view.setCurrentIndex(index)

    def set_video(self, path, selected=True):
        self.video.set_path(path)
        self.video.show()
        self.select_video(selected)

    def select_video(self, selected):
        self.video.setChecked(selected)
        if selected:
            self.view.clearSelection()

    def clear_video(self):
        self.video.hide()


class LabelPanel(QFrame):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._label_manager = app._label_manager
        self._visible_labels = []
        self.setObjectName('panel')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 12)
        layout.addLayout(section('标签类别', 'CLASSES'))
        self.search = QLineEdit()
        self.search.setToolTip('搜索标签类别或备注（S）')
        self.search.setPlaceholderText('搜索类别或中文备注…')
        self.search.textChanged.connect(lambda: self.refresh())
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName('classList')
        self.list.setUniformItemSizes(True)
        self.list.currentRowChanged.connect(self.select_class)
        # 点击当前行也应用类别；刷新可能重建条目，因此回调只读取列表当前行。
        self.list.itemClicked.connect(lambda _item: self.select_class(self.list.currentRow()))
        self.list.itemDoubleClicked.connect(lambda item: self.edit_note())
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, callback in [('＋ 添加', self.add_label), ('删除', self.delete_label),
                               ('排序', self.sort_labels), ('导出', self.export_labels)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            button.setContentsMargins(0, 0, 0, 0)
            row.addWidget(button)
        layout.addLayout(row)

    def refresh(self, select_class_id=None):
        current = self._label_manager.current_class_id if select_class_id is None else select_class_id
        query = self.search.text().strip().casefold()
        self._visible_labels = [label for label in self._label_manager.labels_for_display()
                                if query in label.name.casefold() or
                                query in self.app._label_note_store.get(label.name).casefold()]
        self.list.blockSignals(True)
        self.list.clear()
        for index, label in enumerate(self._visible_labels):
            note = self.app._label_note_store.get(label.name)
            item = QListWidgetItem(f'●  {label.class_id:02d}   {label.name}' + (f'  ·  {note}' if note else ''))
            item.setSizeHint(QSize(100, CLASS_ROW_HEIGHT))
            item.setForeground(QColor(label.color))
            shortcut = f'；快捷选择：{label.class_id + 1}' if 0 <= label.class_id < 9 else ''
            item.setToolTip(f'{label.name}：双击编辑中文备注{shortcut}')
            self.list.addItem(item)
            if label.class_id == current:
                self.list.setCurrentRow(index)
        self.list.blockSignals(False)

    def select_class(self, row):
        if 0 <= row < len(self._visible_labels):
            label = self._visible_labels[row]
            self._label_manager.current_class_id = label.class_id
            self.app._on_class_selected(label.class_id)

    def highlight_class(self, class_id):
        self.refresh(class_id)

    def set_sort_by_name(self, value):
        self._label_manager.sort_by_name = value
        self.refresh()

    def focus_search(self, clear=False):
        if clear:
            self.search.clear()
        self.search.setFocus()

    def sort_labels(self):
        value = self._label_manager.toggle_display_sort()
        self.refresh()
        self.app._on_label_sort_changed(value)

    def add_label(self):
        name, accepted = QInputDialog.getText(self, '添加标签', '类别名称')
        if accepted and name.strip():
            self._label_manager.add_label(name.strip())
            self.refresh()
            self.app._refresh_label_filter_options()

    def delete_label(self):
        row = self.list.currentRow()
        if 0 <= row < len(self._visible_labels):
            label = self._visible_labels[row]
            if askyesno(self, '删除标签', f'确定删除类别“{label.name}”？'):
                self._label_manager.remove_label(label.class_id)
                self.refresh()
                self.app._refresh_label_filter_options()

    def edit_note(self):
        row = self.list.currentRow()
        if not (0 <= row < len(self._visible_labels)) or not self.app._can_edit_label_note():
            return
        label = self._visible_labels[row]
        note, accepted = QInputDialog.getText(self, '类别备注', label.name,
                                             text=self.app._label_note_store.get(label.name))
        if accepted:
            try:
                self.app._label_note_store.set(label.name, note)
            except OSError as error:
                showerror(self, '保存失败', str(error))
            self.refresh()

    def export_labels(self):
        hint = Path(self.app._default_label_export_path())
        path = filedialog.asksaveasfilename(parent=self, initialdir=str(hint.parent),
                                            initialfile=hint.name, defaultextension='.txt',
                                            filetypes=[('标签集', '*.txt')])
        if path:
            try:
                save_labels_txt(self._label_manager, Path(path))
                self.app._on_labels_file_exported(path)
            except Exception as error:
                showerror(self, '导出失败', str(error))


class BoxListPanel(QFrame):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._current_image = None
        self._video_annotations = None
        self.setObjectName('panel')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 10)
        self.count = QLabel('0')
        row = section('标注实例', 'ANNOTATIONS')
        row.insertWidget(1, self.count)
        layout.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['#', '类别', '置信度', '坐标'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setMinimumSectionSize(22)
        self.table.verticalHeader().setDefaultSectionSize(BOX_ROW_HEIGHT)
        self.table.setShowGrid(False)
        widths = app._config.box_list_column_widths
        for i, key in enumerate(BOX_COLUMNS):
            self.table.setColumnWidth(i, widths.get(key, (35, 100, 60, 160)[i]))
        self.table.itemSelectionChanged.connect(self.select_boxes)
        self.table.horizontalHeader().sectionResized.connect(lambda *args: app._on_box_list_columns_changed(self.get_column_widths()))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        layout.addWidget(self.table)

    def get_column_widths(self):
        return {key: self.table.columnWidth(i) for i, key in enumerate(BOX_COLUMNS)}

    def set_image(self, item):
        self._current_image = item
        self._video_annotations = None
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.refresh()

    def set_video_annotations(self, annotations):
        self._current_image = None
        self._video_annotations = annotations
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.refresh()

    def refresh(self):
        annotations = (self._video_annotations if self._video_annotations is not None else
                       self._current_image.annotations if self._current_image else [])
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(len(annotations))
        self.count.setText(str(len(annotations)))
        for row, box in enumerate(annotations):
            values = [str(row + 1), box.class_name, f'{box.confidence:.2f}',
                      f'{box.x1:.0f}, {box.y1:.0f} → {box.x2:.0f}, {box.y2:.0f}']
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 1:
                    item.setForeground(QColor(self.app._label_manager.get_color(box.class_id)))
                self.table.setItem(row, col, item)
            if box.is_selected and self._video_annotations is None:
                for col in range(4):
                    self.table.item(row, col).setSelected(True)
        self.table.blockSignals(False)

    def select_boxes(self):
        if self._current_image:
            selected = {i.row() for i in self.table.selectedIndexes()}
            for row, box in enumerate(self._current_image.annotations):
                box.is_selected = row in selected
            self.app._on_bbox_selected_from_list()

    def context_menu(self, position):
        if self._video_annotations is not None:
            return
        from ui.qt.bridge import Menu
        menu = Menu(self)
        menu.add_command(label='删除选中标注', command=self.app._delete_selected)
        menu.add_command(label='修改类别', command=lambda: self.app._label_panel.focus_search(True))
        point = self.table.viewport().mapToGlobal(position)
        menu.tk_popup(point.x(), point.y())


class ThresholdSlider(QFrame):
    def __init__(self, app):
        super().__init__()
        self.setObjectName('panel')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        heading = QLabel('置信度阈值')
        heading.setObjectName('sectionTitle')
        layout.addWidget(heading)
        self.value = QLabel(f'{app._threshold:.2f}')
        self.value.setObjectName('badge')
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimumWidth(60)
        self.slider.setToolTip('预标注置信度阈值')
        self.slider.setRange(1, 99)
        self.slider.setValue(round(app._threshold * 100))
        self.slider.valueChanged.connect(lambda value: (self.value.setText(f'{value/100:.2f}'), app._on_threshold_changed(value / 100)))
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value)

    def set(self, value):
        self.slider.setValue(round(value * 100))
        self.value.setText(f'{self.slider.value() / 100:.2f}')


class TimelineSlider(QSlider):
    """点击轨道直接定位；拖动期间保持标准 sliderPressed/Released 信号。"""
    def _set_pointer_position(self, event):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                             QStyle.SubControl.SC_SliderGroove, self)
        handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                             QStyle.SubControl.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        position = round(event.position().x() - groove.x() - handle.width() / 2)
        self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                                                    position, span, option.upsideDown))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self.setSliderDown(True)
            self._set_pointer_position(event)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self._set_pointer_position(event)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self._set_pointer_position(event)
            self.setSliderDown(False)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class VideoControls(VisibilityMixin, QFrame):
    def __init__(self, app):
        super().__init__()
        self.setObjectName('panel')
        self.app = app
        self._duration, self._seeking, self._export_busy = 0, False, False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        timeline = QHBoxLayout()
        controls = QHBoxLayout()
        layout.addLayout(timeline)
        layout.addLayout(controls)
        self._play_button = QPushButton('播放')
        self._play_button.setObjectName('primary')
        self._play_button.clicked.connect(app._toggle_video_play)
        controls.addWidget(self._play_button)
        for delta in (-1, 1):
            button = QPushButton()
            set_button_icon(button, 'prev' if delta < 0 else 'next')
            button.setFixedWidth(36)
            button.setToolTip('上一帧 (,)' if delta < 0 else '下一帧 (.)')
            button.setAccessibleName('上一帧' if delta < 0 else '下一帧')
            button.clicked.connect(lambda checked=False, d=delta: app._step_video_frame(d))
            controls.addWidget(button)
        self.time = QLabel('00:00 / 00:00')
        timeline.addWidget(self.time)
        self.slider = TimelineSlider(Qt.Orientation.Horizontal)
        self.slider.sliderPressed.connect(self.begin_seek)
        self.slider.sliderReleased.connect(self.end_seek)
        self.slider.valueChanged.connect(self.seek_by_keyboard)
        timeline.addWidget(self.slider, 1)
        controls.addStretch()
        self.speed = QComboBox()
        self.speed.addItems(['1x', '1.25x', '1.5x', '2x'])
        self.speed.textActivated.connect(lambda text: app._set_video_speed(float(text[:-1])))
        controls.addWidget(self.speed)
        self.export = QPushButton('导出视频')
        set_button_icon(self.export, 'export')
        self.export.clicked.connect(app._export_video)
        controls.addWidget(self.export)

    @staticmethod
    def _format_time(seconds):
        seconds = max(0, int(seconds))
        return f'{seconds//60:02d}:{seconds%60:02d}'

    def begin_seek(self):
        self._seeking = True
        self.app._begin_video_seek()

    def end_seek(self):
        self._seeking = False
        self.app._seek_video(self.slider.value() / VIDEO_SLIDER_UNITS)

    def seek_by_keyboard(self):
        if not self._seeking:
            self.begin_seek()
            self.end_seek()

    def set_duration(self, duration):
        self._duration = duration
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(1, round(duration * VIDEO_SLIDER_UNITS)))
        self.slider.setSingleStep(max(1, round(VIDEO_SLIDER_UNITS / max(1, self.app._video_fps))))
        self.slider.setPageStep(VIDEO_SLIDER_UNITS)
        self.slider.blockSignals(False)
        self.set_position(0)

    def set_position(self, position):
        if not self._seeking:
            self.slider.blockSignals(True)
            self.slider.setValue(round(position * VIDEO_SLIDER_UNITS))
            self.slider.blockSignals(False)
            self.time.setText(f'{self._format_time(position)} / {self._format_time(self._duration)}')

    def set_playing(self, playing):
        self._play_button.setText('暂停' if playing else '播放')

    def set_enabled(self, enabled):
        self.setEnabled(enabled)
        self.export.setEnabled(enabled and not self._export_busy)

    def set_export_busy(self, busy):
        self._export_busy = busy
        self.export.setEnabled(not busy)

    def set_speed(self, speed):
        self.speed.setCurrentText(f'{speed:g}x')


class StatusBar(QFrame):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        row = QHBoxLayout()
        indicator = QLabel('●  READY')
        indicator.setObjectName('ready')
        row.addWidget(indicator)
        self.label = QLabel('就绪 · 打开目录或视频开始标注')
        self.label.setObjectName('muted')
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setMinimumWidth(0)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.label, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(160)
        self.progress.setTextVisible(False)
        self.progress.hide()
        row.addWidget(self.progress)
        toggle = QPushButton('活动日志')
        toggle.clicked.connect(lambda: self.log_view.setVisible(not self.log_view.isVisible()))
        row.addWidget(toggle)
        layout.addLayout(row)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(LOG_LINE_LIMIT)
        self.log_view.setFixedHeight(130)
        self.log_view.hide()
        layout.addWidget(self.log_view)

    def log(self, message, level='info'):
        self.label.setText(message)
        self.label.setToolTip(message)
        self.log_view.appendPlainText(f'[{datetime.now():%H:%M:%S}] {message}')

    info = log
    success = log
    warning = log
    error = log
    set_info = log

    def set_overlay(self, message):
        self.label.setText(message)
        self.label.setToolTip(message)

    def show_progress(self, value=0):
        self.progress.show()
        self.update_progress(value)

    def update_progress(self, value):
        self.progress.setValue(round(value))

    def hide_progress(self):
        self.progress.hide()

    def update_status(self, **kwargs):
        parts = []
        if kwargs.get('total_imgs'):
            parts.append(f'图片 {kwargs.get("current_img", 0)} / {kwargs["total_imgs"]}{kwargs.get("total_suffix", "")}')
        if kwargs.get('img_width'):
            parts.append(f'{kwargs["img_width"]} × {kwargs.get("img_height", 0)}')
        if 'ann_count' in kwargs:
            parts.append(f'标注 {kwargs["ann_count"]}')
        if kwargs.get('zoom'):
            parts.append(f'{kwargs["zoom"]:.0%}')
        parts.extend(str(kwargs[key]) for key in ('category', 'mode') if kwargs.get(key))
        text = '  ·  '.join(parts)
        self.set_overlay(text)

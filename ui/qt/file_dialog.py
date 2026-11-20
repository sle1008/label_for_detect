"""支持地址输入的主题文件选择器，统一目录、打开及保存窗口的导航。"""

import os
from pathlib import Path
from PySide6.QtCore import QDir, QEvent, QFile, QItemSelectionModel, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QFileDialog, QFileSystemModel, QHBoxLayout, QLabel, QLineEdit, QListView,
    QMessageBox, QPushButton, QSizePolicy, QStyledItemDelegate, QToolButton, QMenu, QWidget,
)

# ==================== 文件选择参数设置区 ====================
# Qt 控件继承应用主题，地址输入与文件名输入分别处理。
FILE_DIALOG_OPTIONS = QFileDialog.Option.DontUseNativeDialog
ADDRESS_SHORTCUTS = ('Ctrl+L', 'Alt+D')
ADDRESS_HINT = '粘贴目录地址后按回车跳转（Ctrl+L / Alt+D）'
ADDRESS_HISTORY_LIMIT = 5
ADDRESS_MAX_WIDTH = 640
ADDRESS_MIN_CHARACTERS = 24
FILE_DIALOG_DEFAULT_WIDTH = 740
FILE_DIALOG_DEFAULT_HEIGHT = 488
FILE_DIALOG_SIDEBAR_MIN_WIDTH = 112
SIDEBAR_SYSTEM_LOCATION_NAMES = {
    QStandardPaths.StandardLocation.DesktopLocation: '桌面',
    QStandardPaths.StandardLocation.DocumentsLocation: '文档',
    QStandardPaths.StandardLocation.DownloadLocation: '下载',
    QStandardPaths.StandardLocation.PicturesLocation: '图片',
    QStandardPaths.StandardLocation.MusicLocation: '音乐',
    QStandardPaths.StandardLocation.MoviesLocation: '视频',
}
FILE_VIEW_BUTTONS = ('newFolderButton', 'listModeButton', 'detailModeButton')
FILE_TYPE_COLUMN = 2


class AddressHistoryDelegate(QStyledItemDelegate):
    """历史行按视口宽度省略开头，完整路径仍供选择和工具提示使用。"""

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        # 忽略文本的完整宽度，避免列表项超出视口后被直接裁切。
        size.setWidth(0)
        return size


class FileEntryDelegate(QStyledItemDelegate):
    """文件类型以中文展示，保留原文件模型的导航、排序与编辑逻辑。"""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        model = index.model()
        if index.column() == FILE_TYPE_COLUMN and isinstance(model, QFileSystemModel):
            # Windows 外壳提供的类型名称不经过 Qt 翻译，显示时按目录和扩展名处理。
            suffix = Path(model.fileName(index)).suffix.lstrip('.').upper()
            option.text = '文件夹' if model.isDir(index) else (f'{suffix} 文件' if suffix else '文件')


class SidebarLocationDelegate(QStyledItemDelegate):
    """本地化系统快捷位置的名称，保留 Qt 侧栏模型中的实际路径。"""

    def __init__(self, sidebar, sidebar_urls):
        super().__init__(sidebar)
        self._sidebar_urls = sidebar_urls
        self._display_names = {}
        for location, name in SIDEBAR_SYSTEM_LOCATION_NAMES.items():
            path = QStandardPaths.writableLocation(location)
            if path:
                # 系统位置指向同一路径时，优先采用排在前面的常用名称。
                self._display_names.setdefault(os.path.normcase(os.path.normpath(path)), name)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        # 从当前侧栏取 URL，拖入、移除或重排快捷位置后仍按实际路径匹配。
        urls = self._sidebar_urls()
        row = index.row()
        if 0 <= row < len(urls) and urls[row].isLocalFile():
            path = urls[row].toLocalFile()
            option.text = self._display_names.get(os.path.normcase(os.path.normpath(path)), option.text)


class PathFileDialog(QFileDialog):
    """保留 Qt 文件浏览、筛选及覆盖确认，补充可粘贴的地址栏。"""

    _recent_directories = []

    def __init__(self, parent=None, caption='', directory='', name_filter=''):
        super().__init__(parent, caption, directory, name_filter,
                         options=FILE_DIALOG_OPTIONS)
        address = QWidget(self)
        row = QHBoxLayout(address)
        row.setContentsMargins(0, 0, 0, 0)
        self.address_combo = QComboBox(address)
        self.address_combo.setObjectName('directoryHistory')
        self.address_combo.setEditable(True)
        # 路径长度不参与窗口尺寸推导，长地址在有限空间中滚动显示尾部。
        self.address_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.address_combo.setMinimumContentsLength(ADDRESS_MIN_CHARACTERS)
        self.address_combo.setMaximumWidth(ADDRESS_MAX_WIDTH)
        self.address_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.address_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.address_combo.setMaxVisibleItems(ADDRESS_HISTORY_LIMIT)
        self.address_edit = self.address_combo.lineEdit()
        self.address_edit.setObjectName('directoryAddress')
        self.address_edit.setAccessibleName('目录地址')
        self.address_edit.setPlaceholderText(ADDRESS_HINT)
        self.address_edit.setToolTip(ADDRESS_HINT)
        self.address_edit.installEventFilter(self)
        self.address_combo.installEventFilter(self)
        self._history = self._initial_history(parent)
        self._first_address_click = False
        self._history_completer = self.address_combo.completer()
        self._history_completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self._history_completer.setMaxVisibleItems(ADDRESS_HISTORY_LIMIT)
        for view in (self.address_combo.view(), self._history_completer.popup()):
            view.setItemDelegate(AddressHistoryDelegate(view))
            view.setTextElideMode(Qt.TextElideMode.ElideLeft)
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            view.setMaximumWidth(ADDRESS_MAX_WIDTH)
        self.address_combo.activated.connect(self._choose_history)
        self._history_completer.activated[str].connect(self._choose_completion)
        row.addWidget(self.address_combo, 1)
        go = QPushButton('转到', address)
        go.setAutoDefault(False)
        go.setToolTip(ADDRESS_HINT)
        go.clicked.connect(self.navigate_to_address)
        row.addWidget(go)
        # 仅替换目录显示控件，Qt 原有侧栏、历史导航与文件列表仍负责浏览。
        locations = self.findChild(QComboBox, 'lookInCombo')
        self.layout().replaceWidget(locations, address)
        locations.hide()
        label = self.findChild(QLabel, 'lookInLabel')
        label.setText('地址：')
        label.setBuddy(self.address_edit)
        # 地址行独立跨满窗口，不再与底部的“文件类型”等标签共用列宽。
        navigation = self.findChild(QHBoxLayout, 'hboxLayout')
        self.layout().removeWidget(label)
        navigation_item = self.layout().takeAt(next(
            index for index in range(self.layout().count())
            if self.layout().itemAt(index).layout() is navigation))
        navigation = navigation_item.layout()
        navigation.insertWidget(0, label)
        navigation.setStretchFactor(address, 1)
        address.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.layout().addLayout(navigation, 0, 0, 1, self.layout().columnCount())
        for name in FILE_VIEW_BUTTONS:
            self.findChild(QToolButton, name).hide()
        self._ensure_desktop_shortcut()
        sidebar = self.findChild(QListView, 'sidebar')
        sidebar.setItemDelegate(SidebarLocationDelegate(sidebar, self.sidebarUrls))
        # 侧栏为图标和完整中文名称保留空间，避免翻译后被自动压得过窄。
        sidebar.setMinimumWidth(FILE_DIALOG_SIDEBAR_MIN_WIDTH)
        # 行内重命名沿用列表行高，只缩小编辑框内边距，保证字形完整。
        self.setStyleSheet('QAbstractItemView QLineEdit { padding: 0px 3px; min-height: 0px; }')
        for name in ('listView', 'treeView'):
            view = self.findChild(QWidget, name)
            view.setItemDelegate(FileEntryDelegate(view))
            view.customContextMenuRequested.disconnect()
            view.customContextMenuRequested.connect(
                lambda point, target=view: self._show_file_context_menu(target, point))
        self.resize(max(FILE_DIALOG_DEFAULT_WIDTH, self.minimumSizeHint().width()),
                    max(FILE_DIALOG_DEFAULT_HEIGHT, self.minimumSizeHint().height()))
        self.directoryEntered.connect(self._sync_address)
        self._sync_address()
        self._address_shortcuts = []
        for sequence in ADDRESS_SHORTCUTS:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(self.focus_address)
            self._address_shortcuts.append(shortcut)

    def _ensure_desktop_shortcut(self):
        """保留现有快捷位置，补入系统桌面入口并避免重复。"""
        # 使用系统路径，兼容重定向或 OneDrive 桌面；路径不可用时沿用原侧栏。
        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        if not desktop or not QDir(desktop).exists():
            return
        urls = self.sidebarUrls()
        desktop_key = os.path.normcase(os.path.normpath(desktop))
        if any(url.isLocalFile() and
               os.path.normcase(os.path.normpath(url.toLocalFile())) == desktop_key for url in urls):
            return
        # 桌面置于靠前位置，其他系统入口与用户快捷位置保持原有顺序。
        urls.insert(1, QUrl.fromLocalFile(desktop))
        self.setSidebarUrls(urls)

    def _file_context_menu(self, view, point):
        """文件项保留原生重命名和删除行为，空白处也可新建目录或切换视图。"""
        index = view.indexAt(point)
        if index.isValid():
            index = index.siblingAtColumn(0)
            if view.selectionModel().isSelected(index):
                view.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
            else:
                view.setCurrentIndex(index)
        menu = QMenu(self)
        writable = not self.testOption(QFileDialog.Option.ReadOnly) and not view.model().isReadOnly()
        if index.isValid():
            # 与 Qt 原菜单一致：父目录可写才允许改名或删除，写保护文件仍保留确认流程。
            permissions = QFile.Permission(index.parent().data(QFileSystemModel.Roles.FilePermissions) or 0)
            can_modify = writable and bool(permissions & QFile.Permission.WriteUser)
            for name in ('qt_rename_action', 'qt_delete_action'):
                action = self.findChild(QAction, name)
                action.setEnabled(can_modify)
                menu.addAction(action)
            menu.addSeparator()
        create = self.findChild(QAction, 'qt_new_folder_action')
        create.setEnabled(writable and self.findChild(QToolButton, 'newFolderButton').isEnabled())
        menu.addAction(create)
        menu.addSeparator()
        group = QActionGroup(menu)
        group.setExclusive(True)
        for text, mode in (('列表视图', QFileDialog.ViewMode.List), ('详细视图', QFileDialog.ViewMode.Detail)):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(self.viewMode() == mode)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, target=mode: self.setViewMode(target))
        menu.addSeparator()
        menu.addAction(self.findChild(QAction, 'qt_show_hidden_action'))
        return menu

    def _show_file_context_menu(self, view, point):
        menu = self._file_context_menu(view, point)
        try:
            menu.exec(view.viewport().mapToGlobal(point))
        finally:
            menu.deleteLater()

    def _sync_address(self, directory=None):
        current = QDir.toNativeSeparators(self.directory().absolutePath())
        self._history = self._unique_directories([current, *self._history])
        PathFileDialog._recent_directories = self._history[:]
        self.setHistory(list(reversed(self._history)))
        blocked = self.address_combo.blockSignals(True)
        self.address_combo.clear()
        self.address_combo.addItems(self._history)
        for index, path in enumerate(self._history):
            self.address_combo.setItemData(index, path, Qt.ItemDataRole.ToolTipRole)
        self.address_combo.setCurrentIndex(-1)
        self.address_edit.setText(current)
        self.address_edit.setCursorPosition(len(current))
        self.address_edit.setToolTip(f'{current}\n{ADDRESS_HINT}')
        self.address_combo.blockSignals(blocked)

    @staticmethod
    def _unique_directories(paths):
        directories, seen = [], set()
        for text in paths:
            if not text:
                continue
            path = os.path.abspath(os.path.normpath(str(text)))
            key = os.path.normcase(path)
            try:
                available = Path(path).is_dir()
            except OSError:
                available = False
            if key not in seen and available:
                seen.add(key)
                directories.append(QDir.toNativeSeparators(path))
            if len(directories) == ADDRESS_HISTORY_LIMIT:
                break
        return directories

    def _initial_history(self, parent):
        # 沿用 Qt 目录历史和应用最近目录，同一会话的后续弹窗共享最近访问顺序。
        recent = []
        while parent is not None:
            config = getattr(parent, '_config', None)
            if config is not None:
                recent = getattr(config, 'recent_dirs', [])
                break
            parent = parent.parentWidget()
        return self._unique_directories([*PathFileDialog._recent_directories, *recent,
                                         *reversed(self.history())])

    def _choose_history(self, index):
        self._choose_completion(self.address_combo.itemText(index))

    def _choose_completion(self, text):
        self.address_edit.setText(text)
        self._history_completer.popup().hide()
        self.navigate_to_address()
        self.focus_address()

    def _show_address_history(self):
        if self._address_has_focus() and self.isVisible():
            self.address_edit.selectAll()
            self._history_completer.setCompletionPrefix('')
            # 补全弹窗不按完整路径扩宽，历史项只省略开头，数据仍保留完整地址。
            self._history_completer.popup().setFixedWidth(self.address_combo.width())
            self._history_completer.complete()

    def focus_address(self):
        self.address_combo.setFocus()
        self.address_edit.selectAll()

    def _address_has_focus(self):
        return self.address_edit.hasFocus() or self.address_combo.hasFocus()

    def eventFilter(self, watched, event):
        if watched in (self.address_edit, self.address_combo):
            if event.type() == QEvent.Type.FocusIn and event.reason() == Qt.FocusReason.MouseFocusReason:
                self._first_address_click = True
            elif event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                # 首次进入输入框时保留整条地址选择；再次点击可正常移动插入光标。
                self._first_address_click = self._first_address_click or not self._address_has_focus()
            elif event.type() == QEvent.Type.MouseButtonRelease and self._first_address_click:
                self._first_address_click = False
                QTimer.singleShot(0, self._show_address_history)
        # 地址栏回车只导航，避免同时触发对话框的默认打开或保存按钮。
        if (watched is self.address_edit and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            self.navigate_to_address()
            return True
        return super().eventFilter(watched, event)

    def navigate_to_address(self):
        text = self.address_edit.text().strip()
        if not text:
            return
        # 兼容资源管理器“复制文件地址”的引号、环境变量及本地 file URL。
        if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
            text = text[1:-1]
        if text.lower().startswith('file:'):
            text = QUrl(text).toLocalFile()
        directory, filename = None, None
        try:
            path = Path(os.path.expandvars(text)).expanduser() if text else None
            if path is not None:
                if not path.is_absolute():
                    path = Path(self.directory().absolutePath()) / path
                if path.is_dir():
                    directory = path
                elif (self.fileMode() != QFileDialog.FileMode.Directory and path.parent.is_dir()
                      and (path.is_file() or self.acceptMode() == QFileDialog.AcceptMode.AcceptSave)):
                    directory, filename = path.parent, path.name
        except (OSError, ValueError, RuntimeError):
            # 无效路径、不可访问目录及无法展开的用户目录统一留在当前窗口处理。
            pass
        if directory is None:
            QMessageBox.warning(self, '无法跳转', '目录不存在或路径不可用，请检查地址。')
            self.focus_address()
            return
        self.setDirectory(str(directory))
        if filename is not None:
            self.selectFile(filename)
            # 目录模型异步建立索引，文件名输入不依赖列表是否已经加载完毕。
            self.findChild(QLineEdit, 'fileNameEdit').setText(filename)
        self._sync_address()

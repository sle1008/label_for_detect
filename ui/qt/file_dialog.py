"""支持地址输入的主题文件选择器，统一目录、打开及保存窗口的导航。"""

import os
from pathlib import Path
from PySide6.QtCore import QDir, QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QWidget,
)

# ==================== 文件选择参数设置区 ====================
# Qt 控件继承应用主题，地址输入与文件名输入分别处理。
FILE_DIALOG_OPTIONS = QFileDialog.Option.DontUseNativeDialog
ADDRESS_SHORTCUTS = ('Ctrl+L', 'Alt+D')
ADDRESS_HINT = '粘贴目录地址后按回车跳转（Ctrl+L / Alt+D）'
ADDRESS_HISTORY_LIMIT = 5


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
        label.setText('地址:')
        label.setBuddy(self.address_edit)
        self.directoryEntered.connect(self._sync_address)
        self._sync_address()
        self._address_shortcuts = []
        for sequence in ADDRESS_SHORTCUTS:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(self.focus_address)
            self._address_shortcuts.append(shortcut)

    def _sync_address(self, directory=None):
        current = QDir.toNativeSeparators(self.directory().absolutePath())
        self._history = self._unique_directories([current, *self._history])
        PathFileDialog._recent_directories = self._history[:]
        self.setHistory(list(reversed(self._history)))
        blocked = self.address_combo.blockSignals(True)
        self.address_combo.clear()
        self.address_combo.addItems(self._history)
        self.address_combo.setCurrentIndex(-1)
        self.address_edit.setText(current)
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

"""文件选择器的真实 Qt 路径输入、模态结果与主题回归。"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QPalette
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (
    QApplication, QDialogButtonBox, QFileDialog, QLabel, QLineEdit, QListView, QMessageBox, QPushButton,
    QStyle, QStyleOptionFrame, QStyleOptionViewItem, QToolButton, QTreeView,
)
from ui.qt.app import ensure_application
from ui.qt.bridge import filedialog
from ui.qt.file_dialog import (
    ADDRESS_HISTORY_LIMIT, ADDRESS_MAX_WIDTH, FILE_DIALOG_DEFAULT_HEIGHT, FILE_DIALOG_DEFAULT_WIDTH,
    PathFileDialog,
)
from ui.qt.theme import apply_theme, theme_color

# ==================== 文件选择回归参数设置区 ====================
MODES = ('directory', 'open', 'save')
FILE_TYPES = [('标签文件', '*.txt *.yaml *.yml'), ('所有文件', '*.*')]
MODEL_WAIT_SECONDS = 3


class QtFileDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = ensure_application()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.initial = self.root / '起始目录'
        self.target = self.root / '目标 images'
        self.initial.mkdir()
        self.target.mkdir()
        self.label_file = self.target / 'classes.txt'
        self.label_file.write_text('0: animal\n', encoding='utf-8')
        self.windows = []
        self.previous_theme = self.runtime.property('annotationTheme') or 'dark'
        self.previous_history = PathFileDialog._recent_directories[:]
        PathFileDialog._recent_directories = []

    def tearDown(self):
        for window in self.windows:
            window.close()
            window.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.runtime.processEvents()
        apply_theme(self.runtime, self.previous_theme)
        PathFileDialog._recent_directories = self.previous_history
        self.temporary.cleanup()

    def make_dialog(self, mode='open', directory=None):
        dialog = PathFileDialog(directory=str(directory or self.initial),
                                name_filter=filedialog._filter(FILE_TYPES))
        self.windows.append(dialog)
        if mode == 'directory':
            dialog.setFileMode(QFileDialog.FileMode.Directory)
            dialog.setOption(QFileDialog.Option.ShowDirsOnly)
        else:
            dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        if mode == 'save':
            dialog.setFileMode(QFileDialog.FileMode.AnyFile)
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
            dialog.selectFile('preview.mp4')
        dialog.move(20000, 20000)
        dialog.show()
        self.runtime.processEvents()
        return dialog

    def paste_address(self, dialog, text):
        dialog.focus_address()
        self.runtime.clipboard().setText(text)
        self.runtime.processEvents()
        QTest.keyClick(dialog.address_edit, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(dialog.address_edit.text(), text)
        QTest.keyClick(dialog.address_edit, Qt.Key.Key_Return)
        self.runtime.processEvents()

    def wait_for(self, predicate):
        deadline = time.monotonic() + MODEL_WAIT_SECONDS
        while time.monotonic() < deadline:
            self.runtime.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.fail('文件选择器模型或编辑状态等待超时')

    def test_default_desktop_shortcut_navigates_without_accepting_in_all_modes(self):
        desktop = self.root / '系统桌面 Desktop'
        desktop.mkdir()
        desktop_url = QUrl.fromLocalFile(str(desktop))
        for theme in ('dark', 'light'):
            apply_theme(self.runtime, theme)
            for mode in MODES:
                with self.subTest(theme=theme, mode=mode):
                    with patch('ui.qt.file_dialog.QStandardPaths.writableLocation', return_value=str(desktop)):
                        dialog = self.make_dialog(mode)
                    original_urls = [url for url in dialog.sidebarUrls() if url != desktop_url]
                    try:
                        self.assertEqual(dialog.sidebarUrls().count(desktop_url), 1)
                        self.assertEqual(Path(dialog.directory().absolutePath()), self.initial)
                        sidebar = dialog.findChild(QListView, 'sidebar')
                        index = sidebar.model().index(dialog.sidebarUrls().index(desktop_url), 0)
                        accepted = QSignalSpy(dialog.accepted)
                        QTest.mouseClick(sidebar.viewport(), Qt.MouseButton.LeftButton,
                                         pos=sidebar.visualRect(index).center())
                        self.wait_for(lambda: Path(dialog.directory().absolutePath()) == desktop)
                        self.assertEqual(Path(dialog.address_edit.text()), desktop)
                        self.assertTrue(dialog.isVisible())
                        self.assertEqual(accepted.count(), 0)
                        if mode == 'save':
                            self.assertEqual(Path(dialog.selectedFiles()[0]).name, 'preview.mp4')
                    finally:
                        dialog.setSidebarUrls(original_urls)
                        dialog.hide()

    def test_desktop_shortcut_preserves_bookmarks_and_skips_duplicates_or_missing_paths(self):
        dialog = self.make_dialog('directory')
        original_urls = dialog.sidebarUrls()
        bookmarks = [QUrl('file:'), QUrl.fromLocalFile(str(self.initial)),
                     QUrl.fromLocalFile(str(self.target))]
        try:
            dialog.setSidebarUrls(bookmarks)
            with patch('ui.qt.file_dialog.QStandardPaths.writableLocation', return_value=str(self.target)):
                dialog._ensure_desktop_shortcut()
            self.assertEqual(dialog.sidebarUrls(), bookmarks)
            with patch('ui.qt.file_dialog.QStandardPaths.writableLocation', return_value=str(self.root)):
                dialog._ensure_desktop_shortcut()
                dialog._ensure_desktop_shortcut()
            self.assertEqual(dialog.sidebarUrls(), [bookmarks[0], QUrl.fromLocalFile(str(self.root)),
                                                    *bookmarks[1:]])
            expected = dialog.sidebarUrls()
            for missing in ('', str(self.root / '不存在的桌面')):
                with self.subTest(desktop=missing), patch(
                        'ui.qt.file_dialog.QStandardPaths.writableLocation', return_value=missing):
                    dialog._ensure_desktop_shortcut()
                    self.assertEqual(dialog.sidebarUrls(), expected)
        finally:
            dialog.setSidebarUrls(original_urls)

    def test_address_row_uses_available_space_and_blank_menu_controls_views(self):
        for theme in ('dark', 'light'):
            apply_theme(self.runtime, theme)
            for mode in MODES:
                with self.subTest(theme=theme, mode=mode):
                    dialog = self.make_dialog(mode)
                    label = dialog.findChild(QLabel, 'lookInLabel')
                    label_right = label.mapTo(dialog, QPoint(label.width(), 0)).x()
                    address_left = dialog.address_combo.mapTo(dialog, QPoint()).x()
                    self.assertLessEqual(address_left - label_right, 10)
                    self.assertGreater(dialog.address_combo.width(), 440)
                    self.assertGreaterEqual(dialog.width(), FILE_DIALOG_DEFAULT_WIDTH)
                    self.assertGreaterEqual(dialog.height(), FILE_DIALOG_DEFAULT_HEIGHT)
                    for name in ('newFolderButton', 'listModeButton', 'detailModeButton'):
                        self.assertTrue(dialog.findChild(QToolButton, name).isHidden())
                    view = dialog.findChild(QListView, 'listView')
                    menu = dialog._file_context_menu(view, QPoint(10, view.viewport().height() - 10))
                    try:
                        actions = {action.text(): action for action in menu.actions()}
                        self.assertIn('列表视图', actions)
                        self.assertIn('详细视图', actions)
                        for text, view_mode in (('详细视图', QFileDialog.ViewMode.Detail),
                                                ('列表视图', QFileDialog.ViewMode.List)):
                            actions[text].trigger()
                            self.runtime.processEvents()
                            self.assertEqual(dialog.viewMode(), view_mode)
                            self.assertTrue(actions[text].isChecked())
                    finally:
                        menu.deleteLater()
                    dialog.hide()

    def test_context_creation_and_rename_keep_full_editor_text_in_both_views(self):
        for theme in ('dark', 'light'):
            apply_theme(self.runtime, theme)
            for mode, view_type, name in ((QFileDialog.ViewMode.List, QListView, 'listView'),
                                          (QFileDialog.ViewMode.Detail, QTreeView, 'treeView')):
                with self.subTest(theme=theme, view=mode):
                    dialog = self.make_dialog('directory')
                    dialog.setViewMode(mode)
                    view = dialog.findChild(view_type, name)
                    menu = dialog._file_context_menu(view, QPoint(10, view.viewport().height() - 10))
                    create = next(action for action in menu.actions()
                                  if action.objectName() == 'qt_new_folder_action')
                    create.trigger()
                    self.wait_for(lambda: any(editor.isVisible() for editor in view.findChildren(QLineEdit)))
                    editor = next(editor for editor in view.findChildren(QLineEdit) if editor.isVisible())
                    self.assertIn('新建文件夹', editor.text())
                    option = QStyleOptionFrame()
                    editor.initStyleOption(option)
                    content = editor.style().subElementRect(QStyle.SubElement.SE_LineEditContents, option, editor)
                    self.assertGreaterEqual(content.height(), editor.fontMetrics().height())
                    created_name = f'新目录_{theme}_{name}'
                    created = self.initial / created_name
                    editor.setText(created_name)
                    QTest.keyClick(editor, Qt.Key.Key_Return)
                    self.wait_for(created.is_dir)
                    self.wait_for(lambda: not any(edit.isVisible() for edit in view.findChildren(QLineEdit)))
                    menu.deleteLater()
                    self.wait_for(lambda: view.model().index(str(created)).isValid())
                    index = view.model().index(str(created))
                    view.scrollTo(index)
                    view.grab()
                    # 文件系统模型会异步排序，等待时重新取索引以获取最新行号。
                    self.wait_for(lambda: view.visualRect(view.model().index(str(created))).isValid())
                    index = view.model().index(str(created))
                    point = view.visualRect(index).intersected(view.viewport().rect()).center()
                    rename_menu = dialog._file_context_menu(view, point)
                    self.assertEqual(Path(view.model().filePath(view.currentIndex())), created)
                    rename = next(action for action in rename_menu.actions()
                                  if action.objectName() == 'qt_rename_action')
                    rename.trigger()
                    self.wait_for(lambda: any(edit.isVisible() for edit in view.findChildren(QLineEdit)))
                    editor = next(edit for edit in view.findChildren(QLineEdit) if edit.isVisible())
                    renamed = created.with_name(created_name + '_已重命名')
                    editor.setText(renamed.name)
                    QTest.keyClick(editor, Qt.Key.Key_Return)
                    self.wait_for(renamed.is_dir)
                    self.assertFalse(created.exists())
                    rename_menu.deleteLater()
                    dialog.hide()

    def test_file_menus_and_delete_confirmations_are_chinese_and_keep_cancel_behavior(self):
        for theme in ('dark', 'light'):
            with self.subTest(theme=theme):
                apply_theme(self.runtime, theme)
                folder = self.initial / f'待删除目录_{theme}'
                folder.mkdir()
                dialog = self.make_dialog('directory')
                dialog.setViewMode(QFileDialog.ViewMode.Detail)
                view = dialog.findChild(QTreeView, 'treeView')
                self.wait_for(lambda: view.visualRect(view.model().index(str(folder))).isValid())
                index = view.model().index(str(folder))
                option = QStyleOptionViewItem()
                view.itemDelegate().initStyleOption(option, index.siblingAtColumn(2))
                self.assertEqual(option.text, '文件夹')
                self.assertIn('文件类型', dialog.findChild(QLabel, 'fileTypeLabel').text())
                buttons = dialog.findChild(QDialogButtonBox, 'buttonBox')
                self.assertIn('取消', buttons.button(QDialogButtonBox.StandardButton.Cancel).text())
                edit_menu = dialog.address_edit.createStandardContextMenu()
                self.assertTrue(any('粘贴' in action.text() for action in edit_menu.actions()))
                self.assertFalse(any('Paste' in action.text() for action in edit_menu.actions()))
                edit_menu.deleteLater()
                dialog.setOption(QFileDialog.Option.ReadOnly)
                restricted = dialog._file_context_menu(view, view.visualRect(index).center())
                for action in restricted.actions():
                    if action.objectName() in ('qt_rename_action', 'qt_delete_action', 'qt_new_folder_action'):
                        self.assertFalse(action.isEnabled())
                restricted.deleteLater()
                dialog.setOption(QFileDialog.Option.ReadOnly, False)
                menu = dialog._file_context_menu(view, view.visualRect(index).center())
                actions = {action.objectName(): action for action in menu.actions()}
                self.assertIn('重命名', actions['qt_rename_action'].text())
                self.assertIn('删除', actions['qt_delete_action'].text())
                self.assertIn('新建', actions['qt_new_folder_action'].text())
                self.assertIn('隐藏', actions['qt_show_hidden_action'].text())
                confirmations = []

                def answer_delete(answer):
                    box = self.runtime.activeModalWidget()
                    if isinstance(box, QMessageBox):
                        confirmations.append((box.windowTitle(), box.text(),
                                              [button.text() for button in box.buttons()]))
                        box.button(answer).click()

                for answer in (QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes):
                    QTimer.singleShot(0, lambda response=answer: answer_delete(response))
                    actions['qt_delete_action'].trigger()
                    self.runtime.processEvents()
                    self.assertEqual(folder.exists(), answer == QMessageBox.StandardButton.No)
                self.assertEqual(len(confirmations), 2)
                for title, message, captions in confirmations:
                    self.assertIn('删除', title)
                    self.assertIn('删除', message)
                    self.assertFalse('Are you sure' in message)
                    self.assertTrue(any('是' in caption for caption in captions))
                    self.assertTrue(any('否' in caption for caption in captions))
                menu.deleteLater()
                dialog.hide()

    def test_pasted_directory_navigates_without_accepting_in_all_modes(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                dialog = self.make_dialog(mode)
                accepted = QSignalSpy(dialog.accepted)
                self.assertEqual(Path(dialog.address_edit.text()), self.initial)
                self.paste_address(dialog, f'"{self.target}"')
                self.assertEqual(Path(dialog.directory().absolutePath()), self.target)
                self.assertEqual(Path(dialog.address_edit.text()), self.target)
                self.assertTrue(dialog.isVisible())
                self.assertEqual(accepted.count(), 0)
                if mode == 'save':
                    self.assertEqual(Path(dialog.selectedFiles()[0]).name, 'preview.mp4')
                dialog.hide()

    def test_paths_support_file_urls_relative_directories_and_environment(self):
        dialog = self.make_dialog()
        paths = [self.target.as_uri(), os.path.relpath(self.target, self.initial),
                 str(self.label_file)]
        with patch.dict(os.environ, {'LABEL_PICKER_TARGET': str(self.target)}):
            paths.append('%LABEL_PICKER_TARGET%' if os.name == 'nt' else '$LABEL_PICKER_TARGET')
            for text in paths:
                with self.subTest(text=text):
                    dialog.setDirectory(str(self.initial))
                    dialog.address_edit.setText(text)
                    QTest.keyClick(dialog.address_edit, Qt.Key.Key_Return)
                    self.runtime.processEvents()
                    self.assertEqual(Path(dialog.directory().absolutePath()), self.target)
                    if text == str(self.label_file):
                        deadline = time.monotonic() + MODEL_WAIT_SECONDS
                        while not dialog.selectedFiles() and time.monotonic() < deadline:
                            QTest.qWait(5)
                        self.assertEqual([Path(path) for path in dialog.selectedFiles()], [self.label_file])

    def test_toolbar_navigation_updates_address_after_pasting(self):
        dialog = self.make_dialog('directory')
        self.paste_address(dialog, str(self.target))
        QTest.mouseClick(dialog.findChild(QToolButton, 'toParentButton'), Qt.MouseButton.LeftButton)
        self.runtime.processEvents()
        self.assertEqual(Path(dialog.directory().absolutePath()), self.root)
        self.assertEqual(Path(dialog.address_edit.text()), self.root)

    def test_go_button_and_save_overwrite_confirmation_remain_separate(self):
        dialog = self.make_dialog('save')
        accepted = QSignalSpy(dialog.accepted)
        dialog.address_edit.setText(str(self.label_file))
        go = next(button for button in dialog.findChildren(QPushButton) if button.text() == '转到')
        QTest.mouseClick(go, Qt.MouseButton.LeftButton)
        self.runtime.processEvents()
        self.assertEqual([Path(path) for path in dialog.selectedFiles()], [self.label_file])
        self.assertEqual(accepted.count(), 0)
        confirmations = []

        def decline_overwrite():
            box = self.runtime.activeModalWidget()
            if isinstance(box, QMessageBox):
                confirmations.append(box.text())
                box.done(QMessageBox.StandardButton.No)

        QTimer.singleShot(0, decline_overwrite)
        dialog.accept()
        self.assertEqual(len(confirmations), 1)
        self.assertEqual(accepted.count(), 0)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(self.label_file.read_text(encoding='utf-8'), '0: animal\n')

    def test_invalid_path_keeps_current_directory_and_dialog_open(self):
        dialog = self.make_dialog('directory')
        history = [dialog.address_combo.itemText(index) for index in range(dialog.address_combo.count())]
        for text in (str(self.root / '不存在'), str(self.label_file), '~unknown_picker_user/folder'):
            with self.subTest(text=text), patch('ui.qt.file_dialog.QMessageBox.warning') as warning:
                dialog.address_edit.setText(text)
                QTest.keyClick(dialog.address_edit, Qt.Key.Key_Return)
                warning.assert_called_once()
                self.assertEqual(Path(dialog.directory().absolutePath()), self.initial)
                self.assertTrue(dialog.isVisible())
                self.assertEqual([dialog.address_combo.itemText(index)
                                  for index in range(dialog.address_combo.count())], history)

    def test_first_mouse_click_selects_address_and_shows_history_without_blocking_paste(self):
        dialog = self.make_dialog('directory')
        dialog.activateWindow()
        filename = dialog.findChild(QLineEdit, 'fileNameEdit')
        filename.setFocus()
        self.runtime.processEvents()
        QTest.mouseClick(dialog.address_edit, Qt.MouseButton.LeftButton)
        self.runtime.processEvents()
        self.assertEqual(dialog.address_edit.selectedText(), dialog.address_edit.text())
        self.assertTrue(dialog._history_completer.popup().isVisible())
        self.assertLessEqual(dialog._history_completer.completionCount(), ADDRESS_HISTORY_LIMIT)
        # 弹出历史时编辑框仍接收输入，粘贴地址不必先选择或关闭历史。
        self.runtime.clipboard().setText(str(self.target))
        QTest.keyClick(dialog.address_edit, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(dialog.address_edit, Qt.Key.Key_Return)
        self.runtime.processEvents()
        self.assertEqual(Path(dialog.directory().absolutePath()), self.target)
        dialog._history_completer.popup().hide()
        dialog.address_edit.setFocus()
        QTest.mouseClick(dialog.address_edit, Qt.MouseButton.LeftButton)
        self.runtime.processEvents()
        self.assertEqual(dialog.address_edit.selectedText(), '')

    def test_history_is_recent_deduplicated_limited_and_shared_between_dialogs(self):
        dialog = self.make_dialog('directory')
        directories = [self.root / f'history_{index}' for index in range(ADDRESS_HISTORY_LIMIT + 2)]
        for path in directories:
            path.mkdir()
            dialog.address_edit.setText(str(path))
            dialog.navigate_to_address()
        dialog.address_edit.setText(str(directories[-2]))
        dialog.navigate_to_address()
        expected = [directories[-2], directories[-1], *reversed(directories[:-2])][:ADDRESS_HISTORY_LIMIT]
        self.assertEqual([Path(dialog.address_combo.itemText(index))
                          for index in range(dialog.address_combo.count())], expected)
        dialog.hide()
        reopened = self.make_dialog('directory')
        self.assertEqual([Path(reopened.address_combo.itemText(index))
                          for index in range(reopened.address_combo.count())],
                         [self.initial, *expected][:ADDRESS_HISTORY_LIMIT])

    def test_long_address_keeps_dialog_compact_and_full_path_editable(self):
        long_directory = self.root / ('parent_' + 'a' * 48) / ('nested_' + 'b' * 48) / '尾部目标目录'
        long_directory.mkdir(parents=True)
        for theme in ('dark', 'light'):
            apply_theme(self.runtime, theme)
            for mode in MODES:
                with self.subTest(theme=theme, mode=mode):
                    short = self.make_dialog(mode)
                    short_width = short.width()
                    short.hide()
                    dialog = self.make_dialog(mode, long_directory)
                    self.assertLessEqual(dialog.width(), short_width)
                    self.assertLessEqual(dialog.address_combo.width(), ADDRESS_MAX_WIDTH)
                    self.assertEqual(Path(dialog.address_edit.text()), long_directory)
                    self.assertEqual(dialog.address_edit.cursorPosition(), len(dialog.address_edit.text()))
                    dialog.address_edit.grab()
                    # 输入框左边对应中间字符，说明只滚动隐藏开头，没有截断路径数据。
                    self.assertGreater(dialog.address_edit.cursorPositionAt(
                        QPoint(4, dialog.address_edit.height() // 2)), 0)
                    self.paste_address(dialog, str(self.initial))
                    self.paste_address(dialog, str(long_directory))
                    dialog.resize(1200, dialog.height())
                    self.runtime.processEvents()
                    self.assertLessEqual(dialog.address_combo.width(), ADDRESS_MAX_WIDTH)
                    self.assertEqual(Path(dialog.directory().absolutePath()), long_directory)
                    dialog.hide()

    def test_long_history_popups_stay_within_address_width_and_navigate_full_path(self):
        long_directory = self.root / ('history_' + 'a' * 48) / ('nested_' + 'b' * 48) / '尾部历史目录'
        long_directory.mkdir(parents=True)
        for theme in ('dark', 'light'):
            apply_theme(self.runtime, theme)
            for popup_kind in ('arrow', 'first_click'):
                with self.subTest(theme=theme, popup_kind=popup_kind):
                    PathFileDialog._recent_directories = [str(long_directory)]
                    dialog = self.make_dialog('directory')
                    accepted = QSignalSpy(dialog.accepted)
                    if popup_kind == 'arrow':
                        dialog.address_combo.showPopup()
                        view = dialog.address_combo.view()
                    else:
                        dialog.focus_address()
                        dialog._show_address_history()
                        view = dialog._history_completer.popup()
                    self.runtime.processEvents()
                    self.assertLessEqual(view.window().width(), dialog.address_combo.width() + 2)
                    index = next(view.model().index(row, 0) for row in range(view.model().rowCount())
                                 if Path(view.model().index(row, 0).data()) == long_directory)
                    self.assertEqual(Path(index.data(Qt.ItemDataRole.ToolTipRole)), long_directory)
                    self.assertEqual(view.textElideMode(), Qt.TextElideMode.ElideLeft)
                    QTest.mouseMove(view.viewport(), view.visualRect(index).center())
                    QTest.qWait(QApplication.doubleClickInterval() + 10)
                    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                                     pos=view.visualRect(index).center())
                    self.runtime.processEvents()
                    self.assertEqual(Path(dialog.directory().absolutePath()), long_directory)
                    self.assertEqual(Path(dialog.address_edit.text()), long_directory)
                    self.assertEqual(accepted.count(), 0)
                    dialog.hide()

    def test_mouse_history_selection_navigates_without_accepting_dialog(self):
        for popup_kind in ('arrow', 'first_click'):
            with self.subTest(popup_kind=popup_kind):
                dialog = self.make_dialog('directory')
                dialog.address_edit.setText(str(self.target))
                dialog.navigate_to_address()
                accepted = QSignalSpy(dialog.accepted)
                if popup_kind == 'arrow':
                    dialog.address_combo.showPopup()
                    view = dialog.address_combo.view()
                else:
                    dialog.focus_address()
                    dialog._show_address_history()
                    view = dialog._history_completer.popup()
                self.runtime.processEvents()
                index = view.model().index(1, 0)
                QTest.mouseMove(view.viewport(), view.visualRect(index).center())
                QTest.qWait(QApplication.doubleClickInterval() + 10)
                QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                                 pos=view.visualRect(index).center())
                self.runtime.processEvents()
                self.assertEqual(Path(dialog.directory().absolutePath()), self.initial)
                self.assertTrue(dialog.isVisible())
                self.assertEqual(accepted.count(), 0)
                dialog.hide()

    def test_address_shortcuts_and_navigation_follow_both_themes(self):
        for theme in ('dark', 'light'):
            with self.subTest(theme=theme):
                apply_theme(self.runtime, theme)
                dialog = self.make_dialog()
                dialog.activateWindow()
                for key, modifier in ((Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier),
                                      (Qt.Key.Key_D, Qt.KeyboardModifier.AltModifier)):
                    filename = dialog.findChild(QLineEdit, 'fileNameEdit')
                    filename.setFocus()
                    QTest.keyClick(filename, key, modifier)
                    self.runtime.processEvents()
                    self.assertIn(dialog.focusWidget(), (dialog.address_edit, dialog.address_combo))
                    self.assertEqual(dialog.address_edit.selectedText(), dialog.address_edit.text())
                self.paste_address(dialog, str(self.target))
                self.assertEqual(dialog.palette().color(QPalette.ColorRole.Window).name(),
                                 theme_color('background', theme))
                dialog.hide()

    def test_adapters_preserve_filters_default_suffix_and_cancel_result(self):
        errors = []

        def operate():
            dialog = self.runtime.activeModalWidget()
            try:
                self.assertIsInstance(dialog, PathFileDialog)
                self.assertEqual(Path(dialog.directory().absolutePath()), self.initial)
                if dialog.fileMode() == QFileDialog.FileMode.Directory:
                    dialog.setDirectory(str(self.target))
                else:
                    self.assertEqual(dialog.nameFilters(), filedialog._filter(FILE_TYPES).split(';;'))
                    dialog.setDirectory(str(self.target))
                    dialog.selectFile('preview' if dialog.acceptMode() == QFileDialog.AcceptMode.AcceptSave
                                      else self.label_file.name)
                dialog.accept()
            except BaseException as error:
                errors.append(error)
                dialog.reject()

        for mode, method in (('directory', filedialog.askdirectory),
                             ('open', filedialog.askopenfilename),
                             ('save', filedialog.asksaveasfilename)):
            with self.subTest(mode=mode):
                QTimer.singleShot(0, operate)
                result = method(initialdir=str(self.initial), filetypes=FILE_TYPES,
                                initialfile='preview', defaultextension='.mp4')
                self.assertEqual(errors, [])
                expected = self.target if mode == 'directory' else (
                    self.target / 'preview.mp4' if mode == 'save' else self.label_file)
                self.assertEqual(Path(result), expected)
                QTimer.singleShot(0, lambda: self.runtime.activeModalWidget().reject())
                self.assertEqual(method(initialdir=str(self.initial)), '')
                QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == '__main__':
    unittest.main()

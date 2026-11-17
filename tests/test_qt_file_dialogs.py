"""文件选择器的真实 Qt 路径输入、模态结果与主题回归。"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit, QMessageBox, QPushButton, QToolButton
from ui.qt.app import ensure_application
from ui.qt.bridge import filedialog
from ui.qt.file_dialog import ADDRESS_HISTORY_LIMIT, PathFileDialog
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

    def make_dialog(self, mode='open'):
        dialog = PathFileDialog(directory=str(self.initial),
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
        QTest.keyClick(dialog.address_edit, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(dialog.address_edit.text(), text)
        QTest.keyClick(dialog.address_edit, Qt.Key.Key_Return)
        self.runtime.processEvents()

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

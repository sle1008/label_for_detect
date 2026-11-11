"""Qt 界面真实事件回归，所有图片、视频和配置均在临时目录内创建。"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
import cv2
import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

# ==================== Qt 回归参数设置区 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
WAIT_SECONDS = 8
VIDEO_FRAME_COUNT = 12
VIDEO_FPS = 20

from core.annotation import BBox
from core.project import ImageFilter
from io_ops.annotation_status import preferred_annotation_txt_path
from ui.qt.app import AnnotationApp, ensure_application
from ui.qt.bridge import Menu
from ui.qt import dialogs


class QtWorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = ensure_application()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.images = self.root / 'images'
        self.images.mkdir()
        self.config = self.root / 'config'
        self.config.mkdir()
        self.app = AnnotationApp(restore_session=False, config_directory=self.config)
        self.app.move(20000, 20000)
        self.app.show()
        self.runtime.processEvents()
        self.addCleanup(self.cleanup_app)
        self.paths = [self.images / '01.jpg', self.images / '02.jpg']
        for index, path in enumerate(self.paths):
            Image.new('RGB', (320, 200), (50 + index * 50, 75, 100)).save(path)
        (self.images / 'classes.txt').write_text('0: deer\n1: person\n', encoding='utf-8')

    def wait(self, predicate):
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            self.runtime.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail('Qt 后台流程超时')

    def cleanup_app(self):
        player = self.app._video_player
        self.app.close()
        if player:
            player._thread.join(WAIT_SECONDS)
        self.runtime.processEvents()
        self.assertTrue(self.app._closing)
        self.assertEqual(self.app._callback_errors, [])
        self.temporary.cleanup()

    def load_images(self):
        self.app._load_directory(str(self.images))
        self.wait(lambda: self.app._canvas._current_image is not None)

    def make_video(self):
        path = self.root / 'sample.avi'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), VIDEO_FPS, (64, 48))
        self.assertTrue(writer.isOpened())
        for index in range(VIDEO_FRAME_COUNT):
            writer.write(np.full((48, 64, 3), index * 15, dtype=np.uint8))
        writer.release()
        return path

    def open_video(self):
        path = self.make_video()
        self.app._load_video_path(str(path))
        self.wait(lambda: self.app._canvas._video_mode)
        return path

    def canvas_point(self, x, y):
        cx, cy = self.app._canvas.image_to_canvas(x, y)
        return QPoint(round(cx), round(cy))

    def drag(self, start, end, button=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
        canvas = self.app._canvas
        QTest.mousePress(canvas, button, modifiers, start)
        QTest.mouseMove(canvas, end, 5)
        QTest.mouseRelease(canvas, button, modifiers, end)
        self.runtime.processEvents()

    def test_directory_auto_labels_and_large_virtual_list(self):
        self.load_images()
        self.assertEqual(self.app._label_manager.count(), 2)
        self.assertEqual(self.app._thumb_panel.model.rowCount(), 2)
        self.assertEqual(self.app._project.current_index, 0)
        self.assertEqual(self.app._label_panel.list.count(), 2)

    def test_missing_label_set_clears_old_classes(self):
        self.load_images()
        (self.images / 'classes.txt').unlink()
        self.app._load_directory(str(self.images))
        self.wait(lambda: not self.app._directory_loading)
        self.assertEqual(self.app._label_manager.count(), 0)

    def test_mouse_draw_move_resize_and_undo_redo(self):
        self.load_images()
        canvas = self.app._canvas
        self.drag(self.canvas_point(30, 30), self.canvas_point(100, 100))
        item = self.app._project.current_image
        self.assertEqual(len(item.annotations), 1)
        self.app._undo()
        self.assertEqual(len(item.annotations), 0)
        self.app._redo()
        box = item.annotations[0]
        box.is_selected = True
        self.drag(self.canvas_point(65, 65), self.canvas_point(85, 75))
        self.assertAlmostEqual(box.x1, 50, delta=2)
        self.app._undo()
        self.assertAlmostEqual(box.x1, 30, delta=2)
        self.drag(self.canvas_point(100, 100), self.canvas_point(130, 120))
        self.assertAlmostEqual(box.x2, 130, delta=2)

    def test_box_navigation_buttons_keep_selection_and_video_read_only(self):
        self.load_images()
        item = self.app._project.current_image
        item.annotations = [BBox(10, 10, 35, 35, 0, 'deer'), BBox(60, 40, 95, 80, 1, 'person')]
        self.app._box_list_panel.refresh()
        buttons = {b.text(): b for b in self.app._toolbar.findChildren(QPushButton)}
        buttons['后框'].click()
        self.assertEqual(item.get_selected_annotations(), [item.annotations[0]])
        buttons['后框'].click()
        self.assertEqual(item.get_selected_annotations(), [item.annotations[1]])
        buttons['前框'].click()
        self.assertEqual(item.get_selected_annotations(), [item.annotations[0]])
        self.assertEqual({i.row() for i in self.app._box_list_panel.table.selectedIndexes()}, {0})
        # 视频预览期间前后框操作不能改变隐藏图片的选择或标注数据。
        self.open_video()
        buttons['后框'].click()
        buttons['前框'].click()
        self.assertEqual(item.get_selected_annotations(), [item.annotations[0]])

    def test_open_directory_shortcut_matches_menu_and_refresh_keeps_directory(self):
        with patch.object(self.app, '_open_directory') as open_directory:
            self.app._setup_shortcuts()
            QTest.keyClick(self.app._canvas, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)
            open_directory.assert_called_once()
        self.load_images()
        with patch.object(self.app, '_refresh_list') as refresh:
            self.app._setup_shortcuts()
            QTest.keyClick(self.app._canvas, Qt.Key.Key_F5)
            refresh.assert_called_once()

    def test_rotation_mouse_mapping_and_image_shortcuts(self):
        self.load_images()
        canvas = self.app._canvas
        canvas.setFocus()
        QTest.keyClick(canvas, Qt.Key.Key_R)
        self.assertTrue(canvas._rotation_180)
        self.drag(self.canvas_point(30, 30), self.canvas_point(100, 100))
        box = self.app._project.current_image.annotations[0]
        self.assertAlmostEqual(box.x1, 30, delta=2)
        self.assertAlmostEqual(box.x2, 100, delta=2)
        box.is_selected = True
        self.drag(self.canvas_point(100, 100), self.canvas_point(130, 120))
        self.assertAlmostEqual(box.x1, 30, delta=2)
        self.assertAlmostEqual(box.x2, 130, delta=2)
        self.assertAlmostEqual(box.y2, 120, delta=2)
        original = self.app._project.current_image
        QTest.keyClick(canvas, Qt.Key.Key_Period)
        self.assertIs(self.app._project.current_image, original)
        QTest.keyClick(canvas, Qt.Key.Key_D)
        self.wait(lambda: self.app._canvas._current_image is not original)
        self.assertTrue(preferred_annotation_txt_path(self.paths[0]).is_file())

    def test_filtering_and_background_status_preserve_original_mapping(self):
        self.load_images()
        self.app._set_image_category(0, 'annotated')
        self.app._apply_image_filter(ImageFilter.ANNOTATED)
        self.assertEqual(self.app._thumb_panel.full_indices(), [0])
        self.app._apply_image_filter(ImageFilter.ALL)
        self.assertEqual(self.app._thumb_panel.full_indices(), [0, 1])

    def test_label_search_sort_and_change_selected_box(self):
        self.load_images()
        item = self.app._project.current_image
        item.annotations = [BBox(10, 10, 50, 50, 0, 'deer')]
        item.annotations[0].is_selected = True
        self.app._label_panel.search.setText('person')
        self.assertEqual(self.app._label_panel.list.count(), 1)
        self.app._label_panel.select_class(0)
        self.assertEqual(item.annotations[0].class_name, 'person')
        self.app._undo()
        self.assertEqual(item.annotations[0].class_name, 'deer')

    def test_video_click_step_seek_speed_and_original_preview(self):
        self.open_video()
        canvas = self.app._canvas
        point = QPoint(canvas.width() // 2, canvas.height() // 2)
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=point)
        self.assertTrue(self.app._video_player.playing)
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=point)
        self.assertFalse(self.app._video_player.playing)
        QTest.keyClick(canvas, Qt.Key.Key_Period)
        self.wait(lambda: self.app._video_frame_index == 1)
        self.app._set_video_speed(1.5)
        self.assertEqual(self.app._video_controls.speed.currentText(), '1.5x')
        self.app._begin_video_seek()
        self.app._seek_video(0.3)
        self.wait(lambda: self.app._video_frame_index == 6)
        self.app._play_listed_video(False)
        self.assertFalse(self.app._video_show_annotations)

    def test_video_inference_cache_and_annotated_export(self):
        self.load_images()
        image = self.app._project.current_image
        image.annotations = [BBox(10, 10, 40, 40, 0, 'deer')]
        image.annotations[0].is_selected = True
        self.open_video()
        self.assertEqual(self.app._box_list_panel.table.rowCount(), 0)
        self.app._on_class_selected(1)
        self.assertEqual(image.annotations[0].class_id, 0)
        self.app._pre_annotator._model = Mock(return_value=[])
        output = self.root / 'annotated.mp4'
        with patch.object(self.app._pre_annotator, 'predict_frame', return_value=[BBox(5, 5, 40, 40, 0, 'deer', .9)]) as infer, \
                patch('ui.qt.controller.filedialog.asksaveasfilename', return_value=str(output)):
            self.app._export_video()
            self.wait(lambda: output.is_file() and not self.app._video_export_running)
        self.assertEqual(infer.call_count, VIDEO_FRAME_COUNT)
        self.assertEqual(len(self.app._video_item.annotations), VIDEO_FRAME_COUNT)
        self.app._play_listed_video(False)
        self.assertEqual(self.app._canvas._video_annotations, [])
        self.app._play_listed_video(True)
        self.wait(lambda: bool(self.app._canvas._video_annotations))
        self.assertEqual(self.app._box_list_panel.table.rowCount(), 1)
        self.assertEqual(self.app._box_list_panel.table.item(0, 1).text(), 'deer')

    def test_video_row_remove_and_return_to_images_preserve_files(self):
        self.load_images()
        original = self.app._project.current_image
        path = self.open_video()
        self.app._remove_video_from_list()
        self.wait(lambda: self.app._canvas._current_image is original)
        self.assertTrue(path.exists())
        self.assertEqual(self.app._project.total_images, 2)

    def test_native_dialogs_and_menus_have_complete_commands(self):
        menu = Menu(self.app)
        menu.add_command(label='命令', command=lambda: None)
        menu.add_separator()
        self.assertEqual(len(menu.widget.actions()), 2)
        menu.delete(0, 'end')
        self.assertEqual(len(menu.widget.actions()), 0)
        self.load_images()
        # 对话框真实构造并绘制后自动关闭，避免测试等待手动交互。
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, lambda: QApplication.activeModalWidget().accept())
        dialog = dialogs.LabelLoadDialog(self.app)
        self.assertIsNone(dialog.result)
        QTimer.singleShot(50, lambda: QApplication.activeModalWidget().accept())
        dialogs.StatisticsDialog(self.app, self.app._project, self.app._label_manager)

    def test_closing_flushes_dirty_annotations_and_persists_layout(self):
        self.load_images()
        item = self.app._project.current_image
        item.annotations = [BBox(10, 10, 40, 40, 0, 'deer')]
        item.mark_dirty()
        self.app.close()
        self.assertTrue(preferred_annotation_txt_path(item.path).exists())
        self.assertTrue((self.config / 'config.json').exists())

    def test_save_failure_keeps_window_open_and_preserves_dirty_data(self):
        self.load_images()
        item = self.app._project.current_image
        item.annotations = [BBox(10, 10, 50, 50, 0, 'deer')]
        item.mark_dirty()
        with patch('ui.qt.controller.write_yolo_annotations_atomic', side_effect=OSError('test failure')), \
                patch('ui.qt.controller.showerror') as error:
            self.app.close()
        self.assertFalse(self.app._closing)
        self.assertTrue(item.is_dirty)
        self.assertTrue(self.app.isVisible())
        error.assert_called_once()

    def test_geometry_edits_after_save_are_saved_on_navigation(self):
        self.load_images()
        self.drag(self.canvas_point(30, 30), self.canvas_point(100, 100))
        item = self.app._project.current_image
        self.app._save_current_annotations()
        self.assertFalse(item.is_dirty)
        item.annotations[0].is_selected = True
        self.drag(self.canvas_point(65, 65), self.canvas_point(85, 75))
        self.assertTrue(item.is_dirty)
        self.app._next_image()
        self.wait(lambda: self.app._project.current_index == 1)
        text = preferred_annotation_txt_path(item.path).read_text(encoding='utf-8')
        values = text.split()
        self.assertEqual(values[0], '0')
        self.assertAlmostEqual(float(values[1]), 85 / 320, delta=0.005)

    def test_list_keyboard_navigation_and_multi_selection_are_preserved(self):
        from PySide6.QtCore import QItemSelectionModel
        self.load_images()
        panel = self.app._thumb_panel
        panel.view.setFocus()
        QTest.keyClick(panel.view, Qt.Key.Key_Down)
        self.wait(lambda: self.app._canvas._current_image is self.app._project.image_list[1])
        selection = panel.view.selectionModel()
        for row in range(2):
            selection.select(panel.model.index(row), QItemSelectionModel.SelectionFlag.Select)
        panel.set_current_by_full_index(1)
        self.assertEqual(len(selection.selectedIndexes()), 2)

    def test_real_ctrl_click_toggles_rows_and_shift_selects_range(self):
        for name in ('03.jpg', '04.jpg'):
            Image.new('RGB', (320, 200)).save(self.images / name)
        self.load_images()
        panel = self.app._thumb_panel
        def click(row, modifier=Qt.KeyboardModifier.NoModifier):
            point = panel.view.visualRect(panel.model.index(row)).center()
            QTest.mouseClick(panel.view.viewport(), Qt.MouseButton.LeftButton, modifier, point)
            self.runtime.processEvents()
            self.wait(lambda: self.app._project.current_index == row)
            return sorted(index.row() for index in panel.view.selectionModel().selectedIndexes())
        self.assertEqual(click(0), [0])
        self.assertEqual(click(2, Qt.KeyboardModifier.ControlModifier), [0, 2])
        self.assertEqual(click(0, Qt.KeyboardModifier.ControlModifier), [2])
        self.assertEqual(click(3, Qt.KeyboardModifier.ShiftModifier), [0, 1, 2, 3])

    def test_label_cache_and_category_menus_include_background(self):
        from core.project import LABEL_FILTER_BACKGROUND
        for name in ('03.jpg', '04.jpg'):
            Image.new('RGB', (320, 200)).save(self.images / name)
        for name, content in [('01.jpg', '0 .5 .5 .2 .2\n'), ('02.jpg', '1 .5 .5 .3 .3\n'), ('03.jpg', '')]:
            target = preferred_annotation_txt_path(self.images / name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
        self.load_images()
        self.wait(lambda: self.app._label_cache_ready_generation == self.app._scan_generation)
        action = next(a for a in self.app.class_filter.menu().actions() if a.text().startswith('[1]'))
        action.trigger()
        self.assertEqual(self.app._thumb_panel.full_indices(), [1])
        self.app._apply_label_filter(LABEL_FILTER_BACKGROUND)
        self.assertEqual(self.app._thumb_panel.full_indices(), [2])
        self.app._apply_label_filter(None)
        self.app._status_filter_combo.choose('未标注')
        self.assertEqual(self.app._thumb_panel.full_indices(), [3])
        ready = self.app._label_cache_ready_generation
        self.app._finish_label_cache_preload(ready - 1)
        self.assertEqual(self.app._label_cache_ready_generation, ready)
        with patch.object(self.app._status_bar, 'set_overlay') as update:
            self.app._update_label_cache_progress(ready - 1, 50)
            update.assert_not_called()

    def test_preload_follows_visible_images_and_decode_cache(self):
        for i in range(3, 9):
            Image.new('RGB', (320, 200)).save(self.images / f'{i:02d}.jpg')
        self.load_images()
        from ui.qt.controller import PRELOAD_FORWARD, PRELOAD_BACKWARD
        self.app._project.goto_image(4)
        self.app._show_current_image_async()
        self.wait(lambda: self.app._canvas._current_image is self.app._project.image_list[4])
        self.app._preload_window_async(4)
        expected = self.app._project.image_list[max(0, 4 - PRELOAD_BACKWARD):5 + PRELOAD_FORWARD]
        self.wait(lambda: all(item.is_loaded for item in expected))
        for item in expected:
            self.assertIsNotNone(self.app._image_loader.get_cached(item.path))

    def test_deleting_images_and_refresh_after_external_changes(self):
        Image.new('RGB', (320, 200)).save(self.images / '03.jpg')
        self.load_images()
        with patch('ui.qt.controller.askyesno', return_value=True):
            self.app._delete_image(0)
        self.wait(lambda: self.app._thumb_panel.model.rowCount() == 2 and self.app._canvas._current_image.path.name == '02.jpg')
        self.assertFalse(self.paths[0].exists())
        self.assertEqual(self.app._thumb_panel.full_indices(), [0, 1])
        (self.images / '03.jpg').unlink()
        Image.new('RGB', (320, 200)).save(self.images / '04.jpg')
        QTest.mouseClick(self.app._thumb_panel.refresh_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: not self.app._directory_loading)
        self.assertEqual([item.name for item in self.app._thumb_panel.items], ['02.jpg', '04.jpg'])
        self.assertEqual(self.app._label_manager.count(), 2)
        (self.images / '04.jpg').unlink()
        QTest.keyClick(self.app._canvas, Qt.Key.Key_F5)
        self.wait(lambda: not self.app._directory_loading)
        self.assertEqual(self.app._thumb_panel.model.rowCount(), 1)
        with patch('ui.qt.controller.askyesno', return_value=True):
            self.app._delete_image(0)
        self.assertEqual(self.app._thumb_panel.model.rowCount(), 0)
        self.assertIsNone(self.app._canvas._current_image)

    def test_batch_prediction_uses_current_filter_and_saves_each_result(self):
        target = preferred_annotation_txt_path(self.paths[0])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('0 .5 .5 .2 .2\n', encoding='utf-8')
        self.load_images()
        self.app._apply_image_filter(ImageFilter.UNANNOTATED)
        self.assertEqual(self.app._thumb_panel.full_indices(), [1])
        self.app._pre_annotator._model = Mock()
        self.app._pre_annotator._model_path = str(self.root / 'model.pt')
        with patch.object(self.app._pre_annotator, 'predict', return_value=[BBox(10, 10, 80, 80, 1, 'person', .8)]) as predict, \
                patch('ui.qt.controller.askyesno', return_value=True):
            QTest.mouseClick(self.app._toolbar.batch, Qt.MouseButton.LeftButton)
            self.wait(lambda: not self.app._toolbar._batch_running)
        self.assertEqual(predict.call_args.args[0], self.paths[1])
        self.assertEqual(predict.call_count, 1)
        self.assertTrue(preferred_annotation_txt_path(self.paths[1]).read_text(encoding='utf-8').startswith('1 '))
        self.assertTrue(target.read_text(encoding='utf-8').startswith('0 '))

    def test_restore_prompt_restores_image_and_detects_labels_again(self):
        self.app._config.last_directory = str(self.images)
        self.app._config.last_image_path = str(self.paths[1])
        self.app._config.last_image_index = 1
        self.app._config.last_weights_file = ''
        with patch('ui.qt.controller.RestoreDirectoryDialog', return_value=SimpleNamespace(result='restore')) as prompt:
            self.app._restore_session()
        self.wait(lambda: self.app._canvas._current_image is not None and self.app._canvas._current_image.path == self.paths[1])
        prompt.assert_called_once_with(self.app, str(self.images))
        self.assertEqual(self.app._label_manager.count(), 2)

    def test_auto_detection_one_line_set_excludes_coordinate_txt(self):
        (self.images / 'classes.txt').unlink()
        (self.images / 'classes_boxes.txt').write_text('0: deer 0.5 0.5 0.2 0.2\n', encoding='utf-8')
        (self.images / 'species.txt').write_text('0: fox\n', encoding='utf-8')
        self.load_images()
        self.assertEqual(self.app._label_manager.get_name(0), 'fox')
        self.assertEqual(self.app._label_manager.count(), 1)

    def test_theme_switch_preserves_work_and_is_persisted(self):
        from core.config import ConfigManager
        self.load_images()
        self.drag(self.canvas_point(30, 30), self.canvas_point(100, 100))
        current = self.app._project.current_image
        boxes = list(current.annotations)
        self.app._set_theme('light')
        self.runtime.processEvents()
        self.assertIs(self.app._canvas._current_image, current)
        self.assertEqual(current.annotations, boxes)
        self.assertTrue(current.is_dirty)
        self.assertEqual(ConfigManager(str(self.config)).load().ui_theme, 'light')
        self.app._set_theme('dark')

    def test_legacy_switch_saves_and_failed_save_does_not_switch(self):
        self.load_images()
        self.drag(self.canvas_point(30, 30), self.canvas_point(100, 100))
        with patch('ui.qt.controller.write_yolo_annotations_atomic', side_effect=OSError('test failure')), \
                patch('ui.qt.controller.showerror'):
            self.app._request_ui_switch()
        self.assertIsNone(self.app._requested_ui)
        self.assertTrue(self.app.isVisible())
        self.app._request_ui_switch()
        self.assertEqual(self.app._requested_ui, 'legacy')
        self.assertTrue(preferred_annotation_txt_path(self.paths[0]).exists())

    def test_text_inputs_keep_typing_semantics_and_global_save(self):
        self.load_images()
        search = self.app._label_panel.search
        search.setFocus()
        QTest.keyClicks(search, 'deer.')
        self.assertEqual(search.text(), 'deer.')
        self.assertEqual(self.app._project.current_index, 0)
        with patch.object(self.app, '_save_current') as save:
            self.app.shortcuts['Ctrl+S'] = save
            QTest.keyClick(search, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        save.assert_called_once()

    def test_label_export_and_dataset_export_formats(self):
        self.load_images()
        item = self.app._project.current_image
        item.annotations = [BBox(10, 10, 50, 50, 0, 'deer')]
        item.mark_dirty()
        self.app._save_current_annotations()
        label_file = self.root / 'export_classes.txt'
        with patch('ui.qt.panels.filedialog.asksaveasfilename', return_value=str(label_file)):
            self.app._label_panel.export_labels()
        self.assertIn('0: deer', label_file.read_text(encoding='utf-8'))
        for fmt in ('YOLO', 'COCO JSON', 'Pascal VOC'):
            folder = self.root / fmt
            folder.mkdir()
            result = {'format': fmt, 'output_dir': str(folder), 'copy_images': True}
            with patch('ui.qt.controller.ExportDialog', return_value=SimpleNamespace(result=result)), \
                    patch('ui.qt.controller.showinfo'), patch('ui.qt.controller.showerror') as error:
                self.app._export()
            error.assert_not_called()
            self.assertTrue(any(folder.iterdir()))

    def test_selected_image_copy_export_with_native_progress(self):
        self.load_images()
        output = self.root / 'copy'
        output.mkdir()
        with patch('ui.qt.controller.ImageExportModeDialog', return_value=SimpleNamespace(result='copy')), \
                patch('ui.qt.controller.filedialog.askdirectory', return_value=str(output)), \
                patch('ui.qt.controller.showinfo'):
            self.app._choose_image_export([0, 1])
        self.assertFalse(self.app._image_export_running)
        self.assertEqual(len(list((output / 'images').glob('*.jpg'))), 2)
        self.assertEqual(self.app._project.total_images, 2)

    def test_rebuilding_class_filter_disposes_old_subscriptions(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        self.load_images()
        for _ in range(3):
            self.app._rebuild_label_filter_menu()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app._apply_label_filter(0)
            self.app._apply_label_filter(None)
        self.assertEqual(len(self.app._label_filter_var.callbacks), 5)

    def test_modal_dialog_owns_shortcuts(self):
        self.load_images()
        dialog = QDialog(self.app)
        button = QPushButton('关闭', dialog)
        dialog.setModal(True)
        dialog.show()
        self.runtime.processEvents()
        with patch.object(self.app, '_save_current') as save:
            self.app.shortcuts['Ctrl+S'] = save
            QTest.keyClick(button, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
            save.assert_not_called()
        dialog.close()

    def test_video_timeline_click_drag_and_keyboard_seek(self):
        self.open_video()
        self.app.resize(1100, 680)
        self.runtime.processEvents()
        controls = self.app._video_controls
        slider = controls.slider
        self.assertGreater(slider.width(), 180)
        start = QPoint(slider.width() // 2, slider.height() // 2)
        QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=start)
        self.wait(lambda: self.app._video_frame_index >= VIDEO_FRAME_COUNT // 2 - 1)
        self.assertFalse(self.app._video_playing)
        self.app._toggle_video_play()
        QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(slider, QPoint(slider.width() // 4, start.y()), 5)
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(slider.width() // 4, start.y()))
        self.assertTrue(self.app._video_playing)
        self.app._toggle_video_play()
        with patch.object(self.app, '_seek_video') as seek:
            QTest.keyClick(slider, Qt.Key.Key_End)
            seek.assert_called_once()
        self.assertFalse(self.app._video_playing)

    def test_all_settings_dialogs_construct_and_close(self):
        from PySide6.QtCore import QTimer
        for constructor, args in [(dialogs.ExportDialog, ()),
                                  (dialogs.ImageExportModeDialog, (2,)),
                                  (dialogs.ImageExportConflictDialog, (1,)),
                                  (dialogs.JumpToImageDialog, (2,)),
                                  (dialogs.RestoreDirectoryDialog, (str(self.images),))]:
            with self.subTest(dialog=constructor.__name__):
                QTimer.singleShot(30, lambda: QApplication.activeModalWidget().reject())
                constructor(self.app, *args)

    def test_video_delete_requires_confirmation_and_keeps_images(self):
        self.load_images()
        path = self.open_video()
        with patch('ui.qt.controller.askyesno', return_value=True):
            self.app._delete_video()
            self.wait(lambda: not self.app._video_delete_running)
        self.assertFalse(path.exists())
        self.assertEqual(self.app._project.total_images, 2)


if __name__ == '__main__':
    unittest.main()

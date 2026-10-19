"""窗口关闭回归测试，使用临时配置隔离用户数据。"""

import sys
import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

# ==================== 测试参数设置区 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_IMAGE_SIZE = (32, 24)
sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image

from core.annotation import BBox
from core.config import ConfigManager
from ui.app import AnnotationApp


class WindowCloseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config_manager = ConfigManager(str(self.root))
        # 禁止恢复真实工作目录，配置读写始终限制在临时目录。
        config_patch = patch('ui.app.ConfigManager', return_value=self.config_manager)
        restore_patch = patch.object(AnnotationApp, '_restore_session')
        config_patch.start()
        self.addCleanup(config_patch.stop)
        restore_patch.start()
        self.addCleanup(restore_patch.stop)
        self.app = AnnotationApp()
        self.app.withdraw()
        self.addCleanup(self._cleanup_window)
        self.callback_errors = []
        self.app.report_callback_exception = lambda *error: self.callback_errors.append(error)

    def _cleanup_window(self):
        # 失败用例仍保留窗口，清理时取消定时任务并释放测试资源。
        try:
            exists = self.app.winfo_exists()
        except tk.TclError:
            return
        if exists:
            for callback in self.app.tk.call('after', 'info'):
                self.app.after_cancel(callback)
            self.app._image_loader.shutdown()
            self.app.destroy()

    def _click_close(self):
        # 调用标题栏关闭按钮注册的 Tcl 回调，覆盖真实的窗口协议入口。
        self.app.tk.call(self.app.protocol('WM_DELETE_WINDOW'))
        self.assertEqual(self.callback_errors, [])

    def _add_dirty_image(self):
        image_dir = self.root / 'images'
        image_dir.mkdir()
        image_path = image_dir / 'sample.jpg'
        Image.new('RGB', TEST_IMAGE_SIZE).save(image_path)
        self.app._project.set_image_paths(str(image_dir), [image_path])
        item = self.app._project.current_image
        item.width, item.height = TEST_IMAGE_SIZE
        item.annotations = [BBox(x1=2, y1=3, x2=12, y2=11, class_id=0, class_name='test')]
        item.mark_dirty()
        return item

    def test_close_before_opening_video_destroys_window(self):
        self.assertIsNone(self.app._video_pause_event)
        with patch.object(self.app, 'destroy', wraps=self.app.destroy) as destroy:
            self._click_close()
        destroy.assert_called_once()
        self.assertTrue(self.config_manager.path.is_file())

    def test_close_saves_modified_annotations(self):
        item = self._add_dirty_image()
        self._click_close()
        self.assertFalse(item.is_dirty)
        label_path = self.root / 'labels' / 'sample.txt'
        self.assertTrue(label_path.is_file())
        self.assertTrue(label_path.read_text(encoding='utf-8').startswith('0 '))
        self.assertEqual(self.config_manager.load().last_image_path, str(item.path))

    def test_failed_save_keeps_window_and_modified_annotations(self):
        item = self._add_dirty_image()
        with patch('ui.app.write_yolo_annotations_atomic', side_effect=OSError('test write failure')), \
                patch('ui.app.showerror') as showerror, \
                patch.object(self.app, 'destroy', wraps=self.app.destroy) as destroy:
            self._click_close()
        destroy.assert_not_called()
        showerror.assert_called_once()
        self.assertTrue(item.is_dirty)
        self.assertTrue(self.app.winfo_exists())

    def test_close_signals_video_events_and_releases_capture(self):
        stop_event = threading.Event()
        pause_event = threading.Event()
        open_stop_event = threading.Event()
        capture = Mock()
        self.app._video_stop_event = stop_event
        self.app._video_pause_event = pause_event
        self.app._video_open_stop_event = open_stop_event
        self.app._video_capture = capture
        self._click_close()
        self.assertTrue(stop_event.is_set())
        self.assertTrue(pause_event.is_set())
        self.assertTrue(open_stop_event.is_set())
        capture.release.assert_called_once()


if __name__ == '__main__':
    unittest.main()

"""视频导出、视频条目和菜单的回归验证，所有媒体均为临时测试文件。"""

import hashlib
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import imageio_ffmpeg
import numpy as np

# ==================== 导出测试参数设置区 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
ENCODER_TEST_TIMEOUT = 20

import test_video as video_tests
from core.annotation import BBox
from io_ops.video_export import (
    export_annotated_video, validate_video_export_path, VideoExportCancelled,
)


def sample_annotations():
    return [[BBox(8, 10, 55, 43, 0, '鹿', 0.87)]
            for _ in range(video_tests.TEST_FRAME_COUNT)]


class VideoExportTests(video_tests.VideoFixture):
    def test_export_keeps_original_video_timing_and_draws_annotations(self):
        before = hashlib.sha256(self.video_path.read_bytes()).digest()
        output = self.root / '带标注的视频.mp4'
        result = export_annotated_video(self.video_path, output, sample_annotations())
        capture = cv2.VideoCapture(str(output))
        try:
            self.assertTrue(capture.isOpened())
            self.assertEqual(int(capture.get(cv2.CAP_PROP_FRAME_COUNT)), video_tests.TEST_FRAME_COUNT)
            self.assertAlmostEqual(capture.get(cv2.CAP_PROP_FPS), video_tests.TEST_FPS)
            frames = []
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                frames.append(frame)
            self.assertEqual(len(frames), video_tests.TEST_FRAME_COUNT)
            self.assertEqual(frames[0].shape[:2], (48, 64))
            # 原始首帧为纯黑色，导出后框边、标签背景和文字均应产生彩色像素。
            self.assertGreater(np.count_nonzero(frames[0] > 70), 100)
            self.assertGreater(int(frames[0][40, 8, 2]), int(frames[0][40, 8, 1]) + 70)
        finally:
            capture.release()
        self.assertEqual(result.frame_count, video_tests.TEST_FRAME_COUNT)
        self.assertEqual(hashlib.sha256(self.video_path.read_bytes()).digest(), before)

    def test_frame_count_mismatch_preserves_existing_output(self):
        output = self.root / 'existing.mp4'
        output.write_bytes(b'existing-video')
        with self.assertRaisesRegex(RuntimeError, '不匹配'):
            export_annotated_video(self.video_path, output, [[]])
        self.assertEqual(output.read_bytes(), b'existing-video')
        self.assertEqual(list(self.root.glob('.existing-*.mp4')), [])

    def test_cancel_cleans_partial_file_without_overwriting_output(self):
        output = self.root / 'cancelled.mp4'
        output.write_bytes(b'existing-video')
        stop = threading.Event()
        with self.assertRaises(VideoExportCancelled):
            export_annotated_video(
                self.video_path, output, sample_annotations(), stop_event=stop,
                progress_callback=lambda *args: stop.set(),
            )
        self.assertEqual(output.read_bytes(), b'existing-video')
        self.assertEqual(list(self.root.glob('.cancelled-*.mp4')), [])

    def test_source_path_cannot_be_used_as_export_target(self):
        with self.assertRaisesRegex(ValueError, '原视频相同'):
            validate_video_export_path(self.video_path, self.video_path)

    def test_empty_detections_are_valid_complete_inference_results(self):
        output = self.root / 'background.mp4'
        result = export_annotated_video(
            self.video_path, output, [[] for _ in range(video_tests.TEST_FRAME_COUNT)],
        )
        self.assertEqual(result.frame_count, video_tests.TEST_FRAME_COUNT)
        self.assertTrue(output.is_file())

    def test_original_audio_track_is_preserved(self):
        source = self.root / 'with_audio.mp4'
        encoder = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run([
            encoder, '-y', '-loglevel', 'error', '-i', str(self.video_path),
            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
            '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-c:a', 'aac', str(source),
        ], capture_output=True, check=True, timeout=ENCODER_TEST_TIMEOUT)
        output = self.root / 'audio_annotated.mp4'
        export_annotated_video(source, output, sample_annotations())
        result = subprocess.run([
            encoder, '-loglevel', 'error', '-i', str(output), '-map', '0:a:0', '-f', 'null', '-',
        ], capture_output=True, timeout=ENCODER_TEST_TIMEOUT)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))


class VideoExportAppTests(video_tests.VideoAppFixture):
    def add_images(self):
        paths = [self.root / 'first.jpg', self.root / 'second.jpg']
        for path in paths:
            cv2.imwrite(str(path), np.full((48, 64, 3), 100, dtype=np.uint8))
        self.app._project.set_image_paths(str(self.root), paths)
        self.app._thumb_panel.set_images(list(self.app._project.image_list))
        self.app._show_current_image_async()
        video_tests.wait_for(lambda: self.app._canvas._current_image is not None, self.app.update)
        return list(self.app._project.image_list)

    def test_video_row_selection_and_image_mapping_remain_independent(self):
        original = self.add_images()
        mapping = self.app._thumb_panel.full_indices()
        self.open_video()
        panel = self.app._thumb_panel
        self.assertIn(str(self.video_path), panel._video_listbox.get(0))
        self.assertEqual(panel._video_listbox.curselection(), (0,))
        self.assertEqual(panel._listbox.curselection(), ())
        cache = sample_annotations()
        self.app._video_item.annotations = cache
        self.app._video_annotations = cache
        old_player = self.app._video_player
        self.app._on_thumb_selected(1)
        old_player._thread.join(video_tests.WAIT_TIMEOUT_SECONDS)
        video_tests.wait_for(lambda: self.app._canvas._current_image is original[1], self.app.update)
        self.assertEqual(panel._video_listbox.size(), 1)
        self.assertEqual(panel._video_listbox.curselection(), ())
        self.app._on_video_selected()
        video_tests.wait_for(lambda: self.app._canvas._video_mode, self.app.update)
        self.assertIs(self.app._video_annotations, cache)
        self.assertEqual(panel.full_indices(), mapping)
        self.assertEqual(self.app._project.image_list, original)

    def test_cached_video_export_does_not_run_inference(self):
        self.open_video()
        self.app._video_item.annotations = sample_annotations()
        output = self.root / 'cached.mp4'
        with patch('ui.app.filedialog.asksaveasfilename', return_value=str(output)), \
                patch.object(self.app._pre_annotator, 'predict_video_async') as infer:
            self.app._video_controls._export_button.invoke()
            video_tests.wait_for(lambda: output.is_file() and not self.app._video_export_running,
                                 self.app.update)
        infer.assert_not_called()

    def test_original_video_preview_still_exports_cached_annotation_boxes(self):
        self.open_video()
        cache = sample_annotations()
        self.app._video_item.annotations = cache
        self.app._video_annotations = cache
        self.app._play_listed_video(False)
        output = self.root / 'from_original_preview.mp4'
        with patch('ui.app.filedialog.asksaveasfilename', return_value=str(output)), \
                patch.object(self.app._pre_annotator, 'predict_video_async') as infer:
            self.app._export_video()
            video_tests.wait_for(lambda: output.is_file() and not self.app._video_export_running,
                                 self.app.update)
        infer.assert_not_called()
        self.assertFalse(self.app._video_show_annotations)
        self.assertIs(self.app._video_item.annotations, cache)
        capture = cv2.VideoCapture(str(output))
        try:
            ok, frame = capture.read()
            self.assertTrue(ok)
            self.assertGreater(int(frame[40, 8, 2]), int(frame[40, 8, 1]) + 70)
        finally:
            capture.release()

    def test_export_without_cache_automatically_infers_every_frame_first(self):
        self.open_video()
        self.app._pre_annotator._model = Mock(return_value=[])
        output = self.root / 'automatic.mp4'
        with patch('ui.app.filedialog.asksaveasfilename', return_value=str(output)), \
                patch.object(self.app._pre_annotator, 'predict_frame',
                             return_value=sample_annotations()[0]) as infer:
            self.app._export_video()
            video_tests.wait_for(lambda: output.is_file() and not self.app._video_export_running,
                                 self.app.update)
        self.assertEqual(infer.call_count, video_tests.TEST_FRAME_COUNT)
        self.assertIsNone(self.app._video_export_pending)
        log = self.app._status_bar._log_text.get('1.0', 'end')
        self.assertIn('视频推理完成', log)
        self.assertIn('视频导出完成', log)

    def test_inference_error_does_not_export_a_video(self):
        self.open_video()
        self.app._pre_annotator._model = Mock(return_value=[])
        output = self.root / 'failed.mp4'
        with patch('ui.app.filedialog.asksaveasfilename', return_value=str(output)), \
                patch.object(self.app._pre_annotator, 'predict_frame', side_effect=RuntimeError('failure')):
            self.app._export_video()
            video_tests.wait_for(lambda: not self.app._video_inference_running, self.app.update)
        self.assertFalse(output.exists())
        self.assertIsNone(self.app._video_export_pending)

    def test_export_during_inference_waits_and_reuses_the_same_results(self):
        self.open_video()
        self.app._pre_annotator._model = Mock(return_value=[])
        output = self.root / 'queued.mp4'
        entered, release = threading.Event(), threading.Event()

        def predict(*args, **kwargs):
            entered.set()
            release.wait(video_tests.WAIT_TIMEOUT_SECONDS)
            return sample_annotations()[0]

        with patch('ui.app.filedialog.asksaveasfilename', return_value=str(output)), \
                patch.object(self.app._pre_annotator, 'predict_frame', side_effect=predict) as infer:
            try:
                self.app._pre_annotate_current()
                self.assertTrue(entered.wait(video_tests.WAIT_TIMEOUT_SECONDS))
                self.app._export_video()
                self.assertIsNotNone(self.app._video_export_pending)
                self.assertFalse(output.exists())
            finally:
                release.set()
            video_tests.wait_for(lambda: output.is_file() and not self.app._video_export_running,
                                 self.app.update)
        self.assertEqual(infer.call_count, video_tests.TEST_FRAME_COUNT)
        self.assertIsNone(self.app._video_export_pending)

    def test_cancelling_inference_also_cancels_the_pending_export(self):
        self.open_video()
        self.app._pre_annotator._model = Mock(return_value=[])
        output = self.root / 'cancelled_inference.mp4'
        entered, release = threading.Event(), threading.Event()

        def predict(*args, **kwargs):
            entered.set()
            release.wait(video_tests.WAIT_TIMEOUT_SECONDS)
            return sample_annotations()[0]

        with patch('ui.app.filedialog.asksaveasfilename', return_value=str(output)), \
                patch.object(self.app._pre_annotator, 'predict_frame', side_effect=predict):
            try:
                self.app._export_video()
                self.assertTrue(entered.wait(video_tests.WAIT_TIMEOUT_SECONDS))
                self.app._stop_batch_pre_annotate()
            finally:
                release.set()
            video_tests.wait_for(lambda: not self.app._video_inference_running, self.app.update)
        self.assertFalse(output.exists())
        self.assertIsNone(self.app._video_item.annotations)
        self.assertIsNone(self.app._video_export_pending)

    def test_export_from_image_view_activates_video_and_preserves_images(self):
        original = self.add_images()
        self.open_video()
        player = self.app._video_player
        self.app._on_thumb_selected(1)
        player._thread.join(video_tests.WAIT_TIMEOUT_SECONDS)
        video_tests.wait_for(lambda: self.app._canvas._current_image is original[1], self.app.update)
        self.app._pre_annotator._model = Mock(return_value=[])
        output = self.root / 'from_list.mp4'
        with patch('ui.app.filedialog.asksaveasfilename', return_value=str(output)), \
                patch.object(self.app._pre_annotator, 'predict_frame', return_value=[]) as infer:
            self.app._export_video()
            video_tests.wait_for(lambda: output.is_file() and not self.app._video_export_running,
                                 self.app.update)
        self.assertEqual(infer.call_count, video_tests.TEST_FRAME_COUNT)
        self.assertEqual(self.app._project.image_list, original)
        self.assertEqual(self.app._project.current_index, 1)
        self.assertEqual(self.app._thumb_panel._video_listbox.curselection(), (0,))

    def test_image_canvas_keeps_its_original_context_menu_after_video_navigation(self):
        original = self.add_images()
        self.open_video()
        player = self.app._video_player
        self.app._on_thumb_selected(0)
        player._thread.join(video_tests.WAIT_TIMEOUT_SECONDS)
        video_tests.wait_for(lambda: self.app._canvas._current_image is original[0], self.app.update)
        image_menu, video_menu = Mock(), Mock()
        self.app._canvas.set_image_context_menu_handler(image_menu)
        self.app._canvas.set_video_context_menu_handler(video_menu)
        event = SimpleNamespace(x_root=0, y_root=0)
        self.app._canvas._on_right_up(event)
        image_menu.assert_called_once_with(event)
        video_menu.assert_not_called()

    def test_removing_video_keeps_disk_file_and_all_images(self):
        original = self.add_images()
        self.open_video()
        player = self.app._video_player
        self.app._remove_video_from_list()
        player._thread.join(video_tests.WAIT_TIMEOUT_SECONDS)
        video_tests.wait_for(lambda: self.app._canvas._current_image is original[0], self.app.update)
        self.assertTrue(self.video_path.exists())
        self.assertIsNone(self.app._video_item)
        self.assertEqual(self.app._thumb_panel._video_listbox.size(), 0)
        self.assertEqual(self.app._project.image_list, original)

    def test_delete_video_requires_confirmation_and_only_deletes_video(self):
        original = self.add_images()
        self.open_video()
        with patch('ui.app.askyesno', return_value=False):
            self.app._delete_video()
        self.assertTrue(self.video_path.exists())
        with patch('ui.app.askyesno', return_value=True):
            self.app._delete_video()
            video_tests.wait_for(lambda: not self.app._video_delete_running, self.app.update)
        self.assertFalse(self.video_path.exists())
        self.assertIsNone(self.app._video_item)
        self.assertEqual(self.app._project.image_list, original)
        self.assertTrue(all(image.path.exists() for image in original))

    def test_export_is_blocked_while_the_video_file_is_being_deleted(self):
        self.open_video()
        self.app._video_item.annotations = sample_annotations()
        self.app._video_delete_running = True
        with patch('ui.app.filedialog.asksaveasfilename') as save_dialog, \
                patch.object(self.app, '_start_video_export') as export:
            self.app._export_video()
        save_dialog.assert_not_called()
        export.assert_not_called()

    def test_video_row_and_canvas_offer_the_same_video_menu(self):
        self.open_video()
        event = SimpleNamespace(x_root=0, y_root=0)
        for invoke in (self.app._thumb_panel._on_video_right_click, self.app._canvas._on_right_up):
            with patch('ui.app.tk.Menu') as menu_class:
                invoke(event)
            labels = [call.kwargs['label'] for call in menu_class.return_value.add_command.call_args_list]
            self.assertEqual(labels, ['从列表中删除', '删除视频', '上一帧', '下一帧',
                                      '播放原视频', '推理视频', '导出视频'])
            commands = {call.kwargs['label']: call.kwargs['command']
                        for call in menu_class.return_value.add_command.call_args_list}
            commands['下一帧']()
            self.assertEqual(self.app._video_frame_index, 1)
            self.assertFalse(self.app._video_player.playing)
            commands['上一帧']()
            self.assertEqual(self.app._video_frame_index, 0)
            commands['播放原视频']()
            self.assertFalse(self.app._video_show_annotations)
            self.assertTrue(self.app._video_player.playing)
            self.app._toggle_video_play()


if __name__ == '__main__':
    unittest.main()

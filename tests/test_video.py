"""视频播放状态与逐帧推理回归测试，视频和配置均在临时目录中创建。"""

import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

# ==================== 测试参数设置区 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_FRAME_SIZE = (64, 48)
TEST_FPS = 20.0
TEST_FRAME_COUNT = 20
WAIT_TIMEOUT_SECONDS = 4.0
sys.path.insert(0, str(PROJECT_ROOT))

from core.annotation import BBox
from core.config import ConfigManager
from core.label_manager import LabelManager
from io_ops.pre_annotator import PreAnnotator
from io_ops.video_player import VideoPlayer, VIDEO_PLAYBACK_SPEEDS
from ui.app import AnnotationApp
from ui.video_controls import VideoControls, SPEED_OPTIONS


class VideoControlsTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        # 界面移至屏幕外，只验证真实 Tk 控件事件，不打断用户当前窗口。
        self.root.geometry('800x100+20000+20000')
        self.addCleanup(self.root.destroy)
        self.on_seek = Mock(return_value='break')
        self.on_speed = Mock()
        self.controls = VideoControls(
            self.root, on_seek=self.on_seek, on_speed_changed=self.on_speed,
        )
        self.controls.pack(fill='x')
        self.controls.set_duration(100)
        self.root.update()

    def test_scale_release_cancels_native_repeat_timer(self):
        controls = self.controls
        controls.set_position(10)
        scale = controls._scale
        x, y = scale.winfo_width() - 10, scale.winfo_height() // 2
        scale.event_generate('<ButtonPress-1>', x=x, y=y)
        self.root.update()
        scale.event_generate('<ButtonRelease-1>', x=x, y=y)
        self.root.update()
        frozen_position = controls.get_position()
        deadline = time.perf_counter() + 0.5
        while time.perf_counter() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(controls.get_position(), frozen_position)
        self.on_seek.assert_called_once_with(frozen_position)

    def test_drag_position_and_time_are_not_overwritten_by_playback(self):
        controls = self.controls
        controls._begin_seek()
        controls._scale.set(40)
        self.root.update()
        controls.set_position(5)
        self.assertEqual(controls.get_position(), 40)
        self.assertEqual(controls._time_label.cget('text'), '00:40 / 01:40')
        controls._finish_seek()
        self.on_seek.assert_called_once_with(40)
        controls.set_position(5)
        self.assertEqual(self.on_seek.call_count, 1)

    def test_speed_options_dispatch_selected_values(self):
        self.assertEqual(SPEED_OPTIONS, ('1x', '1.25x', '1.5x', '2x'))
        for speed in VIDEO_PLAYBACK_SPEEDS:
            self.controls._speed_combo.set(f'{speed:g}x')
            self.controls._speed_combo.event_generate('<<ComboboxSelected>>')
            self.root.update()
            self.on_speed.assert_called_with(speed)


def wait_for(predicate, pump=None):
    """在有界时间内等待后台任务，GUI 用例同步处理主线程事件。"""
    deadline = time.perf_counter() + WAIT_TIMEOUT_SECONDS
    while time.perf_counter() < deadline:
        if pump:
            pump()
        result = predicate()
        if result:
            return result
        time.sleep(0.005)
    raise AssertionError('后台视频任务未在预期时间内完成')


class FakeCapture:
    """可阻塞指定帧的解码器，用于稳定复现暂停、定位与旧帧的并发时序。"""

    def __init__(self, count=100, blocked_frame=None):
        self.count = count
        self.index = 0
        self.read_count = 0
        self.seeks = []
        self.release_threads = []
        self.blocked_frame = blocked_frame
        self.blocked = threading.Event()
        self.proceed = threading.Event()

    def set(self, property_id, index):
        self.index = int(index)
        self.seeks.append(self.index)
        return True

    def grab(self):
        self.index += 1
        return self.index <= self.count

    def read(self):
        self.read_count += 1
        if self.index == self.blocked_frame and not self.proceed.is_set():
            self.blocked.set()
            if not self.proceed.wait(WAIT_TIMEOUT_SECONDS):
                raise RuntimeError('测试解码等待超时')
        if self.index >= self.count:
            return False, None
        image = np.full((48, 64, 3), self.index % 256, dtype=np.uint8)
        self.index += 1
        return True, image

    def release(self):
        self.release_threads.append(threading.get_ident())


class VideoPlayerTests(unittest.TestCase):
    def make_player(self, capture=None, fps=TEST_FPS):
        capture = capture or FakeCapture()
        player = VideoPlayer(capture, fps, capture.count)

        def cleanup():
            player.close()
            capture.proceed.set()
            player._thread.join(WAIT_TIMEOUT_SECONDS)

        self.addCleanup(cleanup)
        return player, capture

    def test_open_previews_first_frame_without_playing(self):
        player, capture = self.make_player()
        frame = wait_for(player.take_frame)
        self.assertEqual(frame.index, 0)
        self.assertFalse(player.playing)
        self.assertEqual(capture.read_count, 1)

    def test_pause_stops_decoding_and_keeps_displayed_frame(self):
        player, capture = self.make_player()
        wait_for(player.take_frame)
        player.play(0)
        displayed = wait_for(lambda: (frame if (frame := player.take_frame())
                                     and frame.index >= 2 else None))
        player.pause(displayed.index)
        frozen = wait_for(player.take_frame)
        reads = capture.read_count
        threading.Event().wait(0.15)
        self.assertEqual(frozen.index, displayed.index)
        self.assertEqual(capture.read_count, reads)
        self.assertIsNone(player.take_frame())
        self.assertFalse(player.playing)

    def test_late_frame_is_discarded_after_seek(self):
        player, capture = self.make_player(FakeCapture(blocked_frame=2))
        wait_for(player.take_frame)
        player.play(0)
        self.assertTrue(capture.blocked.wait(WAIT_TIMEOUT_SECONDS))
        player.seek(0.7, playing=False)
        self.assertIsNone(player.take_frame())
        capture.proceed.set()
        result = wait_for(player.take_frame)
        self.assertEqual(result.index, 14)
        self.assertEqual(result.image.getpixel((0, 0)), (14, 14, 14))
        self.assertFalse(player.playing)

    def test_normal_playback_never_seeks_back_between_frames(self):
        player, capture = self.make_player(FakeCapture(count=12), fps=40.0)
        wait_for(player.take_frame)
        player.play(0)
        seen = []

        def ended():
            frame = player.take_frame()
            if frame:
                seen.append(frame.index)
            return seen and seen[-1] == 11 and not player.playing

        wait_for(ended)
        self.assertEqual(seen, sorted(set(seen)))
        self.assertEqual(capture.seeks, [0, 0])
        player.play(seen[-1])
        self.assertEqual(wait_for(player.take_frame).index, 0)

    def test_required_speeds_change_playback_timing(self):
        for speed in VIDEO_PLAYBACK_SPEEDS:
            with self.subTest(speed=speed):
                player, _ = self.make_player(FakeCapture(count=21))
                wait_for(player.take_frame)
                player.set_speed(speed, 0)
                wait_for(player.take_frame)
                started = time.perf_counter()
                player.play(0)
                wait_for(lambda: not player.playing)
                elapsed = time.perf_counter() - started
                self.assertAlmostEqual(elapsed, 1.0 / speed, delta=0.18)

    def test_decoder_is_released_only_by_owning_thread(self):
        player, capture = self.make_player(FakeCapture(blocked_frame=0))
        self.assertTrue(capture.blocked.wait(WAIT_TIMEOUT_SECONDS))
        player.close()
        self.assertEqual(capture.release_threads, [])
        capture.proceed.set()
        player._thread.join(WAIT_TIMEOUT_SECONDS)
        self.assertEqual(capture.release_threads, [player._thread.ident])


class VideoFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.video_path = self.root / 'sample.avi'
        writer = cv2.VideoWriter(
            str(self.video_path), cv2.VideoWriter_fourcc(*'MJPG'), TEST_FPS, TEST_FRAME_SIZE,
        )
        self.assertTrue(writer.isOpened())
        try:
            for index in range(TEST_FRAME_COUNT):
                writer.write(np.full((48, 64, 3), index * 10, dtype=np.uint8))
        finally:
            writer.release()


class VideoInferenceTests(VideoFixture):
    def make_annotator(self):
        annotator = PreAnnotator()
        annotator._model = Mock(return_value=[])
        return annotator

    def infer(self, annotator):
        done = threading.Event()
        results = []

        def on_done(result):
            results.append(result)
            done.set()

        annotator.predict_video_async(self.video_path, 0.2, LabelManager(), on_done)
        self.assertTrue(done.wait(WAIT_TIMEOUT_SECONDS))
        return results[0]

    def test_inference_processes_every_frame_including_empty_detections(self):
        annotator = self.make_annotator()
        calls = []

        def predict(frame, threshold, labels, raise_on_error=False):
            index = len(calls)
            calls.append(float(frame.mean()))
            return [BBox(1, 2, 20, 30, 0, f'frame_{index}', 0.87)] if index % 2 else []

        with patch.object(annotator, 'predict_frame', side_effect=predict):
            result = self.infer(annotator)
        self.assertEqual(result.error, '')
        self.assertEqual(len(result.annotations), TEST_FRAME_COUNT)
        self.assertEqual(len(calls), TEST_FRAME_COUNT)
        self.assertEqual(calls, sorted(calls))
        self.assertEqual(result.annotations[0], [])
        self.assertEqual(result.annotations[1][0].class_name, 'frame_1')
        self.assertEqual(result.annotations[1][0].confidence, 0.87)
        self.assertFalse(annotator.is_busy)

    def test_prediction_error_does_not_publish_partial_cache(self):
        annotator = self.make_annotator()
        with patch.object(annotator, 'predict_frame', side_effect=[[], RuntimeError('model failure')]):
            result = self.infer(annotator)
        self.assertIn('model failure', result.error)
        self.assertEqual(result.annotations, [])
        self.assertFalse(annotator.is_busy)

    def test_cancelled_inference_does_not_publish_partial_cache(self):
        annotator = self.make_annotator()

        def predict(*args, **kwargs):
            annotator.cancel()
            return []

        with patch.object(annotator, 'predict_frame', side_effect=predict):
            result = self.infer(annotator)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.annotations, [])
        self.assertFalse(annotator.is_busy)

    def test_model_receives_original_bgr_frame(self):
        annotator = self.make_annotator()
        frame = np.array([[[10, 20, 30]]], dtype=np.uint8)
        annotator.predict_frame(frame)
        actual = annotator._model.call_args.args[0]
        np.testing.assert_array_equal(actual, frame)


class VideoAppTests(VideoFixture):
    def setUp(self):
        super().setUp()
        config_patch = patch('ui.app.ConfigManager', return_value=ConfigManager(str(self.root)))
        restore_patch = patch.object(AnnotationApp, '_restore_session')
        config_patch.start()
        self.addCleanup(config_patch.stop)
        restore_patch.start()
        self.addCleanup(restore_patch.stop)
        self.app = AnnotationApp()
        self.app.withdraw()
        self.addCleanup(self.cleanup_app)

    def cleanup_app(self):
        player = self.app._video_player
        self.app._stop_video()
        if player:
            player._thread.join(WAIT_TIMEOUT_SECONDS)
        for callback in self.app.tk.call('after', 'info'):
            self.app.after_cancel(callback)
        self.app._image_loader.shutdown()
        self.app.destroy()

    def open_video(self):
        with patch('ui.app.filedialog.askopenfilename', return_value=str(self.video_path)):
            self.app._open_video()
        wait_for(lambda: self.app._canvas._video_mode, self.app.update)

    def test_open_video_needs_no_weights_and_starts_paused(self):
        self.open_video()
        self.assertFalse(self.app._pre_annotator.is_loaded)
        self.assertFalse(self.app._video_player.playing)
        self.assertIsNone(self.app._video_annotations)
        self.assertEqual(self.app._video_frame_index, 0)

    def test_ctrl_x_caches_all_frames_then_playback_and_seek_reuse_results(self):
        self.open_video()
        self.app._pre_annotator._model = Mock(return_value=[])
        calls = []

        def predict(*args, **kwargs):
            index = len(calls)
            calls.append(index)
            return [BBox(2, 3, 30, 40, 0, f'frame_{index}', 0.87)]

        with patch.object(self.app._pre_annotator, 'predict_frame', side_effect=predict):
            self.app._pre_annotate_current()
            wait_for(lambda: self.app._video_annotations is not None, self.app.update)
            self.assertEqual(len(calls), TEST_FRAME_COUNT)
            self.assertFalse(self.app._video_player.playing)
            self.assertIn('视频推理完成', self.app._status_bar._log_text.get('1.0', 'end'))
            self.app._set_video_speed(2.0)
            self.app._toggle_video_play()
            wait_for(lambda: self.app._video_frame_index >= 3, self.app.update)
            self.app._toggle_video_play()
            paused_index = self.app._video_frame_index
            wait_for(lambda: self.app._video_player._pending_seek is None, self.app.update)
            self.assertFalse(self.app._video_player.playing)
            self.app._begin_video_seek()
            self.app._seek_video(0.5)
            wait_for(lambda: self.app._canvas._video_annotations
                     and self.app._canvas._video_annotations[0].class_name == 'frame_10',
                     self.app.update)
            self.assertFalse(self.app._video_player.playing)
            self.assertGreaterEqual(paused_index, 3)
            label_text = [self.app._canvas.itemcget(item, 'text')
                          for item in self.app._canvas.find_withtag('video_annotation')]
            self.assertEqual(label_text, ['frame_10 0.87'])
            self.app._toggle_video_play()
            self.app._begin_video_seek()
            self.app._seek_video(0.2)
            self.assertTrue(self.app._video_player.playing)
            wait_for(lambda: self.app._video_frame_index == TEST_FRAME_COUNT - 1,
                     self.app.update)
            self.assertEqual(len(calls), TEST_FRAME_COUNT)

    def test_closing_video_cancels_inference_and_ignores_late_completion(self):
        self.open_video()
        self.app._pre_annotator._model = Mock(return_value=[])
        entered = threading.Event()
        release = threading.Event()

        def predict(*args, **kwargs):
            entered.set()
            release.wait(WAIT_TIMEOUT_SECONDS)
            return []

        with patch.object(self.app._pre_annotator, 'predict_frame', side_effect=predict):
            self.app._pre_annotate_current()
            self.assertTrue(entered.wait(WAIT_TIMEOUT_SECONDS))
            stop = self.app._video_inference_stop_event
            self.app._stop_video()
            self.assertTrue(stop.is_set())
            release.set()
            wait_for(lambda: not self.app._pre_annotator.is_busy, self.app.update)
            self.app.update()
            self.assertEqual(self.app._video_path, '')
            self.assertIsNone(self.app._video_annotations)


if __name__ == '__main__':
    unittest.main()

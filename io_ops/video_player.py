"""独立的视频解码与播放时钟，不在播放线程中执行模型推理。"""

import math
import threading
import time
from dataclasses import dataclass

from PIL import Image


# ==================== 视频播放参数设置区 ====================
DEFAULT_VIDEO_FPS = 25.0
VIDEO_PLAYBACK_SPEEDS = (1.0, 1.25, 1.5, 2.0)


@dataclass(frozen=True)
class VideoFrame:
    """帧号和图像作为同一结果传递，版本号隔离暂停、定位前的旧帧。"""

    index: int
    image: Image.Image
    revision: int


class VideoPlayer:
    """单个后台线程独占解码器，主线程只下发控制命令和取最新帧。"""

    def __init__(self, capture, fps, frame_count):
        self.fps = fps if math.isfinite(fps) and fps > 0 else DEFAULT_VIDEO_FPS
        self.frame_count = max(0, int(frame_count))
        self._capture = capture
        self._condition = threading.Condition()
        self._closed = False
        self._playing = False
        self._speed = 1.0
        self._revision = 0
        self._pending_seek = 0
        self._latest_frame = None
        self._last_index = -1
        self._at_end = False
        self._error = ''
        self._anchor_index = 0
        self._anchor_time = time.perf_counter()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def playing(self):
        with self._condition:
            return self._playing

    @property
    def error(self):
        with self._condition:
            return self._error

    def _reset_position(self, index):
        """调用方持有条件锁；任何时间线变化都使已排队和正在解码的帧失效。"""
        index = max(0, int(index))
        if self.frame_count:
            index = min(index, self.frame_count - 1)
        self._revision += 1
        self._latest_frame = None
        self._pending_seek = index
        self._anchor_index = index
        self._anchor_time = time.perf_counter()
        self._at_end = False
        self._condition.notify_all()

    def play(self, displayed_index):
        with self._condition:
            if self._closed:
                return
            target = 0 if self._at_end else displayed_index
            self._playing = True
            self._reset_position(target)

    def pause(self, displayed_index):
        with self._condition:
            if self._closed:
                return
            self._playing = False
            self._reset_position(displayed_index)

    def seek(self, seconds, playing=None):
        self.seek_frame(max(0.0, float(seconds)) * self.fps, playing=playing)

    def seek_frame(self, index, playing=None):
        """直接按帧号定位，逐帧操作无需经过秒数换算和浮点数截断。"""
        with self._condition:
            if self._closed:
                return
            if playing is not None:
                self._playing = bool(playing)
            self._reset_position(index)

    def set_speed(self, speed, displayed_index):
        if speed not in VIDEO_PLAYBACK_SPEEDS:
            raise ValueError(f'不支持的播放倍速: {speed}')
        with self._condition:
            if self._closed:
                return
            self._speed = speed
            self._reset_position(displayed_index)

    def take_frame(self):
        with self._condition:
            result = self._latest_frame
            self._latest_frame = None
            return result

    def set_frame_count(self, count):
        """完整顺序扫描后，以实际帧数补全未知或不准确的视频元数据。"""
        with self._condition:
            self.frame_count = max(0, int(count))

    def close(self):
        """只发送退出信号，解码器由其所属线程释放，避免与 read/seek 并发。"""
        with self._condition:
            self._closed = True
            self._playing = False
            self._latest_frame = None
            self._condition.notify_all()

    def _run(self):
        import cv2

        next_index = 0
        try:
            while True:
                with self._condition:
                    while not self._closed:
                        if self._pending_seek is not None:
                            target = self._pending_seek
                            self._pending_seek = None
                            seeking = True
                            break
                        if not self._playing:
                            self._condition.wait()
                            continue
                        frame_rate = self.fps * self._speed
                        now = time.perf_counter()
                        delay = (self._anchor_time
                                 + (next_index - self._anchor_index) / frame_rate - now)
                        if delay > 0:
                            self._condition.wait(delay)
                            continue
                        # 解码或界面耗时较长时跳过已过期画面，时间线始终单向前进。
                        target = max(next_index, self._anchor_index
                                     + int((now - self._anchor_time) * frame_rate))
                        if self.frame_count:
                            target = min(target, self.frame_count - 1)
                        seeking = False
                        break
                    if self._closed:
                        break
                    revision = self._revision

                if seeking:
                    self._capture.set(cv2.CAP_PROP_POS_FRAMES, target)
                    next_index = target
                while next_index < target:
                    if not self._capture.grab():
                        break
                    next_index += 1
                    with self._condition:
                        if self._closed or revision != self._revision:
                            break
                with self._condition:
                    if self._closed or revision != self._revision:
                        continue

                ok, frame = self._capture.read()
                if not ok:
                    with self._condition:
                        if revision == self._revision:
                            self._playing = False
                            self._at_end = True
                            if self._last_index < 0:
                                self._error = '无法解码视频画面'
                    continue
                image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                next_index = target + 1
                with self._condition:
                    if self._closed or revision != self._revision:
                        continue
                    self._last_index = target
                    self._latest_frame = VideoFrame(target, image, revision)
                    if self.frame_count and next_index >= self.frame_count:
                        self._at_end = True
                        self._playing = False
        except Exception as exc:
            with self._condition:
                self._error = str(exc)
                self._playing = False
        finally:
            self._capture.release()

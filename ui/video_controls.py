"""视频播放与进度控制组件。"""

import tkinter as tk
from tkinter import ttk

from io_ops.video_player import VIDEO_PLAYBACK_SPEEDS


# ==================== 视频控件参数设置区 ====================
SPEED_OPTIONS = tuple(f'{speed:g}x' for speed in VIDEO_PLAYBACK_SPEEDS)


class VideoControls(ttk.Frame):
    """提供播放控制、时间显示和可拖动的视频进度条。"""

    def __init__(self, parent, on_toggle_play=None, on_seek=None,
                 on_seek_start=None, on_speed_changed=None, on_export=None):
        super().__init__(parent)
        self._on_toggle_play = on_toggle_play
        self._on_seek = on_seek
        self._on_seek_start = on_seek_start
        self._on_speed_changed = on_speed_changed
        self._duration = 0.0
        self._seeking = False
        self._position = tk.DoubleVar(value=0.0)
        self._speed = tk.StringVar(value=SPEED_OPTIONS[0])
        self._enabled = False
        self._export_busy = False

        self._play_button = ttk.Button(self, text='播放', command=self._toggle_play)
        self._play_button.grid(row=0, column=0, padx=(4, 8), pady=4)
        self._time_label = ttk.Label(self, text='00:00 / 00:00', width=14, anchor='center')
        self._time_label.grid(row=0, column=1, sticky='w')
        self._scale = ttk.Scale(
            self, from_=0, to=1, orient='horizontal', variable=self._position,
            command=self._on_scale_changed,
        )
        self._scale.grid(row=0, column=2, sticky='ew', padx=(8, 10), pady=4)
        self.columnconfigure(2, weight=1)
        self._scale.bind('<ButtonPress-1>', self._begin_seek)
        self._scale.bind('<ButtonRelease-1>', self._finish_seek)
        self._speed_combo = ttk.Combobox(
            self, textvariable=self._speed, values=SPEED_OPTIONS,
            state='readonly', width=6,
        )
        self._speed_combo.grid(row=0, column=3, padx=(0, 8), pady=4)
        self._speed_combo.bind('<<ComboboxSelected>>', self._change_speed)
        self._export_button = ttk.Button(self, text='导出视频', command=on_export)
        self._export_button.grid(row=0, column=4, padx=(0, 8), pady=4)
        self.set_enabled(False)

    @staticmethod
    def _format_time(seconds):
        seconds = max(0, int(seconds))
        return f'{seconds // 60:02d}:{seconds % 60:02d}'

    def _toggle_play(self):
        if self._on_toggle_play:
            self._on_toggle_play()

    def _begin_seek(self, event=None):
        if not self._enabled:
            return
        self._seeking = True
        if self._on_seek_start:
            self._on_seek_start()

    def _finish_seek(self, event=None):
        if not self._seeking:
            return
        self._seeking = False
        # 即使没有移动滑块，也要结束定位并恢复拖动前的播放/暂停状态。
        if self._on_seek:
            self._on_seek(float(self._position.get()))
        # 继续执行 ttk 的 Release 类绑定，使轨道点击的重复定时器正常停止。

    def _change_speed(self, event=None):
        if self._enabled and self._on_speed_changed:
            self._on_speed_changed(float(self._speed.get().removesuffix('x')))

    def _on_scale_changed(self, value):
        self._update_time(float(value))

    def _update_time(self, position):
        self._time_label.configure(
            text=f'{self._format_time(position)} / {self._format_time(self._duration)}'
        )

    def set_duration(self, duration):
        self._duration = max(0.0, float(duration))
        self._scale.configure(to=max(self._duration, 0.001))
        self.set_position(0.0)
        self.set_enabled(self._duration > 0)

    def set_position(self, position):
        position = max(0.0, min(float(position), self._duration))
        if self._seeking:
            return
        self._position.set(position)
        self._update_time(position)

    def get_position(self):
        return float(self._position.get())

    def set_playing(self, playing):
        self._play_button.configure(text='暂停' if playing else '播放')

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        if not enabled:
            self._seeking = False
        state = 'normal' if enabled else 'disabled'
        self._play_button.configure(state=state)
        self._scale.configure(state=state)
        self._speed_combo.configure(state='readonly' if enabled else 'disabled')
        self.set_export_busy(self._export_busy)

    def set_export_busy(self, busy):
        self._export_busy = bool(busy)
        self._export_button.configure(
            state='normal' if self._enabled and not self._export_busy else 'disabled'
        )

    def set_speed(self, speed):
        self._speed.set(f'{speed:g}x')

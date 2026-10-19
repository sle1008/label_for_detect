"""视频播放与进度控制组件。"""

import tkinter as tk
from tkinter import ttk


class VideoControls(ttk.Frame):
    """提供播放控制、时间显示和可拖动的视频进度条。"""

    def __init__(self, parent, on_toggle_play=None, on_seek=None):
        super().__init__(parent)
        self._on_toggle_play = on_toggle_play
        self._on_seek = on_seek
        self._duration = 0.0
        self._seeking = False
        self._seek_changed = False
        self._position = tk.DoubleVar(value=0.0)

        self.columnconfigure(1, weight=1)
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
        self.set_enabled(False)

    @staticmethod
    def _format_time(seconds):
        seconds = max(0, int(seconds))
        return f'{seconds // 60:02d}:{seconds % 60:02d}'

    def _toggle_play(self):
        if self._on_toggle_play:
            self._on_toggle_play()

    def _begin_seek(self, event=None):
        self._seeking = True
        self._seek_changed = False

    def _finish_seek(self, event=None):
        self._seeking = False
        if self._seek_changed and self._on_seek:
            return self._on_seek(float(self._position.get()))

    def _on_scale_changed(self, value):
        self._update_time(float(value))
        if self._seeking:
            self._seek_changed = True

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
        if not self._seeking:
            self._position.set(position)
        self._update_time(position)

    def get_position(self):
        return float(self._position.get())

    def set_playing(self, playing):
        self._play_button.configure(text='暂停' if playing else '播放')

    def set_enabled(self, enabled):
        state = 'normal' if enabled else 'disabled'
        self._play_button.configure(state=state)
        self._scale.configure(state=state)

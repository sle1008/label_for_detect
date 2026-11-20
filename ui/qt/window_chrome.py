"""同步 Qt 窗口的 Windows 原生标题栏颜色。"""

import sys

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

# ==================== 原生窗口参数设置区 ====================
WINDOWS_11_FIRST_BUILD = 22000
DWM_CAPTION_COLOR = 35
DWM_TEXT_COLOR = 36


def sync_title_bars(application, background, foreground):
    """在支持原生标题栏配色的系统上更新已打开和后续打开的窗口。"""
    if (sys.platform != 'win32' or application.platformName() != 'windows'
            or sys.getwindowsversion().build < WINDOWS_11_FIRST_BUILD):
        return
    chrome = getattr(application, '_annotation_window_chrome', None)
    if chrome is None:
        try:
            chrome = _WindowChrome(application)
        except (OSError, AttributeError):
            return
        application._annotation_window_chrome = chrome
        application.installEventFilter(chrome)
    chrome.set_colors(background, foreground)
    for widget in application.topLevelWidgets():
        if widget.isVisible():
            chrome.apply(widget)


class _WindowChrome(QObject):
    def __init__(self, application):
        super().__init__(application)
        from ctypes import WinDLL, byref, sizeof, wintypes

        self._set_attribute = WinDLL('dwmapi').DwmSetWindowAttribute
        self._set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
        self._set_attribute.restype = wintypes.LONG
        self._byref, self._sizeof, self._dword = byref, sizeof, wintypes.DWORD

    def set_colors(self, background, foreground):
        self._colors = []
        for attribute, name in ((DWM_CAPTION_COLOR, background), (DWM_TEXT_COLOR, foreground)):
            color = QColor(name)
            # DWM 使用 COLORREF，低字节为红色，不含透明度。
            value = color.red() | (color.green() << 8) | (color.blue() << 16)
            self._colors.append((attribute, self._dword(value)))

    def apply(self, widget):
        flags = widget.windowFlags()
        if (not widget.isWindow() or not flags & Qt.WindowType.WindowTitleHint
                or flags & Qt.WindowType.FramelessWindowHint):
            return
        handle = widget.windowHandle()
        if handle is None:
            return
        for attribute, value in self._colors:
            self._set_attribute(handle.winId(), attribute, self._byref(value), self._sizeof(value))

    def eventFilter(self, watched, event):
        # 显示时已有原生句柄；不提前为隐藏窗口创建系统资源。
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget) and watched.isWindow():
            self.apply(watched)
        return False

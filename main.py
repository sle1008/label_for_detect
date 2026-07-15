"""Annotation Tool - Main Entry Point."""

import sys
import os
import ctypes
import argparse
from pathlib import Path

# Suppress libpng iCCP warnings from PNGs with bad embedded profiles (harmless).
class _StderrFilter:
    __slots__ = ('_stream',)

    def __init__(self, stream):
        self._stream = stream

    def write(self, data):
        if 'iCCP: known incorrect sRGB profile' not in data:
            self._stream.write(data)

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


sys.stderr = _StderrFilter(sys.stderr)

# Enable high-DPI awareness on Windows (must be before Tk init)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Add project root to path
from utils.paths import get_app_root

PROJECT_ROOT = get_app_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.app import AnnotationApp


_SINGLE_INSTANCE_MUTEX_NAME = r'Local\AnnotationTool.SingleInstance.6F7D94A1'
_ERROR_ALREADY_EXISTS = 183


def _acquire_single_instance(mutex_name: str = _SINGLE_INSTANCE_MUTEX_NAME):
    """Return (mutex_handle, already_running) for the current Windows session."""
    if sys.platform != 'win32':
        return None, False

    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.GetLastError.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        return None, False
    return handle, kernel32.GetLastError() == _ERROR_ALREADY_EXISTS


def _release_single_instance(handle):
    if handle and sys.platform == 'win32':
        ctypes.windll.kernel32.CloseHandle(handle)


def _show_already_running_message():
    """Show a native prompt before any second Tk window is created."""
    from ctypes import wintypes

    message = '标注工具已经在运行。\n\n请先关闭当前软件窗口，再重新打开。'
    title = '软件已在运行'
    mb_ok = 0x00000000
    mb_icon_information = 0x00000040
    mb_set_foreground = 0x00010000
    mb_topmost = 0x00040000
    user32 = ctypes.windll.user32
    user32.MessageBoxW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT,
    ]
    user32.MessageBoxW.restype = ctypes.c_int
    user32.MessageBoxW(
        None,
        message,
        title,
        mb_ok | mb_icon_information | mb_set_foreground | mb_topmost,
    )


def main():
    mutex_handle, already_running = _acquire_single_instance()
    if already_running:
        _release_single_instance(mutex_handle)
        _show_already_running_message()
        return

    parser = argparse.ArgumentParser(description='目标检测标注工具')
    parser.add_argument('--dir', type=str, help='初始图片目录')
    parser.add_argument('--labels', type=str, help='标签文件路径')
    parser.add_argument('--weights', type=str, help='预标注模型 (.pt/.onnx/.engine/.trt)')
    args = parser.parse_args()

    try:
        app = AnnotationApp()

        # Apply command-line overrides
        if args.dir:
            app._load_directory(args.dir)
        if args.labels:
            ext = Path(args.labels).suffix.lower()
            if ext == '.txt':
                app._label_manager.load_from_txt(args.labels)
            elif ext in ('.yaml', '.yml'):
                app._label_manager.load_from_yaml(args.labels)
            app._label_panel.refresh()
        if args.weights:
            app._pre_annotator.load_weights(args.weights)

        app.mainloop()
    finally:
        _release_single_instance(mutex_handle)


if __name__ == '__main__':
    main()

"""Annotation Tool - Main Entry Point."""

import sys
import os
import ctypes
import argparse
import importlib.util
from pathlib import Path

# ==================== 启动参数设置区 ====================
_SINGLE_INSTANCE_MUTEX_NAME = r'Local\AnnotationTool.SingleInstance.6F7D94A1'
_ERROR_ALREADY_EXISTS = 183
_STARTUP_DEPENDENCIES = (
    ('PySide6-Essentials', 'PySide6'),
    ('Pillow', 'PIL'),
    ('opencv-python', 'cv2'),
    ('imageio-ffmpeg', 'imageio_ffmpeg'),
    ('PyYAML', 'yaml'),
    ('onnxruntime', 'onnxruntime'),
    ('ultralytics', 'ultralytics'),
)

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


if sys.stderr is not None:
    sys.stderr = _StderrFilter(sys.stderr)

# Add project root to path
from utils.paths import get_app_root

PROJECT_ROOT = get_app_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


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


def _find_missing_dependencies():
    """Return required packages that are not importable in the current Python."""
    missing = []
    for package_name, module_name in _STARTUP_DEPENDENCIES:
        try:
            available = importlib.util.find_spec(module_name) is not None
        except (ImportError, ValueError):
            available = False
        if not available:
            missing.append(package_name)
    return missing


def _show_missing_dependencies_message(missing):
    message = (
        f"缺少依赖：{'、'.join(missing)}\n\n"
        '请在项目目录运行：\n'
        'python -m pip install -r requirements.txt'
    )
    if sys.platform == 'win32':
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.MessageBoxW.argtypes = [
            wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT,
        ]
        user32.MessageBoxW.restype = ctypes.c_int
        user32.MessageBoxW(
            None,
            message,
            '运行环境不完整',
            0x00000000 | 0x00000010 | 0x00010000 | 0x00040000,
        )
    else:
        print(message, file=sys.stderr)


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
    missing_dependencies = _find_missing_dependencies()
    if missing_dependencies:
        _show_missing_dependencies_message(missing_dependencies)
        return

    mutex_handle, already_running = _acquire_single_instance()
    if already_running:
        _release_single_instance(mutex_handle)
        _show_already_running_message()
        return

    parser = argparse.ArgumentParser(description='目标检测标注工具')
    parser.add_argument('--dir', type=str, help='初始图片目录')
    parser.add_argument('--labels', type=str, help='标签文件路径')
    parser.add_argument('--weights', type=str, help='预标注模型 (.pt/.onnx/.engine/.trt)')
    appearance = parser.add_mutually_exclusive_group()
    appearance.add_argument('--legacy-ui', action='store_true', help='使用稳定版 Tk 兼容界面')
    appearance.add_argument('--modern-ui', action='store_true', help='使用现代 Qt 界面')
    args = parser.parse_args()

    from core.config import ConfigManager
    backend = ConfigManager().load().ui_backend
    if args.legacy_ui:
        backend = 'legacy'
    elif args.modern_ui:
        backend = 'modern'

    try:
        first_window = True
        while True:
            # 每次只运行一种窗口事件循环，单实例锁在界面切换期间持续保留。
            if backend == 'legacy':
                from ui.app import AnnotationApp
            else:
                from ui.qt.app import AnnotationApp
            app = AnnotationApp()

            # 命令行只在首次启动覆盖会话，切换界面后恢复刚保存的目录与位置。
            if first_window:
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
                    app._defer_weights_load_until_images_ready(args.weights)
            first_window = False
            app.mainloop()
            requested = getattr(app, '_requested_ui', None)
            if requested not in ('modern', 'legacy'):
                break
            if backend != 'legacy':
                from PySide6.QtCore import QCoreApplication, QEvent
                app.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            backend = requested
    finally:
        _release_single_instance(mutex_handle)


if __name__ == '__main__':
    main()

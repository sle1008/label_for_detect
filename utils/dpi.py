"""为不同桌面框架设置原生 DPI 上下文，避免 Windows 对窗口进行位图放大。"""

import ctypes
import sys

# ==================== DPI 参数设置区 ====================
# Tk 沿用系统 DPI；Qt 使用每屏 DPI。窗口会继承创建线程的上下文。
UI_DPI_CONTEXTS = {'legacy': -2, 'modern': -4}
UI_DPI_AWARENESS = {'legacy': 1, 'modern': 2}


def configure_ui_dpi(backend):
    """在创建窗口前调用；线程级设置允许两种框架在同一进程中反复切换。"""
    if sys.platform != 'win32':
        return
    context = UI_DPI_CONTEXTS[backend]
    try:
        setter = ctypes.windll.user32.SetThreadDpiAwarenessContext
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        if setter(ctypes.c_void_p(context)):
            return
    except (AttributeError, OSError):
        pass
    # 旧版 Windows 不提供线程级接口，回退到进程级 DPI 感知。
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(UI_DPI_AWARENESS[backend])
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass

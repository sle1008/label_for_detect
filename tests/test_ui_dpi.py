"""在独立进程中验证 Windows DPI，隔离 Qt/Tk 及测试运行器的全局状态。"""

import json
from pathlib import Path
import subprocess
import sys
import unittest

# ==================== DPI 回归参数设置区 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROBE_TIMEOUT_SECONDS = 45
DPI_PROBE = r'''
import ctypes
import json
import sys
import tempfile
from unittest.mock import patch
from core.config import ConfigManager
from ui.app import AnnotationApp as LegacyApp

user32 = ctypes.windll.user32
user32.GetWindowDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
states = []

def record(backend, handle):
    context = user32.GetWindowDpiAwarenessContext(handle)
    states.append({'backend': backend,
                   'awareness': user32.GetAwarenessFromDpiAwarenessContext(context),
                   'dpi': user32.GetDpiForWindow(handle)})

with tempfile.TemporaryDirectory() as directory:
    config = ConfigManager(directory)
    if sys.argv[1] == 'switch':
        from PySide6.QtCore import QTimer, QCoreApplication, QEvent
        from ui.qt.app import AnnotationApp as ModernApp, ensure_application
        runtime = ensure_application()
        modern = ModernApp(restore_session=False, config_directory=directory)
        modern.show()
        def enter_legacy():
            record('modern', int(modern.winId()))
            modern._request_ui_switch()
        QTimer.singleShot(30, enter_legacy)
        runtime.exec()
        assert modern._requested_ui == 'legacy'
        modern.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    with patch('ui.app.ConfigManager', return_value=config), patch.object(LegacyApp, '_restore_session'):
        legacy = LegacyApp()
        legacy.withdraw()
        def finish_legacy():
            record('legacy', legacy.winfo_id())
            legacy._request_ui_switch()
        legacy.after(30, finish_legacy)
        legacy.mainloop()
        assert legacy._requested_ui == 'modern'

    if sys.argv[1] == 'switch':
        modern = ModernApp(restore_session=False, config_directory=directory)
        modern.show()
        def finish_modern():
            record('modern', int(modern.winId()))
            modern.close()
        QTimer.singleShot(30, finish_modern)
        runtime.exec()

print(json.dumps({'states': states, 'system_dpi': user32.GetDpiForSystem()}))
'''


@unittest.skipUnless(sys.platform == 'win32', '仅 Windows 使用 DPI 感知上下文')
class UiDpiTests(unittest.TestCase):
    def probe(self, scenario):
        result = subprocess.run(
            [sys.executable, '-c', DPI_PROBE, scenario], cwd=PROJECT_ROOT,
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_legacy_startup_uses_system_dpi_without_cli_flag(self):
        result = self.probe('legacy')
        self.assertEqual(result['states'], [
            {'backend': 'legacy', 'awareness': 1, 'dpi': result['system_dpi']},
        ])

    def test_modern_legacy_modern_event_loops_keep_native_dpi(self):
        result = self.probe('switch')
        self.assertEqual([(s['backend'], s['awareness']) for s in result['states']],
                         [('modern', 2), ('legacy', 1), ('modern', 2)])
        self.assertEqual(result['states'][1]['dpi'], result['system_dpi'])
        self.assertTrue(all(s['dpi'] > 0 for s in result['states']))


if __name__ == '__main__':
    unittest.main()

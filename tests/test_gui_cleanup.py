"""在独立进程内验证原生 GUI 资源退出和后台垃圾回收的线程边界。"""

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

# ==================== GUI 退出回归参数设置区 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
GUI_SHUTDOWN_TIMEOUT = 120


class GuiCleanupTests(unittest.TestCase):
    def test_closed_qt_and_tk_windows_survive_worker_gc_and_process_exit(self):
        script = textwrap.dedent('''
            import gc
            import os
            import threading
            import unittest

            os.environ['QT_FORCE_STDERR_LOGGING'] = '1'
            gc.disable()

            def select_qt_case(suite):
                for test in suite:
                    if isinstance(test, unittest.TestSuite):
                        yield from select_qt_case(test)
                    elif test.id().endswith('test_directory_auto_labels_and_large_virtual_list'):
                        yield test

            loader = unittest.defaultTestLoader
            suite = unittest.TestSuite(select_qt_case(loader.discover('tests', pattern='test_qt_ui.py')))
            suite.addTests(loader.discover('tests', pattern='test_video.py'))
            result = unittest.TextTestRunner(verbosity=0).run(suite)
            success = result.wasSuccessful()
            del suite, result

            from PySide6.QtWidgets import QApplication
            if QApplication.topLevelWidgets():
                raise AssertionError('Qt windows remain after cleanup')

            # 主线程关闭窗口后，再强制后台 GC，避免把原生资源延迟回收问题遗漏到退出。
            worker = threading.Thread(target=gc.collect)
            worker.start()
            worker.join()
            print('GUI cleanup and worker GC completed', flush=True)
            raise SystemExit(0 if success else 1)
        ''')
        completed = subprocess.run(
            [sys.executable, '-X', 'faulthandler', '-c', script], cwd=PROJECT_ROOT,
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=GUI_SHUTDOWN_TIMEOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
        )
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn('GUI cleanup and worker GC completed', output)
        self.assertNotIn('Tcl_AsyncDelete', output)
        self.assertNotIn('main thread is not in main loop', output)


if __name__ == '__main__':
    unittest.main()

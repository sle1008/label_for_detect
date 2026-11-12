"""启动前依赖检查的回归测试。"""

import sys
import unittest
from unittest.mock import patch

import main


class StartupDependencyTests(unittest.TestCase):
    def test_missing_dependencies_stop_startup_and_show_message(self):
        missing = ['PySide6-Essentials', 'Pillow']
        with (
            patch.object(sys, 'argv', ['main.py']),
            patch.object(main, '_find_missing_dependencies', return_value=missing),
            patch.object(main, '_show_missing_dependencies_message') as show_message,
            patch.object(main, '_acquire_single_instance') as acquire_instance,
        ):
            main.main()

        show_message.assert_called_once_with(missing)
        acquire_instance.assert_not_called()

    def test_complete_environment_continues_to_existing_startup_flow(self):
        with (
            patch.object(sys, 'argv', ['main.py']),
            patch.object(main, '_find_missing_dependencies', return_value=[]),
            patch.object(main, '_acquire_single_instance', return_value=(None, True)) as acquire_instance,
            patch.object(main, '_show_already_running_message') as show_message,
        ):
            main.main()

        acquire_instance.assert_called_once_with()
        show_message.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()

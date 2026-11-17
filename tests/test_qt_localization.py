"""隔离验证 Qt 标准控件译文，避免影响其他测试使用的 QApplication。"""

import json
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

# ==================== 标准界面翻译回归参数设置区 ====================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRANSLATION_CHECK_TIMEOUT_SECONDS = 15
RUNTIME_PREAMBLE = '''
import gc
import json
import tempfile
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QLocale
from ui.qt.localization import install_chinese_translations
application = QCoreApplication([])
'''


class QtLocalizationTests(unittest.TestCase):
    def check_in_core_runtime(self, source):
        # 子进程只创建核心应用；主测试进程仍可正常初始化完整图形应用。
        completed = subprocess.run(
            [sys.executable, '-c', RUNTIME_PREAMBLE + textwrap.dedent(source)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=TRANSLATION_CHECK_TIMEOUT_SECONDS,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_bundled_catalog_covers_dialog_model_menus_and_confirmations(self):
        result = self.check_in_core_runtime('''
            translators = install_chinese_translations(application)
            cases = [
                ('QFileDialog', 'File &name:', '文件'),
                ('QFileDialog', '&Rename', '重命名'),
                ('QFileDialog', '&Delete', '删除'),
                ('QFileDialog', "Are you sure you want to delete '%1'?", '删除'),
                ('QFileDialog', '%1 already exists.\\nDo you want to replace it?', '替换'),
                ('QFileSystemModel', 'My Computer', '电脑'),
                ('QFileSystemModel', 'Date Modified', '修改'),
                ('QLineEdit', 'Cu&t', '剪切'),
                ('QLineEdit', '&Copy', '复制'),
                ('QLineEdit', '&Paste', '粘贴'),
                ('QLineEdit', 'Select All', '全选'),
                ('QMessageBox', 'Show Details...', '显示'),
                ('QPlatformTheme', 'Cancel', '取消'),
                ('QPlatformTheme', '&Yes', '是'),
                ('QPlatformTheme', '&No', '否'),
            ]
            translations = []
            for context, source, keyword in cases:
                translated = application.translate(context, source)
                assert keyword in translated, (context, source, translated)
                if '%1' in source:
                    assert '%1' in translated
                translations.append(translated)
            print(json.dumps({'catalog_count': len(translators), 'checked': len(translations)}))
        ''')
        self.assertEqual(result['catalog_count'], 2)
        self.assertEqual(result['checked'], 15)

    def test_missing_catalog_retains_standard_chinese_and_template_arguments(self):
        result = self.check_in_core_runtime('''
            with tempfile.TemporaryDirectory() as empty_directory:
                with patch('ui.qt.localization.QLibraryInfo.path', return_value=empty_directory):
                    translators = install_chinese_translations(application)
                cases = [
                    ('QFileDialog', '&Look in:', '查找'),
                    ('QFileDialog', 'Files of &type:', '文件类型'),
                    ('QFileDialog', 'Show &hidden files', '隐藏'),
                    ('QFileSystemModel', 'My Computer', '电脑'),
                    ('QFileSystemModel', 'Name', '名称'),
                    ('QLineEdit', '&Undo', '撤销'),
                    ('QLineEdit', '&Redo', '重做'),
                    ('QLineEdit', '&Paste', '粘贴'),
                    ('QMessageBox', 'Hide Details...', '隐藏'),
                    ('QPlatformTheme', 'Cancel', '取消'),
                    ('QPlatformTheme', '&Yes', '是'),
                    ('QPlatformTheme', '&No', '否'),
                ]
                for context, source, keyword in cases:
                    translated = application.translate(context, source)
                    assert keyword in translated, (context, source, translated)
                source = "'%1' is write protected.\\nDo you want to delete it anyway?"
                translated = application.translate('QFileDialog', source)
                assert '%1' in translated and '\\n' in translated and '删除' in translated
                print(json.dumps({'catalog_count': len(translators), 'checked': len(cases)}))
        ''')
        self.assertEqual(result['catalog_count'], 1)
        self.assertEqual(result['checked'], 12)

    def test_new_qt_mnemonic_variants_are_translated(self):
        result = self.check_in_core_runtime('''
            install_chinese_translations(application)
            values = [application.translate('QFileDialog', '&Look in:'),
                      application.translate('QFileDialog', 'Files of &type:')]
            assert '查找' in values[0] and '&L' in values[0]
            assert '文件类型' in values[1] and '&T' in values[1]
            print(json.dumps({'checked': len(values)}))
        ''')
        self.assertEqual(result['checked'], 2)

    def test_install_is_idempotent_persistent_and_preserves_locale_and_business_text(self):
        result = self.check_in_core_runtime('''
            before = QLocale().name()
            installed = install_chinese_translations(application)
            identities = [id(translator) for translator in installed]
            assert all(translator.parent() is application for translator in installed)
            del installed
            gc.collect()
            repeated = install_chinese_translations(application)
            assert [id(translator) for translator in repeated] == identities
            assert QLocale().name() == before
            assert application.translate('ApplicationController', 'Delete') == 'Delete'
            assert application.translate('QFileDialog', '自定义业务内容') == '自定义业务内容'
            assert '粘贴' in application.translate('QLineEdit', '&Paste')
            official = repeated[-1].translate('QMessageBox', 'Show Details...')
            assert official
            assert application.translate('QMessageBox', 'Show Details...') == official
            print(json.dumps({'locale_preserved': True, 'translator_count': len(repeated)}))
        ''')
        self.assertTrue(result['locale_preserved'])
        self.assertEqual(result['translator_count'], 2)

    def test_legacy_catalog_is_loaded_when_primary_catalog_is_unavailable(self):
        result = self.check_in_core_runtime('''
            with patch('ui.qt.localization.QT_TRANSLATION_CATALOGS', ('absent_catalog', 'qt')):
                installed = install_chinese_translations(application)
            assert len(installed) == 2
            assert installed[-1].filePath().endswith('qt_zh_CN.qm')
            assert '复制' in application.translate('QLineEdit', '&Copy')
            print(json.dumps({'legacy_catalog_loaded': True}))
        ''')
        self.assertTrue(result['legacy_catalog_loaded'])


if __name__ == '__main__':
    unittest.main()

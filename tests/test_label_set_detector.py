"""标签集自动识别、历史恢复和手动浏览默认路径回归测试。"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

# ==================== 测试参数设置区 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
VALID_INDEXED_LABELS = '0: bird\n1: african buffalo\n'
COORDINATE_LABELS = '0 0.5 0.5 0.2 0.2\n1 0.3 0.4 0.1 0.1\n'

from core.config import AppConfig
from core.label_manager import LabelManager
from io_ops.label_set_detector import iter_label_set_candidates, read_label_set_txt
from ui.app import AnnotationApp


class LabelFileFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, name, content):
        path = self.root / name
        path.write_text(content, encoding='utf-8')
        return path


class LabelSetDetectorTests(LabelFileFixture):
    def test_standard_filename_precedes_other_named_and_content_candidates(self):
        self.write('aaa.txt', VALID_INDEXED_LABELS)
        self.write('my_classes.txt', '0: cat\n')
        self.write('class.TXT', '0: bear\n')
        self.write('classes.txt', '0: bird\n')
        names = [path.name for path, _ in iter_label_set_candidates(self.root)]
        self.assertEqual(names, ['classes.txt', 'class.TXT', 'my_classes.txt', 'aaa.txt'])

    def test_named_file_supports_plain_names_bom_comments_and_multiword_names(self):
        self.write('Animal_Classes.TXT', '\ufeff# classes\nbird\n\nafrican buffalo\n')
        candidates = list(iter_label_set_candidates(self.root))
        self.assertEqual(len(candidates), 1)
        self.assertEqual([(row['class_id'], row['name']) for row in candidates[0][1]],
                         [(0, 'bird'), (1, 'african buffalo')])

    def test_unnamed_file_requires_two_indexed_entries(self):
        self.write('one.txt', '0: cat\n\n# comment\n')
        self.write('plain.txt', 'cat\ndog\n')
        self.write('valid.txt', VALID_INDEXED_LABELS)
        self.assertEqual([p.name for p, _ in iter_label_set_candidates(self.root)], ['valid.txt'])

    def test_coordinates_and_mixed_files_are_never_candidates(self):
        contents = [
            COORDINATE_LABELS,
            '0: 0.5 0.5 0.2 0.2\n1: 0.1 0.1 0.3 0.3\n',
            '0,10,20,30,40\n1,20,30,40,50\n',
            VALID_INDEXED_LABELS + COORDINATE_LABELS,
            'cat\n' + COORDINATE_LABELS,
            '0: cat\n0: dog\n',
            '0: cat\n1: \n',
            '0: cat\nnot an indexed entry\n',
            '0 0.1 0.2 0.3 0.4 0.5 0.6\n',
        ]
        for content in contents:
            with self.subTest(content=content):
                path = self.write('classes.txt', content)
                self.assertIsNone(read_label_set_txt(path))
                self.assertEqual(list(iter_label_set_candidates(self.root)), [])

    def test_invalid_named_file_does_not_hide_valid_content_file(self):
        self.write('classes.txt', COORDINATE_LABELS)
        self.write('dataset.txt', VALID_INDEXED_LABELS)
        self.assertEqual([p.name for p, _ in iter_label_set_candidates(self.root)], ['dataset.txt'])

    def test_unreadable_empty_and_bad_encoding_are_skipped(self):
        self.write('classes.txt', '# empty\n\n')
        (self.root / 'class.txt').write_bytes(b'\xff\xfe\x00')
        self.assertEqual(list(iter_label_set_candidates(self.root)), [])
        self.assertIsNone(read_label_set_txt(self.root / 'missing.txt'))
        self.assertEqual(list(iter_label_set_candidates(self.root / 'missing')), [])

    def test_scan_stays_in_opened_directory(self):
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / 'classes.txt').write_text(VALID_INDEXED_LABELS, encoding='utf-8')
        self.assertEqual(list(iter_label_set_candidates(self.root)), [])

    def test_indexed_thresholds_are_preserved(self):
        path = self.write('classes.txt', '3: african buffalo | 0.75\n8: bird | 0.3\n')
        rows = read_label_set_txt(path)
        self.assertEqual([(row['class_id'], row['threshold']) for row in rows], [(3, 0.75), (8, 0.3)])


class AutomaticLabelImportTests(LabelFileFixture):
    def make_app(self, directory, last_file=''):
        app = SimpleNamespace(
            _config=AppConfig(last_label_file=str(last_file)),
            _project=SimpleNamespace(image_dir=directory, current_image=None),
            _label_manager=LabelManager(),
            _label_panel=SimpleNamespace(refresh=Mock()),
            _refresh_label_filter_options=Mock(),
            _status_bar=Mock(),
        )
        for name in ('_try_import_remembered_label_file', '_try_import_root_classes_file',
                     '_apply_detected_label_set', '_replace_labels_from_file',
                     '_last_label_file_directory'):
            setattr(app, name, MethodType(getattr(AnnotationApp, name), app))
        return app

    def test_manual_binding_wins_when_returning_to_directory(self):
        manual = self.write('manual.yaml', 'names: [manual_bird, manual_cat]\n')
        self.write('classes.txt', VALID_INDEXED_LABELS)
        app = self.make_app(self.root)
        app._config.remember_directory_label_file(str(self.root), str(manual))
        AnnotationApp._try_auto_folder_labels(app, str(self.root))
        self.assertEqual(app._config.last_label_file, str(manual))
        self.assertEqual(app._label_manager.get_name(0), 'manual_bird')

    def test_missing_manual_binding_keeps_existing_protection(self):
        self.write('classes.txt', VALID_INDEXED_LABELS)
        app = self.make_app(self.root)
        app._label_manager.add_label('existing')
        app._config.remember_directory_label_file(str(self.root), str(self.root / 'missing.txt'))
        with patch('ui.app.showwarning') as warning:
            AnnotationApp._try_auto_folder_labels(app, str(self.root))
        warning.assert_called_once()
        self.assertEqual(app._label_manager.get_name(0), 'existing')

    def test_new_directory_local_file_wins_over_last_file_and_other_binding(self):
        previous = self.write('previous.txt', '0: previous\n')
        local = self.write('classes.txt', VALID_INDEXED_LABELS)
        app = self.make_app(self.root, previous)
        app._config.remember_directory_label_file(str(self.root / 'other'), str(previous))
        AnnotationApp._try_auto_folder_labels(app, str(self.root))
        self.assertEqual(app._config.last_label_file, str(local))
        self.assertEqual(app._label_manager.get_name(0), 'bird')

    def test_new_directory_uses_content_file_before_history(self):
        previous = self.write('previous.txt', '0: previous\n')
        dataset = self.write('dataset.txt', VALID_INDEXED_LABELS)
        app = self.make_app(self.root, previous)
        AnnotationApp._try_auto_folder_labels(app, str(self.root))
        self.assertEqual(app._config.last_label_file, str(dataset))

    def test_last_txt_fallback_reloads_file_instead_of_retaining_old_definitions(self):
        previous = self.write('previous.txt', '0: updated\n')
        opened = self.root / 'opened'
        opened.mkdir()
        (opened / 'sample.txt').write_text(COORDINATE_LABELS, encoding='utf-8')
        app = self.make_app(opened, previous)
        app._label_manager.add_label('stale')
        AnnotationApp._try_auto_folder_labels(app, str(opened))
        self.assertEqual(app._label_manager.get_name(0), 'updated')
        self.assertEqual(app._config.last_label_file, str(previous))

    def test_invalid_history_preserves_current_labels_and_uses_folder_detection(self):
        previous = self.write('previous.txt', COORDINATE_LABELS)
        app = self.make_app(self.root, previous)
        app._label_manager.add_label('existing')
        with patch('ui.app.detect_class_folder_layout', return_value=SimpleNamespace(detected=False)) as detect:
            AnnotationApp._try_auto_folder_labels(app, str(self.root))
        detect.assert_called_once_with(self.root)
        self.assertEqual(app._label_manager.get_name(0), 'existing')

    def test_file_dialog_still_starts_at_parent_of_image_directory(self):
        image_dir = self.root / 'images'
        image_dir.mkdir()
        app = self.make_app(image_dir, self.root / 'previous.txt')
        with patch('ui.app.filedialog.askopenfilename', return_value='') as choose:
            AnnotationApp._load_label_file_from_disk(app)
        self.assertEqual(choose.call_args.kwargs['initialdir'], str(self.root))


if __name__ == '__main__':
    unittest.main()

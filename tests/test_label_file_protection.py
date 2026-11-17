"""标签集与同名图片文件的隔离保护，所有文件均创建在临时目录。"""

import tempfile
import unittest
from pathlib import Path
from core.annotation import BBox
from core.image_item import ImageItem
from io_ops.annotation_writer import write_yolo_annotations_atomic
from io_ops.image_export import EXPORT_MODE_COPY, EXPORT_MODE_MOVE, export_images_and_labels
from io_ops.image_files import delete_annotation_files, delete_image_and_labels
from io_ops.label_set_detector import is_label_set_file, iter_label_set_candidates

# ==================== 标签文件保护回归参数设置区 ====================
INDEXED_LABEL_SET = '0: animal\n1: bird\n'
PLAIN_LABEL_SET = 'animal\nbird\n'
YOLO_ANNOTATIONS = '0 0.5 0.5 0.2 0.2\n'
IMAGE_SIZE = (100, 80)


class LabelFileProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def make_pair(self, name='classes', content=INDEXED_LABEL_SET, directory=None):
        directory = directory or self.root
        directory.mkdir(parents=True, exist_ok=True)
        image = directory / f'{name}.jpg'
        image.write_bytes(b'temporary-image-placeholder')
        labels = directory / f'{name}.txt'
        labels.write_text(content, encoding='utf-8')
        return ImageItem(image, width=IMAGE_SIZE[0], height=IMAGE_SIZE[1]), labels

    def test_guard_matches_auto_detection_for_named_and_indexed_files(self):
        contents = {
            'classes.txt': PLAIN_LABEL_SET,
            'my_class_definitions.TXT': '\ufeff# definitions\n0: animal | 0.8\n',
            'animal.txt': '0: animal\n',
            'plain.txt': PLAIN_LABEL_SET,
            'class_coordinates.txt': YOLO_ANNOTATIONS,
            'coordinates.txt': '0: animal 10 20 30 40\n',
            'invalid_classes.txt': '0: animal\n0: bird\n',
            'empty.txt': '',
            'class.yaml': '0: animal\n',
        }
        for name, content in contents.items():
            (self.root / name).write_text(content, encoding='utf-8')
        (self.root / 'class_bad_encoding.txt').write_bytes(b'\xff\xfe\x00')
        expected = {path for path, _ in iter_label_set_candidates(self.root)}
        self.assertEqual({path for path in self.root.iterdir() if is_label_set_file(path)}, expected)
        self.assertEqual({path.name for path in expected},
                         {'classes.txt', 'my_class_definitions.TXT', 'animal.txt'})
        self.assertFalse(is_label_set_file(self.root / 'missing.txt'))

    def test_detection_does_not_modify_label_set(self):
        _, labels = self.make_pair()
        before, modified = labels.read_bytes(), labels.stat().st_mtime_ns
        for _ in range(2):
            self.assertTrue(is_label_set_file(labels))
            self.assertEqual(next(iter_label_set_candidates(self.root))[0], labels)
        self.assertEqual(labels.read_bytes(), before)
        self.assertEqual(labels.stat().st_mtime_ns, modified)

    def test_delete_image_preserves_label_set_and_removes_normal_cached_annotation(self):
        item, labels = self.make_pair()
        normal = self.root / 'separate' / 'classes.txt'
        normal.parent.mkdir()
        normal.write_text(YOLO_ANNOTATIONS, encoding='utf-8')
        item._annotation_path_checked = True
        item._annotation_file_path = normal
        self.assertEqual(delete_image_and_labels(item), (True, ''))
        self.assertFalse(item.path.exists())
        self.assertFalse(normal.exists())
        self.assertEqual(labels.read_text(encoding='utf-8'), INDEXED_LABEL_SET)

    def test_delete_preserves_cached_label_set_but_removes_coordinate_sidecar(self):
        item, normal = self.make_pair('animal', YOLO_ANNOTATIONS)
        labels = self.root / 'classes.txt'
        labels.write_text(INDEXED_LABEL_SET, encoding='utf-8')
        item._annotation_path_checked = True
        item._annotation_file_path = labels
        self.assertEqual(delete_image_and_labels(item), (True, ''))
        self.assertFalse(normal.exists())
        self.assertEqual(labels.read_text(encoding='utf-8'), INDEXED_LABEL_SET)

    def test_clear_annotations_preserves_indexed_or_plain_label_sets(self):
        for name, content in (('animal', INDEXED_LABEL_SET), ('classes', PLAIN_LABEL_SET)):
            with self.subTest(name=name):
                item, labels = self.make_pair(name)
                labels.write_text(content, encoding='utf-8')
                delete_annotation_files(item.path)
                self.assertTrue(item.path.exists())
                self.assertEqual(labels.read_text(encoding='utf-8'), content)

    def test_copy_and_move_do_not_export_label_sets_as_sidecars(self):
        for mode in (EXPORT_MODE_COPY, EXPORT_MODE_MOVE):
            with self.subTest(mode=mode):
                item, labels = self.make_pair(directory=self.root / mode)
                result = export_images_and_labels([item], self.root / f'output_{mode}', mode=mode)[0]
                self.assertTrue(result.success)
                self.assertEqual(item.path.exists(), mode == EXPORT_MODE_COPY)
                self.assertTrue(result.image_destination.exists())
                self.assertIsNone(result.label_destination)
                self.assertFalse((self.root / f'output_{mode}' / 'labels' / 'classes.txt').exists())
                self.assertEqual(labels.read_text(encoding='utf-8'), INDEXED_LABEL_SET)

    def test_atomic_save_refuses_label_sets_without_touching_original_or_creating_temp(self):
        for name, content in (('animal', INDEXED_LABEL_SET), ('classes', PLAIN_LABEL_SET)):
            with self.subTest(name=name):
                _, labels = self.make_pair(name, content)
                modified = labels.stat().st_mtime_ns
                for annotations in ([], [BBox(10, 10, 30, 30, 0, 'animal')]):
                    with self.assertRaisesRegex(FileExistsError, '标签集'):
                        write_yolo_annotations_atomic(labels, annotations, *IMAGE_SIZE)
                self.assertEqual(labels.read_text(encoding='utf-8'), content)
                self.assertEqual(labels.stat().st_mtime_ns, modified)
                self.assertEqual(list(self.root.glob(f'.{labels.name}.*.tmp')), [])

    def test_coordinate_file_named_classes_keeps_normal_write_delete_and_export_behavior(self):
        for mode in ('write', 'delete', 'clear', 'move'):
            with self.subTest(mode=mode):
                item, labels = self.make_pair(content=YOLO_ANNOTATIONS, directory=self.root / mode)
                self.assertFalse(is_label_set_file(labels))
                if mode == 'write':
                    write_yolo_annotations_atomic(labels, [], *IMAGE_SIZE)
                    self.assertEqual(labels.read_text(encoding='utf-8'), '')
                    write_yolo_annotations_atomic(labels, [BBox(10, 10, 30, 30, 0, 'animal')], *IMAGE_SIZE)
                    self.assertTrue(labels.read_text(encoding='utf-8').startswith('0 '))
                elif mode == 'delete':
                    self.assertEqual(delete_image_and_labels(item), (True, ''))
                    self.assertFalse(labels.exists())
                elif mode == 'clear':
                    delete_annotation_files(item.path)
                    self.assertFalse(labels.exists())
                    self.assertTrue(item.path.exists())
                else:
                    result = export_images_and_labels([item], self.root / 'moved', mode=EXPORT_MODE_MOVE)[0]
                    self.assertTrue(result.success)
                    self.assertEqual(result.label_destination.read_text(encoding='utf-8'), YOLO_ANNOTATIONS)
                    self.assertFalse(labels.exists())


if __name__ == '__main__':
    unittest.main()

"""Core logic tests (no GUI required)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.annotation import BBox
from core.image_item import ImageItem
from core.project import Project, ImageFilter
from core.label_manager import LabelManager
from io_ops.label_file_parser import load_annotation_file
from io_ops.annotation_writer import write_yolo_annotations_atomic
from io_ops.annotation_status import infer_label_category_from_annotations, annotation_file_contains_class
from utils.geometry import yolo_to_pixel, pixel_to_yolo


class GeometryTests(unittest.TestCase):
    def test_yolo_roundtrip(self):
        w, h = 640, 480
        x1, y1, x2, y2 = 100, 50, 300, 250
        cx, cy, bw, bh = pixel_to_yolo(x1, y1, x2, y2, w, h)
        rx1, ry1, rx2, ry2 = yolo_to_pixel(cx, cy, bw, bh, w, h)
        self.assertAlmostEqual(rx1, x1, places=1)
        self.assertAlmostEqual(ry1, y1, places=1)
        self.assertAlmostEqual(rx2, x2, places=1)
        self.assertAlmostEqual(ry2, y2, places=1)

    def test_bbox_from_yolo(self):
        bbox = BBox.from_yolo(0, 'deer', 0.5, 0.5, 0.4, 0.4, 274, 184)
        self.assertGreater(bbox.x2, bbox.x1)
        self.assertGreater(bbox.y2, bbox.y1)
        self.assertLessEqual(bbox.x2, 274)
        self.assertLessEqual(bbox.y2, 184)


class AnnotationFileTests(unittest.TestCase):
    def test_load_yolo_annotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            img = root / 'sample.jpg'
            img.write_bytes(b'fake')
            txt = root / 'sample.txt'
            txt.write_text('0 0.5 0.5 0.4 0.4\n', encoding='utf-8')

            mgr = LabelManager()
            mgr.add_label('deer')
            anns = load_annotation_file(img, mgr, img_width=274, img_height=184)
            self.assertEqual(len(anns), 1)
            self.assertEqual(anns[0].class_id, 0)
            self.assertAlmostEqual(anns[0].center()[0], 137, delta=2)

    def test_load_yolo_annotation_for_class_subfolder_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            img_dir = root / 'test' / 'images' / 'african buffalo'
            label_dir = root / 'test' / 'labels'
            img_dir.mkdir(parents=True)
            label_dir.mkdir(parents=True)
            img = img_dir / 'african buffalo_00007_943f6b4af4.jpg'
            img.write_bytes(b'fake')
            label = label_dir / 'african buffalo_00007_943f6b4af4.txt'
            label.write_text('0 0.5 0.5 0.4 0.4\n', encoding='utf-8')

            mgr = LabelManager()
            mgr.add_label('bear')
            anns = load_annotation_file(img, mgr, img_width=100, img_height=100)
            self.assertEqual(len(anns), 1)
            self.assertEqual(anns[0].class_id, 0)

    def test_infer_dominant_label_category(self):
        self.assertEqual(
            infer_label_category_from_annotations(['deer']),
            'deer',
        )
        self.assertEqual(
            infer_label_category_from_annotations(['deer', 'pig', 'pig']),
            'pig',
        )
        self.assertEqual(
            infer_label_category_from_annotations(['deer', 'pig']),
            'deer',
        )

    def test_annotation_file_contains_class_reads_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            img = root / 'image.jpg'
            txt = root / 'image.txt'
            img.write_bytes(b'fake')
            txt.write_text('1 0.5 0.5 0.2 0.2\n', encoding='utf-8')
            item = ImageItem(path=img)

            self.assertTrue(annotation_file_contains_class(item, 1))
            self.assertFalse(annotation_file_contains_class(item, 0))

    def test_atomic_yolo_save_replaces_complete_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sample.txt'
            path.write_text('old incomplete content', encoding='utf-8')
            annotations = [
                BBox(x1=10, y1=20, x2=50, y2=80, class_id=3, class_name='deer'),
            ]

            write_yolo_annotations_atomic(path, annotations, 100, 100)

            self.assertEqual(path.read_text(encoding='utf-8'), annotations[0].to_yolo(100, 100) + '\n')
            self.assertEqual(list(Path(tmp).glob('*.tmp')), [])

    def test_atomic_yolo_save_preserves_old_file_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sample.txt'
            path.write_text('previous complete file\n', encoding='utf-8')
            annotations = [
                BBox(x1=10, y1=20, x2=50, y2=80, class_id=3, class_name='deer'),
            ]

            with patch('io_ops.annotation_writer.os.replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    write_yolo_annotations_atomic(path, annotations, 100, 100)

            self.assertEqual(path.read_text(encoding='utf-8'), 'previous complete file\n')
            self.assertEqual(list(Path(tmp).glob('*.tmp')), [])


class ProjectScanTests(unittest.TestCase):
    def test_scan_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a.jpg').write_bytes(b'1')
            (root / 'b.png').write_bytes(b'2')
            sub = root / 'train'
            sub.mkdir()
            (sub / 'c.jpg').write_bytes(b'3')

            paths = Project.scan_image_paths(str(root))
            self.assertEqual(len(paths), 3)


from io_ops.folder_labels import detect_class_folder_layout


class FolderLabelDetectionTests(unittest.TestCase):
    def _make_class_dataset(self, root: Path, classes: dict):
        for name, count in classes.items():
            folder = root / name
            folder.mkdir(parents=True, exist_ok=True)
            for i in range(count):
                (folder / f'{name}_{i}.jpg').write_bytes(b'x')

    def test_detects_class_per_folder_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_class_dataset(root, {'antelope': 3, 'bear': 2, 'bee': 1})
            result = detect_class_folder_layout(root)
            self.assertTrue(result.detected)
            self.assertIn(result.confidence, ('high', 'medium'))
            self.assertEqual(set(result.class_names), {'antelope', 'bear', 'bee'})

    def test_rejects_train_val_split_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for split in ('train', 'val'):
                cls = root / split / 'bear'
                cls.mkdir(parents=True)
                (cls / 'a.jpg').write_bytes(b'x')
            result = detect_class_folder_layout(root)
            self.assertFalse(result.detected)


class CanvasScaleTests(unittest.TestCase):
    """Verify scale math used by the canvas coordinate transforms."""

    def test_image_to_canvas_matches_display_size(self):
        img_w, img_h = 274, 184
        canvas_w, canvas_h = 1200, 800
        scale = min(canvas_w / img_w, canvas_h / img_h) * 0.95
        offset_x = (canvas_w - img_w * scale) / 2
        offset_y = (canvas_h - img_h * scale) / 2

        display_w = int(round(img_w * scale))
        display_h = int(round(img_h * scale))

        cx2 = img_w * scale + offset_x
        cy2 = img_h * scale + offset_y
        self.assertAlmostEqual(cx2 - offset_x, display_w, delta=2)
        self.assertAlmostEqual(cy2 - offset_y, display_h, delta=2)


class ProjectFilterTests(unittest.TestCase):
    def test_filter_preserves_sort_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('a.jpg', 'b.jpg', 'c.jpg'):
                (root / name).write_bytes(b'x')
            (root / 'a.txt').write_text('0 0.5 0.5 0.2 0.2\n', encoding='utf-8')

            project = Project()
            project.set_image_paths(str(root), Project.scan_image_paths(str(root)))
            project.image_filter = ImageFilter.ANNOTATED

            indices = project.get_filtered_indices()
            self.assertEqual(indices, [0])
            self.assertEqual(project.image_list[indices[0]].name, 'a.jpg')

    def test_navigation_respects_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('a.jpg', 'b.jpg', 'c.jpg'):
                (root / name).write_bytes(b'x')
            (root / 'a.txt').write_text('0 0.5 0.5 0.2 0.2\n', encoding='utf-8')
            (root / 'c.txt').write_text('0 0.5 0.5 0.2 0.2\n', encoding='utf-8')

            project = Project()
            project.set_image_paths(str(root), Project.scan_image_paths(str(root)))
            project.image_filter = ImageFilter.ANNOTATED
            project.goto_image(0)

            self.assertTrue(project.next_image())
            self.assertEqual(project.current_index, 2)
            self.assertTrue(project.prev_image())
            self.assertEqual(project.current_index, 0)

    def test_navigation_keeps_visible_list_snapshot_after_annotation_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('a.jpg', 'b.jpg', 'c.jpg'):
                (root / name).write_bytes(b'x')

            project = Project()
            project.set_image_paths(str(root), Project.scan_image_paths(str(root)))
            project.image_filter = ImageFilter.UNANNOTATED
            project.set_visible_indices(project.get_filtered_indices())
            project.goto_image(1)
            project.image_list[1].add_annotation(
                BBox(class_id=0, class_name='deer', x1=10, y1=10, x2=50, y2=50),
            )
            project.invalidate_filter_cache()

            self.assertTrue(project.next_image())
            self.assertEqual(project.current_index, 2)
            self.assertTrue(project.prev_image())
            self.assertEqual(project.current_index, 1)

    def test_next_filtered_index_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('a.jpg', 'b.jpg', 'c.jpg', 'd.jpg'):
                (root / name).write_bytes(b'x')
            (root / 'a.txt').write_text('0 0.5 0.5 0.2 0.2\n', encoding='utf-8')
            (root / 'c.txt').write_text('0 0.5 0.5 0.2 0.2\n', encoding='utf-8')

            project = Project()
            project.set_image_paths(str(root), Project.scan_image_paths(str(root)))
            project.image_filter = ImageFilter.ANNOTATED

            self.assertEqual(project.next_filtered_index_after(0), 2)
            self.assertIsNone(project.next_filtered_index_after(2))

    def test_unannotated_keeps_current_image_while_editing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('a.jpg', 'b.jpg', 'c.jpg'):
                (root / name).write_bytes(b'x')

            project = Project()
            project.set_image_paths(str(root), Project.scan_image_paths(str(root)))
            project.image_filter = ImageFilter.UNANNOTATED
            project.goto_image(1)
            item = project.image_list[1]
            from core.annotation import BBox
            item.add_annotation(BBox(class_id=0, class_name='deer', x1=10, y1=10, x2=50, y2=50))

            self.assertEqual(project.get_filtered_indices(), [0, 1, 2])

            project.goto_image(2)
            self.assertEqual(project.get_filtered_indices(), [0, 2])

    def test_filter_cache_reuses_indices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a.jpg').write_bytes(b'x')
            (root / 'a.txt').write_text('0 0.5 0.5 0.2 0.2\n', encoding='utf-8')

            project = Project()
            project.set_image_paths(str(root), Project.scan_image_paths(str(root)))
            project.image_filter = ImageFilter.ANNOTATED
            first = project.get_filtered_indices()
            second = project.get_filtered_indices()
            self.assertIs(first, second)

    def test_remove_image_at(self):
        project = Project()
        paths = [Path(f'{i}.jpg') for i in range(3)]
        project.set_image_paths('.', paths)
        project.goto_image(1)
        project.remove_image_at(1)
        self.assertEqual(len(project.image_list), 2)
        self.assertEqual(project.current_index, 1)
        self.assertEqual(project.image_list[1].name, '2.jpg')

    def test_resolve_refresh_index_keeps_current(self):
        paths = [Path('a.jpg'), Path('b.jpg'), Path('c.jpg')]
        idx = Project.resolve_refresh_index(
            paths, paths, Path('b.jpg'), 1,
        )
        self.assertEqual(idx, 1)

    def test_resolve_refresh_index_falls_back_to_prior(self):
        prior = [Path('a.jpg'), Path('b.jpg'), Path('c.jpg')]
        new_paths = [Path('a.jpg'), Path('c.jpg')]
        idx = Project.resolve_refresh_index(
            new_paths, prior, Path('b.jpg'), 1,
        )
        self.assertEqual(idx, 0)

    def test_resolve_refresh_index_empty(self):
        idx = Project.resolve_refresh_index([], [Path('a.jpg')], Path('a.jpg'), 0)
        self.assertEqual(idx, -1)


class AnnotationStatusTests(unittest.TestCase):
    def test_detects_label_file_without_loading_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            img = root / 'deer.jpg'
            img.write_bytes(b'fake')
            (root / 'deer.txt').write_text('0 0.5 0.5 0.4 0.4\n', encoding='utf-8')

            from io_ops.annotation_status import is_image_annotated
            item = ImageItem(path=img)
            self.assertTrue(is_image_annotated(item))

    def test_in_memory_annotations_count_as_annotated(self):
        from io_ops.annotation_status import is_image_annotated
        item = ImageItem(path=Path('x.jpg'))
        item.add_annotation(BBox(x1=1, y1=1, x2=2, y2=2, class_id=0, class_name='a'))
        self.assertTrue(is_image_annotated(item))

    def test_manual_status_overrides_empty_image(self):
        from io_ops.annotation_status import get_image_category, is_image_annotated
        item = ImageItem(path=Path('a.jpg'))
        item.manual_annotation_status = 'annotated'
        self.assertTrue(is_image_annotated(item))
        self.assertEqual(get_image_category(item), 'annotated')
        item.manual_annotation_status = 'unannotated'
        self.assertFalse(is_image_annotated(item))
        self.assertEqual(get_image_category(item), 'unannotated')
        item.manual_annotation_status = 'uncertain'
        self.assertFalse(is_image_annotated(item))
        self.assertEqual(get_image_category(item), 'uncertain')

    def test_empty_label_file_counts_as_annotated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            img = root / 'bg.jpg'
            img.write_bytes(b'fake')
            (root / 'bg.txt').write_text('', encoding='utf-8')

            from io_ops.annotation_status import is_image_annotated
            item = ImageItem(path=img)
            self.assertTrue(is_image_annotated(item))

    def test_no_label_file_is_unannotated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            img = root / 'todo.jpg'
            img.write_bytes(b'fake')

            from io_ops.annotation_status import is_image_annotated
            item = ImageItem(path=img)
            self.assertFalse(is_image_annotated(item))

    def test_manual_status_roundtrip(self):
        from io_ops.annotation_status import (
            load_manual_statuses, save_manual_statuses, IMAGE_CATEGORY_UNCERTAIN,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_manual_statuses(root, {'sub/a.jpg': IMAGE_CATEGORY_UNCERTAIN})
            loaded = load_manual_statuses(root)
            self.assertEqual(loaded, {'sub/a.jpg': IMAGE_CATEGORY_UNCERTAIN})
            save_manual_statuses(root, {})
            self.assertFalse((root / '.annotation_status.json').exists())

    def test_uncertain_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a.jpg').write_bytes(b'x')
            (root / 'b.jpg').write_bytes(b'x')
            (root / 'a.txt').write_text('0 0.5 0.5 0.2 0.2\n', encoding='utf-8')

            project = Project()
            project.set_image_paths(str(root), Project.scan_image_paths(str(root)))
            project.image_list[1].manual_annotation_status = 'uncertain'

            project.image_filter = ImageFilter.UNCERTAIN
            self.assertEqual(project.get_filtered_indices(), [1])


class PathDisplayTests(unittest.TestCase):
    def test_format_image_display_path(self):
        from utils.paths import format_image_display_path
        path = Path('D:/animals-humans/images/antelope/00260.jpg')
        self.assertEqual(
            format_image_display_path(path),
            'images/antelope/00260.jpg',
        )

    def test_format_deep_path_from_opened_subfolder(self):
        from utils.paths import format_image_display_path
        path = Path('D:/animals-humans/images/bat/00671.jpg')
        self.assertEqual(
            format_image_display_path(path),
            'images/bat/00671.jpg',
        )


class LabelColorTests(unittest.TestCase):
    def test_palette_has_36_colors(self):
        from utils.colors import LABEL_PALETTE
        self.assertEqual(len(LABEL_PALETTE), 36)

    def test_colors_cycle_after_palette_length(self):
        from utils.colors import get_color_for_class, LABEL_PALETTE
        n = len(LABEL_PALETTE)
        self.assertEqual(get_color_for_class(0), get_color_for_class(n))
        self.assertEqual(get_color_for_class(5), get_color_for_class(5 + n))

    def test_no_faded_pastel_colors(self):
        # Every palette color should be dark enough to read on a light bg:
        # reject near-white / very light colors (min channel high AND bright).
        from utils.colors import LABEL_PALETTE, hex_to_rgb
        for hexc in LABEL_PALETTE:
            r, g, b = hex_to_rgb(hexc)
            luminance = 0.299 * r + 0.587 * g + 0.114 * b
            self.assertLess(luminance, 200, f'{hexc} too light/faded')


class ClassesFileImportTests(unittest.TestCase):
    def test_load_from_txt_id_name_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            classes = Path(tmp) / 'classes.txt'
            classes.write_text('0: bird\n1: african buffalo\n', encoding='utf-8')
            mgr = LabelManager()
            count = mgr.load_from_txt(str(classes))
            self.assertEqual(count, 2)
            self.assertEqual(mgr.get_name(0), 'bird')
            self.assertEqual(mgr.get_name(1), 'african buffalo')


class ImageItemMutationTests(unittest.TestCase):
    def test_remove_selected_marks_dirty(self):
        item = ImageItem(path=Path('sample.jpg'))
        bbox = BBox(x1=1, y1=1, x2=10, y2=10, class_id=0, class_name='a')
        bbox.is_selected = True
        item.annotations.append(bbox)
        item.mark_clean()
        self.assertFalse(item.is_dirty)

        removed = item.remove_selected()
        self.assertEqual(len(removed), 1)
        self.assertTrue(item.is_dirty)


if __name__ == '__main__':
    unittest.main()

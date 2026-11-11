"""批量标签缓存的路径优先级、目录索引复用与并发编辑回归。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.annotation import BBox
from core.image_item import ImageItem
from core.project import Project, LABEL_FILTER_BACKGROUND
from io_ops.annotation_status import resolve_annotation_txt_path
from io_ops.label_cache import AnnotationDirectoryIndex, iter_label_cache_batches

# ==================== 缓存回归参数设置区 ====================
TEST_IMAGE_COUNT = 12


class LabelCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, relative, content=''):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def test_index_preserves_sidecar_flat_and_mirrored_path_preference(self):
        images = [self.write(f'images/camera_{index}/sample.jpg') for index in range(3)]
        missing = self.write('images/missing.jpg')
        self.write('images/camera_0/sample.txt', '0 .5 .5 .2 .2\n')
        flat = self.write('labels/sample.txt', '1 .5 .5 .2 .2\n')
        mirrored = self.write('labels/camera_1/sample.txt', '2 .5 .5 .2 .2\n')
        index = AnnotationDirectoryIndex()
        for image in images + [missing]:
            self.assertEqual(index.resolve(image), resolve_annotation_txt_path(image, scan_fallback=False))
        self.assertEqual(index.resolve(images[1]), flat)
        flat.unlink()
        self.assertEqual(AnnotationDirectoryIndex().resolve(images[1]), mirrored)

    def test_each_candidate_directory_is_enumerated_once(self):
        import os
        items = [ImageItem(self.write(f'images/{index:02d}.jpg')) for index in range(TEST_IMAGE_COUNT)]
        for index in range(TEST_IMAGE_COUNT):
            self.write(f'labels/{index:02d}.txt', f'{index} .5 .5 .2 .2\n')
        with patch('io_ops.label_cache.os.scandir', wraps=os.scandir) as scan:
            records = [record for batch in iter_label_cache_batches(items) for record in batch]
        directories = [call.args[0] for call in scan.call_args_list]
        self.assertEqual(len(directories), len(set(directories)))
        self.assertLess(len(directories), TEST_IMAGE_COUNT)
        self.assertEqual([record[2] for record in records], [{i} for i in range(TEST_IMAGE_COUNT)])

    def test_background_missing_and_malformed_files_stay_distinct(self):
        paths = [self.write(f'images/{name}.jpg') for name in ('empty', 'missing', 'malformed')]
        self.write('labels/empty.txt')
        self.write('labels/malformed.txt', 'invalid .5 .5 .2 .2\n')
        project = Project()
        project.set_image_paths(str(self.root/'images'), paths)
        for batch in iter_label_cache_batches(project.image_list):
            project.merge_label_cache_batch(batch)
        project.label_filter_class_id = LABEL_FILTER_BACKGROUND
        self.assertEqual(project.get_filtered_indices(), [0])
        self.assertFalse(project.label_contains_class(project.image_list[1], 0))

    def test_cancel_drops_remaining_batches(self):
        items = [ImageItem(self.write(f'images/{index:02d}.jpg')) for index in range(TEST_IMAGE_COUNT)]
        cancelled = False
        batches = iter_label_cache_batches(items, is_cancelled=lambda: cancelled, batch_size=3)
        self.assertEqual(len(next(batches)), 3)
        cancelled = True
        self.assertEqual(list(batches), [])

    def test_session_edits_and_saves_override_older_disk_snapshot(self):
        image = self.write('images/sample.jpg')
        label = self.write('labels/sample.txt', '0 .5 .5 .2 .2\n')
        project = Project()
        project.set_image_paths(str(image.parent), [image])
        item = project.current_image
        records = next(iter_label_cache_batches(project.image_list))
        item.annotations = [BBox(1, 1, 10, 10, 4, 'fox')]
        item.mark_dirty()
        project.merge_label_cache_batch(records)
        self.assertTrue(project.label_contains_class(item, 4))
        self.assertFalse(project.label_contains_class(item, 0))
        item.annotations.clear()
        label.unlink()
        item.mark_clean()
        project.merge_label_cache_batch(records)
        self.assertFalse(project._is_background_item(item))
        label.write_text('', encoding='utf-8')
        project.merge_label_cache_batch(records)
        self.assertTrue(project._is_background_item(item))


if __name__ == '__main__':
    unittest.main()

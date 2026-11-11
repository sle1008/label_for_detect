"""画布显示变换测试，不启动 Tk 窗口。"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.annotation import BBox
from ui.canvas_panel import AnnotationCanvas


class CanvasRotationTests(unittest.TestCase):
    """验证旋转显示时原图和画布之间的坐标对应关系。"""

    @staticmethod
    def make_canvas(rotated=True):
        canvas = object.__new__(AnnotationCanvas)
        canvas._source_pil_image = SimpleNamespace(width=200, height=100)
        canvas._rotation_180 = rotated
        canvas._scale = 2.0
        canvas._offset_x = 20.0
        canvas._offset_y = 30.0
        return canvas

    def test_rotated_coordinate_transforms_are_inverse(self):
        canvas = self.make_canvas()

        canvas_point = canvas.image_to_canvas(25, 40)
        image_point = canvas.canvas_to_image(*canvas_point)

        self.assertAlmostEqual(image_point[0], 25)
        self.assertAlmostEqual(image_point[1], 40)
        self.assertEqual(canvas_point, (370.0, 150.0))

    def test_rotated_bbox_keeps_normalized_display_bounds(self):
        canvas = self.make_canvas()
        bbox = BBox(10, 20, 60, 70, 0, 'object')

        bounds = canvas._bbox_canvas_bounds(bbox)

        self.assertEqual(bounds, (300.0, 90.0, 400.0, 190.0))

    def test_unrotated_coordinate_transform_is_unchanged(self):
        canvas = self.make_canvas(rotated=False)

        self.assertEqual(canvas.image_to_canvas(25, 40), (70.0, 110.0))
        self.assertEqual(canvas.canvas_to_image(70.0, 110.0), (25.0, 40.0))


if __name__ == '__main__':
    unittest.main()

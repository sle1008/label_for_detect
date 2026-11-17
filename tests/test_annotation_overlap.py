"""重复框几何识别回归，验证人工复核提示不会将普通遮挡视为重复。"""

import unittest
from core.annotation import BBox
from core.annotation_overlap import find_duplicate_groups

# ==================== 重叠回归参数设置区 ====================
SAMPLE_BOX = (10, 10, 110, 110)


class AnnotationOverlapTests(unittest.TestCase):
    def box(self, coords=SAMPLE_BOX, class_id=0):
        return BBox(*coords, class_id, f'class-{class_id}')

    def test_exact_same_and_different_classes_form_groups_without_changing_data(self):
        boxes = [self.box(), self.box(class_id=1), self.box((200, 20, 250, 80)),
                 self.box((200, 20, 250, 80))]
        before = [vars(box).copy() for box in boxes]
        self.assertEqual(find_duplicate_groups(boxes), ((0, 1), (2, 3)))
        self.assertEqual([vars(box) for box in boxes], before)

    def test_nearly_equal_frames_are_flagged_but_normal_overlap_and_containment_are_not(self):
        original = self.box()
        for coords, expected in [((12, 12, 112, 112), ((0, 1),)),
                                 ((30, 10, 130, 110), ()),
                                 ((20, 20, 60, 60), ()),
                                 ((110, 10, 210, 110), ())]:
            with self.subTest(coords=coords):
                self.assertEqual(find_duplicate_groups([original, self.box(coords)]), expected)

    def test_groups_follow_moves_deletes_and_chained_duplicates(self):
        boxes = [self.box(), self.box((15, 10, 115, 110)), self.box((20, 10, 120, 110))]
        self.assertEqual(find_duplicate_groups(boxes), ((0, 1, 2),))
        del boxes[1]
        self.assertEqual(find_duplicate_groups(boxes), ())
        boxes[1].move(-10, 0)
        self.assertEqual(find_duplicate_groups(boxes), ((0, 1),))

    def test_ninety_percent_boundary_is_inclusive_and_lower_overlap_is_not_duplicate(self):
        original = self.box((0, 0, 100, 100))
        for width, expected in ((89.9, ()), (90, ((0, 1),)), (90.1, ((0, 1),))):
            with self.subTest(width=width):
                self.assertEqual(find_duplicate_groups([original, self.box((0, 0, width, 100))]), expected)

    def test_invalid_boxes_and_empty_lists_do_not_produce_duplicates(self):
        self.assertEqual(find_duplicate_groups([]), ())
        boxes = [self.box(), self.box((10, 10, 10, 100)), self.box((110, 10, 10, 110)),
                 self.box((float('nan'), 10, 110, 110)), self.box((10, 10, float('inf'), 110))]
        self.assertEqual(find_duplicate_groups(boxes), ())


if __name__ == '__main__':
    unittest.main()

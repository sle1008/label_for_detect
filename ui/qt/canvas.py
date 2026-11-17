"""Qt 原生标注画布：统一坐标变换、鼠标编辑、视频预览和高分屏绘制。"""

import math
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QPainterPath, QPen, QColor, QImage, QFont, QCursor, QPixmap
from PySide6.QtWidgets import QWidget
from core.annotation import BBox
from core.annotation_overlap import find_duplicate_groups
from ui.canvas_state import CanvasMode, LABEL_MODE_CYCLE, LABEL_MODE_FULL, LABEL_MODE_COMPACT
from ui.qt.bridge import VisibilityMixin, event_position
from ui.qt.theme import theme_color

# ==================== 画布参数设置区 ====================
DRAG_THRESHOLD = 3
HANDLE_RADIUS = 4
HANDLE_HIT_RADIUS = 9
MINIMUM_BOX_PIXELS = 3
# 鼠标图形采用固定的小尺寸黑白十字，与延伸的辅助虚线分别绘制。
CROSS_CURSOR_SIZE = 32
CROSS_CURSOR_ARM = 9
CROSS_CURSOR_OUTLINE_WIDTH = 3
CROSS_CURSOR_CORE_WIDTH = 1
# 选中框的黑色外描边与白色内线宽度按逻辑像素统一配置。
SELECTED_BOX_OUTLINE_WIDTH = 5
SELECTED_BOX_CORE_WIDTH = 2
# 重复框使用细一档的强调边线和合并标签；提示仅绘制在预览，不写入标注。
BOX_LINE_WIDTH = 1.7
DUPLICATE_BOX_EXTRA_WIDTH = 1
DUPLICATE_ACCENT = '#efb65e'
DUPLICATE_TAG_BACKGROUND = '#18212c'
DUPLICATE_TAG_ROW_HEIGHT = 22
DUPLICATE_TAG_TEXT_PADDING = 32
DUPLICATE_NOTICE_PIXEL_SIZE = 17
DUPLICATE_NOTICE_OUTLINE_WIDTH = 3
DUPLICATE_NOTICE_MARGIN = 12
ROTATED_HANDLES = {'nw': 'se', 'ne': 'sw', 'sw': 'ne', 'se': 'nw',
                   'n': 's', 's': 'n', 'w': 'e', 'e': 'w'}
# 相对的手柄共用一个缩放轴，旋转 180 度后仍使用相同的双向箭头。
RESIZE_CURSORS = {
    'nw': Qt.CursorShape.SizeFDiagCursor, 'se': Qt.CursorShape.SizeFDiagCursor,
    'ne': Qt.CursorShape.SizeBDiagCursor, 'sw': Qt.CursorShape.SizeBDiagCursor,
    'n': Qt.CursorShape.SizeVerCursor, 's': Qt.CursorShape.SizeVerCursor,
    'w': Qt.CursorShape.SizeHorCursor, 'e': Qt.CursorShape.SizeHorCursor,
}


class AnnotationCanvas(VisibilityMixin, QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(200, 150)
        self._current_image = None
        self._source_pil_image = None
        self._qimage = None
        self._video_mode = False
        self._video_annotations = []
        self._scale = 1.0
        self._offset_x = self._offset_y = 0.0
        self._rotation_180 = False
        self._label_mode = LABEL_MODE_FULL
        self._mode = CanvasMode.IDLE
        self._press = None
        self._press_image = None
        self._box = None
        self._handle = None
        self._originals = []
        self._preview = None
        self._rubber = None
        self._right = False
        self._space = False
        self._pointer = None
        self._hover = None
        self._context_menu = None
        self._duplicate_geometry = None
        self._duplicate_groups = ()
        self._label_regions = []
        self._label_press = None
        self._tab_cycle_scope = None
        self._on_image_context_menu = app._on_canvas_image_context_menu
        self._on_video_context_menu = app._show_video_context_menu
        self._crosshair_cursor = self._create_crosshair_cursor()

    @staticmethod
    def _create_crosshair_cursor():
        pixmap = QPixmap(CROSS_CURSOR_SIZE, CROSS_CURSOR_SIZE)
        pixmap.fill(Qt.GlobalColor.transparent)
        center = CROSS_CURSOR_SIZE // 2
        painter = QPainter(pixmap)
        for color, width in (('#ffffff', CROSS_CURSOR_OUTLINE_WIDTH), ('#000000', CROSS_CURSOR_CORE_WIDTH)):
            pen = QPen(QColor(color), width)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawLine(center - CROSS_CURSOR_ARM, center, center + CROSS_CURSOR_ARM, center)
            painter.drawLine(center, center - CROSS_CURSOR_ARM, center, center + CROSS_CURSOR_ARM)
        painter.end()
        return QCursor(pixmap, center, center)

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, mode):
        self._mode = mode
        self.app._on_mode_changed(mode)

    @property
    def label_mode(self):
        return self._label_mode

    def set_context_menu(self, menu):
        self._context_menu = menu

    def set_label_manager(self, manager):
        self._label_manager = manager

    def set_label_mode(self, mode):
        if mode in LABEL_MODE_CYCLE:
            self._label_mode = mode
            self.update()

    def cycle_label_mode(self):
        mode = LABEL_MODE_CYCLE[(LABEL_MODE_CYCLE.index(self._label_mode) + 1) % len(LABEL_MODE_CYCLE)]
        self.set_label_mode(mode)
        return mode

    def _set_pixels(self, image):
        pixels = image.convert('RGBA')
        self._qimage = QImage(pixels.tobytes(), pixels.width, pixels.height,
                              pixels.width * 4, QImage.Format.Format_RGBA8888).copy()

    def set_image(self, item, pil_image=None):
        self._video_mode = False
        self._press = None
        self._label_press = None
        self._label_regions = []
        self._tab_cycle_scope = None
        if self._current_image is not item:
            self._rotation_180 = False
        self._current_image = item
        self._source_pil_image = pil_image if pil_image is not None else item._pil_image
        self._video_annotations = []
        self._mode = CanvasMode.IDLE
        if self._source_pil_image is not None:
            self._set_pixels(self._source_pil_image)
            self.fit_to_window()
        else:
            self._qimage = None
            self.update()

    def show_video_frame(self, image, annotations=None):
        self._video_mode = True
        self._label_press = None
        self._label_regions = []
        self._tab_cycle_scope = None
        self._rotation_180 = False
        self._current_image = None
        self._source_pil_image = image
        self._video_annotations = annotations or []
        self.app._box_list_panel.set_video_annotations(self._video_annotations)
        self._set_pixels(image)
        self.fit_to_window()

    def clear_all(self):
        self._current_image = self._source_pil_image = self._qimage = None
        self._video_annotations = []
        self._video_mode = False
        self._rotation_180 = False
        self._preview = self._rubber = self._press = None
        self._label_press = None
        self._label_regions = []
        self._tab_cycle_scope = None
        self._mode = CanvasMode.IDLE
        self.update()

    def refresh(self):
        self.update()

    def toggle_rotation_180(self):
        if self._video_mode or not self._current_image or self._source_pil_image is None:
            return False
        self._rotation_180 = not self._rotation_180
        self.update()
        return True

    def fit_to_window(self):
        image = self._source_pil_image
        if image is None:
            return
        padding = 0 if self._video_mode else 32
        self._scale = max(0.001, min((self.width() - padding) / image.width,
                                    (self.height() - padding) / image.height))
        self._offset_x = (self.width() - image.width * self._scale) / 2
        self._offset_y = (self.height() - image.height * self._scale) / 2
        self.update()

    def image_to_canvas(self, x, y):
        if self._rotation_180 and self._source_pil_image:
            x, y = self._source_pil_image.width - x, self._source_pil_image.height - y
        return x * self._scale + self._offset_x, y * self._scale + self._offset_y

    def canvas_to_image(self, x, y):
        x, y = (x - self._offset_x) / self._scale, (y - self._offset_y) / self._scale
        if self._rotation_180 and self._source_pil_image:
            x, y = self._source_pil_image.width - x, self._source_pil_image.height - y
        return x, y

    def _rect(self, box):
        a = self.image_to_canvas(box.x1, box.y1)
        b = self.image_to_canvas(box.x2, box.y2)
        return QRectF(QPointF(*a), QPointF(*b)).normalized()

    def _handles(self, box):
        r = self._rect(box)
        return {'nw': r.topLeft(), 'n': QPointF(r.center().x(), r.top()), 'ne': r.topRight(),
                'w': QPointF(r.left(), r.center().y()), 'e': QPointF(r.right(), r.center().y()),
                'sw': r.bottomLeft(), 's': QPointF(r.center().x(), r.bottom()), 'se': r.bottomRight()}

    def _find_handle(self, point):
        if not self._current_image:
            return None, None
        best, distance = (None, None), HANDLE_HIT_RADIUS
        for box in self._current_image.get_selected_annotations():
            center_distance = (point - self._rect(box).center()).manhattanLength()
            for handle, location in self._handles(box).items():
                d = math.hypot(point.x() - location.x(), point.y() - location.y())
                if d <= distance and d < center_distance:
                    # 将屏幕上的手柄方向转换回原图方向，后续编辑统一使用原图坐标。
                    best = (ROTATED_HANDLES[handle] if self._rotation_180 else handle, box)
                    distance = d
        return best

    def _find_box(self, point):
        if not self._current_image:
            return None
        x, y = self.canvas_to_image(point.x(), point.y())
        candidates = [box for box in self._current_image.annotations if box.contains_point(x, y)]
        return min(candidates, key=lambda box: box.area(), default=None)

    def _frame_rect(self):
        if self._source_pil_image is None:
            return QRectF()
        return QRectF(self._offset_x, self._offset_y, self._source_pil_image.width * self._scale,
                      self._source_pil_image.height * self._scale)

    def _get_duplicate_groups(self, boxes):
        geometry = tuple((box.x1, box.y1, box.x2, box.y2) for box in boxes)
        if geometry != self._duplicate_geometry:
            self._duplicate_groups = find_duplicate_groups(boxes)
            self._duplicate_geometry = geometry
        return self._duplicate_groups

    def _select_annotation(self, box, toggle=False):
        """画布各选择入口同步实例表和类别定位，选择本身不修改标注。"""
        item = self._current_image
        if self._video_mode or item is None or box not in item.annotations:
            return
        if not toggle:
            item.deselect_all()
        box.is_selected = not box.is_selected if toggle else True
        self.app._on_annotation_changed()
        self.update()

    def _set_tab_cycle_scope(self, box, point):
        """由点击位置确定循环范围；Tab 产生的选择不会改变此范围。"""
        if box is None:
            self._tab_cycle_scope = 'image' if self._frame_rect().contains(point) else None
        else:
            boxes = self._current_image.annotations
            index = boxes.index(box)
            self._tab_cycle_scope = ('duplicates' if any(index in group for group in self._get_duplicate_groups(boxes))
                                     else None)

    def cycle_annotation_selection(self):
        if self._video_mode or self._current_image is None or self._press is not None:
            return False
        boxes = self._current_image.annotations
        selected = [index for index, box in enumerate(boxes) if box.is_selected]
        if self._tab_cycle_scope == 'image' and boxes:
            target = (selected[-1] + 1) % len(boxes) if selected else 0
            self._select_annotation(boxes[target])
            return True
        if len(selected) != 1:
            return False
        for group in self._get_duplicate_groups(boxes):
            if selected[0] in group:
                target = group[(group.index(selected[0]) + 1) % len(group)]
                self._select_annotation(boxes[target])
                return True
        return False

    def _find_label(self, point):
        if self._video_mode or self._current_image is None:
            return None
        # 后绘制的标签位于上层，命中顺序应与实际显示保持一致。
        for rect, box in reversed(self._label_regions):
            if rect.contains(point) and box in self._current_image.annotations:
                return box
        return None

    def _draw_duplicate_labels(self, painter, boxes, groups, visible_frame):
        """每组标签集中显示，避免几乎重合的类别条彼此覆盖。"""
        if visible_frame.isEmpty():
            return
        painter.save()
        for group in groups:
            expanded = (self._video_mode or self._label_mode == LABEL_MODE_FULL
                        or any(boxes[index].is_selected or boxes[index] is self._hover for index in group))
            rows = []
            for index in group:
                box = boxes[index]
                if expanded:
                    text = f'#{index + 1} {box.class_name}'
                    if self._video_mode or box.confidence < 1:
                        text += f' {box.confidence:.2f}'
                    rows.append((text, box))
            compact = (' / '.join(f'#{index + 1}' for index in group)
                       if not expanded and self._label_mode == LABEL_MODE_COMPACT else '')
            if not rows and not compact:
                continue
            painter.setFont(QFont('Microsoft YaHei UI', 9, QFont.Weight.DemiBold))
            metrics = painter.fontMetrics()
            # 标签可以利用图像旁的画布留白，文字宽度按实际字体测量，避免过早省略。
            canvas_rect = QRectF(self.rect())
            width = min(canvas_rect.width(), max(metrics.horizontalAdvance(text)
                        for text in ([compact] if compact else [text for text, _ in rows]))
                        + DUPLICATE_TAG_TEXT_PADDING)
            height = DUPLICATE_TAG_ROW_HEIGHT * max(1, len(rows)) + 4
            bounds = self._rect(boxes[group[0]])
            for index in group[1:]:
                bounds = bounds.united(self._rect(boxes[index]))
            if not bounds.intersects(visible_frame):
                continue
            left = max(canvas_rect.left(), min(bounds.left(), canvas_rect.right() - width))
            top = bounds.top() - height - 3
            if top < visible_frame.top():
                top = bounds.top() + 6
            top = max(visible_frame.top(), min(top, visible_frame.bottom() - height))
            panel = QRectF(left, top, width, height)
            painter.setPen(QPen(QColor(DUPLICATE_ACCENT), 1))
            painter.setBrush(QColor(DUPLICATE_TAG_BACKGROUND))
            painter.drawRoundedRect(panel, 5, 5)
            row = panel.adjusted(9, 2, -7, 0)
            row.setHeight(DUPLICATE_TAG_ROW_HEIGHT)
            if compact:
                painter.setPen(QColor(DUPLICATE_ACCENT))
                painter.drawText(row, Qt.AlignmentFlag.AlignVCenter,
                                 metrics.elidedText(compact, Qt.TextElideMode.ElideRight, int(row.width())))
                left = row.left()
                for index in group:
                    width = metrics.horizontalAdvance(f'#{index + 1}')
                    hit = QRectF(left, row.top(), width, row.height()).intersected(row)
                    if not hit.isEmpty():
                        self._label_regions.append((hit, boxes[index]))
                    left += width + metrics.horizontalAdvance(' / ')
            for text, box in rows:
                self._label_regions.append((QRectF(row), box))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(self.app._label_manager.get_color(box.class_id)))
                painter.drawEllipse(QPointF(row.left() + 3, row.center().y()), 3, 3)
                painter.setPen(QColor('#ffffff'))
                painter.setFont(QFont('Microsoft YaHei UI', 9,
                                     QFont.Weight.DemiBold if box.is_selected else QFont.Weight.Normal))
                text_rect = row.adjusted(12, 0, 0, 0)
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter,
                                 painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight,
                                                                 int(text_rect.width())))
                row.translate(0, DUPLICATE_TAG_ROW_HEIGHT)
        painter.restore()

    def _draw_duplicate_notice(self, painter, visible_frame, count):
        if not count or visible_frame.isEmpty():
            return
        painter.save()
        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(DUPLICATE_NOTICE_PIXEL_SIZE)
        font.setWeight(QFont.Weight.DemiBold)
        text = QPainterPath()
        text.addText(QPointF(), font, f'有{count}个重复框')
        bounds = text.boundingRect()
        # 提示贴合当前可见图像区域，缩放和平移后也不会跟随框被裁出画面。
        text.translate(visible_frame.right() - DUPLICATE_NOTICE_MARGIN - bounds.right(),
                       visible_frame.top() + DUPLICATE_NOTICE_MARGIN - bounds.top())
        pen = QPen(QColor('#ffffff'), DUPLICATE_NOTICE_OUTLINE_WIDTH)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(text)
        # 先描外边，再覆盖黑色字芯，避免白色笔画吞没较细的中文字体。
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#000000'))
        painter.drawPath(text)
        painter.restore()

    def paintEvent(self, event):
        self._label_regions = []
        painter = QPainter(self)
        theme = self.app._config.ui_theme
        painter.fillRect(self.rect(), QColor(theme_color('canvas', theme)))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._qimage is None:
            painter.setPen(QColor(theme_color('muted', theme)))
            painter.setFont(QFont('Microsoft YaHei UI', 24, QFont.Weight.DemiBold))
            painter.drawText(self.rect().adjusted(0, -45, 0, -45), Qt.AlignmentFlag.AlignCenter, '开始新的标注工作')
            painter.setFont(QFont('Microsoft YaHei UI', 11))
            painter.setPen(QColor(theme_color('placeholder', theme)))
            painter.drawText(self.rect().adjusted(0, 35, 0, 35), Qt.AlignmentFlag.AlignCenter,
                             '打开图片目录开始标注，或载入视频进行预览与推理')
            return
        frame_rect = self._frame_rect()
        painter.save()
        if self._rotation_180:
            painter.translate(frame_rect.center())
            painter.rotate(180)
            painter.translate(-frame_rect.center())
        painter.drawImage(frame_rect, self._qimage)
        painter.restore()
        boxes = self._video_annotations if self._video_mode else self._current_image.annotations
        duplicate_groups = self._get_duplicate_groups(boxes)
        duplicate_indices = {index for group in duplicate_groups for index in group}
        visible_frame = frame_rect.intersected(QRectF(self.rect()))
        indexed_boxes = list(enumerate(boxes))
        if not self._video_mode:
            # 仅调整绘制顺序，保持实例序号和数据顺序，选中框最后覆盖显示。
            indexed_boxes.sort(key=lambda pair: pair[1].is_selected)
        for index, box in indexed_boxes:
            rect = self._rect(box)
            color = QColor(self.app._label_manager.get_color(box.class_id))
            selected = box.is_selected and not self._video_mode
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if selected:
                painter.setPen(QPen(QColor('#000000'), SELECTED_BOX_OUTLINE_WIDTH))
                painter.drawRect(rect)
                painter.setPen(QPen(QColor('#ffffff'), SELECTED_BOX_CORE_WIDTH))
            else:
                painter.setPen(QPen(color, BOX_LINE_WIDTH +
                                    (DUPLICATE_BOX_EXTRA_WIDTH if index in duplicate_indices else 0)))
            painter.drawRect(rect)
            if selected:
                painter.setBrush(QColor('#ffffff'))
                painter.setPen(QPen(color, 1.5))
                for point in self._handles(box).values():
                    painter.drawRoundedRect(QRectF(point.x() - HANDLE_RADIUS, point.y() - HANDLE_RADIUS,
                                                   2 * HANDLE_RADIUS, 2 * HANDLE_RADIUS), 1, 1)
            if self._video_mode or self._label_mode == LABEL_MODE_FULL or selected or self._hover is box:
                text = f'{box.class_name}'
                if not self._video_mode:
                    # 图片框使用原始实例顺序编号，与右侧实例表及精简标签一致。
                    text = f'#{index + 1} {text}'
                if self._video_mode or box.confidence < 1:
                    text += f' {box.confidence:.2f}'
            elif self._label_mode == LABEL_MODE_COMPACT:
                text = f'#{index + 1}'
            else:
                text = ''
            if text and index not in duplicate_indices:
                painter.setFont(QFont('Microsoft YaHei UI', 9))
                width = painter.fontMetrics().horizontalAdvance(text) + 12
                tag = QRectF(rect.left(), max(0, rect.top() - 24), width, 22)
                self._label_regions.append((tag, box))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(tag, 3, 3)
                painter.setPen(QColor('#ffffff'))
                painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, text)
        self._draw_duplicate_labels(painter, boxes, duplicate_groups, visible_frame)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor('#95b7f9'), 1.3, Qt.PenStyle.DashLine))
        if self._preview:
            painter.drawRect(self._preview)
        if self._rubber:
            painter.drawRect(self._rubber)
        if self._pointer and not self._video_mode and self._frame_rect().contains(self._pointer):
            painter.setPen(QPen(QColor(theme_color('crosshair', theme)), 0.7, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(self._pointer.x(), 0), QPointF(self._pointer.x(), self.height()))
            painter.drawLine(QPointF(0, self._pointer.y()), QPointF(self.width(), self._pointer.y()))
        self._draw_duplicate_notice(painter, visible_frame,
                                    sum(len(group) - 1 for group in duplicate_groups))

    def _update_pointer_cursor(self):
        if self._video_mode or not self._current_image or self._pointer is None:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        # 光标与按下时共用手柄命中范围，包含图片边缘外可拖拽的少量留白。
        handle, _ = self._find_handle(self._pointer)
        if handle:
            self.setCursor(RESIZE_CURSORS[handle])
        elif self._find_label(self._pointer):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif not self._frame_rect().contains(self._pointer):
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor if self._hover else self._crosshair_cursor)

    def mousePressEvent(self, event):
        self.setFocus()
        point = event.position()
        self._press = point
        self._press_image = self.canvas_to_image(point.x(), point.y())
        self._right = event.button() == Qt.MouseButton.RightButton
        self._originals = []
        self._label_press = None
        if event.button() == Qt.MouseButton.MiddleButton or (self._space and not self._video_mode):
            self._mode = CanvasMode.PANNING
            self._pan_offsets = (self._offset_x, self._offset_y)
            return
        if self._video_mode:
            if not self._frame_rect().contains(point):
                self._press = None
            return
        if not self._current_image:
            return
        if self._right or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._mode = CanvasMode.SELECTING
            return
        # 点击前刷新标签命中区域，避免刚缩放、切图或删框后沿用旧位置。
        self.repaint()
        self._handle, self._box = self._find_handle(point)
        if self._handle:
            self._mode = CanvasMode.RESIZING
            self._originals = [(self._box, (self._box.x1, self._box.y1, self._box.x2, self._box.y2))]
            return
        self._label_press = self._find_label(point)
        if self._label_press:
            self._mode = CanvasMode.IDLE
            return
        self._box = self._find_box(point)
        self._set_tab_cycle_scope(self._box, point)
        if self._box and self._box.is_selected:
            self._mode = CanvasMode.MOVING
            self._originals = [(box, (box.x1, box.y1, box.x2, box.y2))
                               for box in self._current_image.get_selected_annotations()]
        else:
            self._mode = CanvasMode.IDLE

    def mouseMoveEvent(self, event):
        point = event.position()
        self._pointer = point
        self._hover = self._find_label(point) or self._find_box(point)
        if self._press is None:
            self._update_pointer_cursor()
            self.update()
            return
        distance = (point - self._press).manhattanLength()
        if self._label_press is not None:
            self.update()
            return
        if self._mode == CanvasMode.PANNING:
            delta = point - self._press
            self._offset_x, self._offset_y = self._pan_offsets[0] + delta.x(), self._pan_offsets[1] + delta.y()
        elif self._video_mode:
            if distance > DRAG_THRESHOLD:
                self._press = None
        elif self._current_image:
            x, y = self.canvas_to_image(point.x(), point.y())
            if self._mode == CanvasMode.IDLE and distance > DRAG_THRESHOLD:
                self._current_image.deselect_all()
                self._mode = CanvasMode.DRAWING
            if self._mode == CanvasMode.DRAWING:
                self._preview = QRectF(self._press, point).normalized()
            elif self._mode == CanvasMode.MOVING:
                for box, original in self._originals:
                    dx, dy = x - self._press_image[0], y - self._press_image[1]
                    box.x1, box.y1, box.x2, box.y2 = original[0] + dx, original[1] + dy, original[2] + dx, original[3] + dy
            elif self._mode == CanvasMode.RESIZING:
                self._box.resize(self._handle, x, y, self._current_image.width, self._current_image.height)
            elif self._mode == CanvasMode.SELECTING and distance > DRAG_THRESHOLD:
                self._rubber = QRectF(self._press, point).normalized()
        self.update()

    def mouseReleaseEvent(self, event):
        point, press = event.position(), self._press
        label_press, self._label_press = self._label_press, None
        mode = self._mode
        self._press = None
        if label_press is not None:
            if (press is not None and (point - press).manhattanLength() <= DRAG_THRESHOLD
                    and self._find_label(point) is label_press):
                self._set_tab_cycle_scope(label_press, point)
                self._select_annotation(label_press, bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
            self._mode = CanvasMode.IDLE
            return
        if mode == CanvasMode.PANNING:
            self._mode = CanvasMode.IDLE
            return
        if self._video_mode:
            if event.button() == Qt.MouseButton.RightButton:
                self._on_video_context_menu(event_position(event.globalPosition().toPoint()))
            elif press is not None and (point - press).manhattanLength() <= DRAG_THRESHOLD and self._frame_rect().contains(point):
                self.app._toggle_video_play()
            return
        if not self._current_image or press is None:
            return
        if mode == CanvasMode.SELECTING and self._rubber:
            for box in self._current_image.annotations:
                center = QPointF(*self.image_to_canvas(*box.center()))
                box.is_selected = self._rubber.contains(center)
            self.app._on_annotation_changed()
        elif self._right:
            menu = self._context_menu if self._current_image.get_selected_annotations() else None
            if menu:
                menu.tk_popup(event.globalPosition().x(), event.globalPosition().y())
            else:
                self._on_image_context_menu(event_position(event.globalPosition().toPoint()))
        elif mode == CanvasMode.DRAWING:
            x, y = self.canvas_to_image(point.x(), point.y())
            width, height = self._current_image.width, self._current_image.height
            x1, x2 = sorted((max(0, min(width, self._press_image[0])), max(0, min(width, x))))
            y1, y2 = sorted((max(0, min(height, self._press_image[1])), max(0, min(height, y))))
            if x2 - x1 >= MINIMUM_BOX_PIXELS and y2 - y1 >= MINIMUM_BOX_PIXELS:
                cid = self.app._label_manager.current_class_id
                self.app._on_new_bbox(BBox(x1, y1, x2, y2, cid, self.app._label_manager.get_name(cid)))
        elif mode in (CanvasMode.MOVING, CanvasMode.RESIZING):
            changes = [(box, old, (box.x1, box.y1, box.x2, box.y2)) for box, old in self._originals
                       if any(abs(a - b) > 0.5 for a, b in zip(old, (box.x1, box.y1, box.x2, box.y2)))]
            if changes:
                self._current_image.mark_dirty()
                self.app._on_geometry_changed('移动标注框' if mode == CanvasMode.MOVING else '调整标注框', changes)
            elif self._box and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._box.is_selected = not self._box.is_selected
                self.app._on_annotation_changed()
        elif mode == CanvasMode.IDLE:
            box = self._find_box(point)
            if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._current_image.deselect_all()
            if box:
                box.is_selected = not box.is_selected if event.modifiers() & Qt.KeyboardModifier.ControlModifier else True
            self.app._on_annotation_changed()
        self._mode = CanvasMode.IDLE
        self._rubber = self._preview = None
        self.update()

    def wheelEvent(self, event):
        if not self._current_image or self._video_mode:
            return
        point = event.position()
        scale = max(0.02, min(30, self._scale * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15)))
        self._offset_x = point.x() - (point.x() - self._offset_x) * scale / self._scale
        self._offset_y = point.y() - (point.y() - self._offset_y) * scale / self._scale
        self._scale = scale
        self.app._on_zoom_changed(scale)
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._space = False
            self._update_pointer_cursor()
        else:
            super().keyReleaseEvent(event)

    def resizeEvent(self, event):
        if self._source_pil_image and (self._video_mode or self._scale <= 0):
            self.fit_to_window()
        else:
            # 调整侧栏时保留图片的缩放与平移位置，便于连续精细标注。
            self.update()

    def leaveEvent(self, event):
        self._pointer = None
        self.update()

    def cancel_or_deselect(self):
        if self._mode != CanvasMode.IDLE:
            for box, original in self._originals:
                box.x1, box.y1, box.x2, box.y2 = original
        elif self._current_image:
            self._current_image.deselect_all()
            self.app._on_annotation_changed()
        self._mode = CanvasMode.IDLE
        self._preview = self._rubber = self._press = None
        self._label_press = None
        self.update()

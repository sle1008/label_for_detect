"""Qt 原生标注画布：统一坐标变换、鼠标编辑、视频预览和高分屏绘制。"""

import math
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QImage, QFont, QCursor
from PySide6.QtWidgets import QWidget
from core.annotation import BBox
from ui.canvas_state import CanvasMode, LABEL_MODE_CYCLE, LABEL_MODE_FULL, LABEL_MODE_COMPACT
from ui.qt.bridge import VisibilityMixin, event_position

# ==================== 画布参数设置区 ====================
DRAG_THRESHOLD = 3
HANDLE_RADIUS = 4
HANDLE_HIT_RADIUS = 9
MINIMUM_BOX_PIXELS = 3
ROTATED_HANDLES = {'nw': 'se', 'ne': 'sw', 'sw': 'ne', 'se': 'nw',
                   'n': 's', 's': 'n', 'w': 'e', 'e': 'w'}


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
        self._on_image_context_menu = app._on_canvas_image_context_menu
        self._on_video_context_menu = app._show_video_context_menu

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
        self._rotation_180 = False
        self._current_image = None
        self._source_pil_image = image
        self._video_annotations = annotations or []
        self._set_pixels(image)
        self.fit_to_window()

    def clear_all(self):
        self._current_image = self._source_pil_image = self._qimage = None
        self._video_annotations = []
        self._video_mode = False
        self._rotation_180 = False
        self._preview = self._rubber = self._press = None
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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#0d1117'))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._qimage is None:
            painter.setPen(QColor('#5a6b84'))
            painter.setFont(QFont('Microsoft YaHei UI', 24, QFont.Weight.DemiBold))
            painter.drawText(self.rect().adjusted(0, -45, 0, -45), Qt.AlignmentFlag.AlignCenter, '开始新的标注工作')
            painter.setFont(QFont('Microsoft YaHei UI', 11))
            painter.setPen(QColor('#8494ab'))
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
        for index, box in enumerate(boxes):
            rect = self._rect(box)
            color = QColor(self.app._label_manager.get_color(box.class_id))
            selected = box.is_selected and not self._video_mode
            painter.setPen(QPen(color, 2.5 if selected else 1.7))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            if selected:
                painter.setBrush(color)
                painter.setPen(QPen(QColor('#eaf2ff'), 1))
                for point in self._handles(box).values():
                    painter.drawRoundedRect(QRectF(point.x() - HANDLE_RADIUS, point.y() - HANDLE_RADIUS,
                                                   2 * HANDLE_RADIUS, 2 * HANDLE_RADIUS), 1, 1)
            if self._video_mode or self._label_mode == LABEL_MODE_FULL or selected or self._hover is box:
                text = f'{box.class_name}'
                if self._video_mode or box.confidence < 1:
                    text += f' {box.confidence:.2f}'
            elif self._label_mode == LABEL_MODE_COMPACT:
                text = f'#{index + 1}'
            else:
                text = ''
            if text:
                painter.setFont(QFont('Microsoft YaHei UI', 9))
                width = painter.fontMetrics().horizontalAdvance(text) + 12
                tag = QRectF(rect.left(), max(0, rect.top() - 24), width, 22)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(tag, 3, 3)
                painter.setPen(QColor('#ffffff'))
                painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, text)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor('#95b7f9'), 1.3, Qt.PenStyle.DashLine))
        if self._preview:
            painter.drawRect(self._preview)
        if self._rubber:
            painter.drawRect(self._rubber)
        if self._pointer and not self._video_mode and self._frame_rect().contains(self._pointer):
            painter.setPen(QPen(QColor('#6e8bad'), 0.7, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(self._pointer.x(), 0), QPointF(self._pointer.x(), self.height()))
            painter.drawLine(QPointF(0, self._pointer.y()), QPointF(self.width(), self._pointer.y()))

    def mousePressEvent(self, event):
        self.setFocus()
        point = event.position()
        self._press = point
        self._press_image = self.canvas_to_image(point.x(), point.y())
        self._right = event.button() == Qt.MouseButton.RightButton
        self._originals = []
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
        self._handle, self._box = self._find_handle(point)
        if self._handle:
            self._mode = CanvasMode.RESIZING
            self._originals = [(self._box, (self._box.x1, self._box.y1, self._box.x2, self._box.y2))]
            return
        self._box = self._find_box(point)
        if self._box and self._box.is_selected:
            self._mode = CanvasMode.MOVING
            self._originals = [(box, (box.x1, box.y1, box.x2, box.y2))
                               for box in self._current_image.get_selected_annotations()]
        else:
            self._mode = CanvasMode.IDLE

    def mouseMoveEvent(self, event):
        point = event.position()
        self._pointer = point
        self._hover = self._find_box(point)
        if self._press is None:
            handle, box = self._find_handle(point)
            self.setCursor(Qt.CursorShape.SizeAllCursor if box or self._hover else Qt.CursorShape.CrossCursor)
            self.update()
            return
        distance = (point - self._press).manhattanLength()
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
        mode = self._mode
        self._press = None
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
            self.setCursor(Qt.CursorShape.CrossCursor)
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
        self.update()

"""Annotation canvas with drawing, selection, zoom/pan."""

import math
import tkinter as tk
from typing import Optional, List, Tuple, Callable
from enum import Enum, auto
from PIL import Image, ImageTk

from core.annotation import BBox
from core.image_item import ImageItem
from core.label_manager import LabelManager
from utils.constants import (
    CANVAS_BG_COLOR, MIN_ZOOM, MAX_ZOOM, ZOOM_FACTOR,
    MIN_BOX_SIZE, HANDLE_SIZE, HANDLE_HIT_EXTRA,
    BBOX_LINE_WIDTH, BBOX_LINE_WIDTH_SELECTED,
    BBOX_LABEL_FONT, ZOOM_REDRAW_DELAY_MS,
    CANVAS_CROSSHAIR_COLOR, CANVAS_CROSSHAIR_WIDTH, CANVAS_CROSSHAIR_DASH,
    CANVAS_PREVIEW_BOX_WIDTH, CANVAS_PREVIEW_BOX_DASH,
    CANVAS_SELECT_BOX_COLOR, CANVAS_SELECT_BOX_WIDTH, CANVAS_SELECT_BOX_DASH,
)
from utils.geometry import point_in_rect, point_on_rect_edge
from utils.colors import get_color_for_class


# Label display modes (declutter when there are many boxes)
LABEL_MODE_COMPACT = 'compact'   # only #index; selected/hovered show full label
LABEL_MODE_FULL = 'full'         # all boxes show full label
LABEL_MODE_HIDDEN = 'hidden'     # no labels; selected/hovered show full label
LABEL_MODE_CYCLE = (LABEL_MODE_FULL, LABEL_MODE_COMPACT, LABEL_MODE_HIDDEN)
LABEL_MODE_NAMES = {
    LABEL_MODE_COMPACT: '精简',
    LABEL_MODE_FULL: '全部',
    LABEL_MODE_HIDDEN: '隐藏',
}

# Canvas tag shared by every bbox-related item (rect/label/handles), so the
# annotations can be cleared and redrawn without touching the image bitmap.
ANNOTATION_TAG = 'annotation'


class CanvasMode(Enum):
    IDLE = auto()
    DRAWING = auto()
    MOVING = auto()
    RESIZING = auto()
    SELECTING = auto()  # Rubber-band selection
    PANNING = auto()


class AnnotationCanvas(tk.Canvas):
    """Main annotation canvas with zoom, pan, and annotation tools."""
    
    def __init__(self, parent, on_annotation_changed: Callable = None,
                 on_mode_changed: Callable = None,
                 on_new_bbox: Callable[['BBox'], None] = None,
                 on_zoom_changed: Callable = None,
                 on_geometry_changed: Callable = None):
        super().__init__(parent, bg=CANVAS_BG_COLOR, highlightthickness=0)
        
        self._on_annotation_changed = on_annotation_changed
        self._on_mode_changed = on_mode_changed
        self._on_new_bbox = on_new_bbox
        self._on_zoom_changed = on_zoom_changed
        self._on_geometry_changed = on_geometry_changed
        
        # State
        self._mode = CanvasMode.IDLE
        self._current_image: Optional[ImageItem] = None
        self._source_pil_image: Optional[Image.Image] = None
        self._photo_image: Optional[ImageTk.PhotoImage] = None
        self._label_manager: Optional[LabelManager] = None
        
        # Transform
        self._scale: float = 1.0
        self._offset_x: float = 0
        self._offset_y: float = 0
        
        # Drawing state
        self._draw_start: Optional[Tuple[float, float]] = None
        self._draw_rect_id: Optional[int] = None
        self._move_start: Optional[Tuple[float, float]] = None
        self._move_original: Optional[List[Tuple[float, float, float, float]]] = None
        self._press_canvas: Optional[Tuple[float, float]] = None
        self._press_image: Optional[Tuple[float, float]] = None
        self._press_bbox: Optional[BBox] = None
        self._press_drag_threshold = 3
        self._resize_handle: str = ''
        self._resize_bbox: Optional[BBox] = None
        self._resize_original: Optional[Tuple[float, float, float, float]] = None
        self._pan_start: Optional[Tuple[float, float]] = None
        self._pan_offset_start: Optional[Tuple[float, float]] = None
        
        # Selection rectangle
        self._sel_rect_id: Optional[int] = None
        self._sel_start: Optional[Tuple[float, float]] = None
        
        # Crosshair
        self._crosshair_h: Optional[int] = None
        self._crosshair_v: Optional[int] = None
        self._show_crosshair = True

        # Label display
        self._label_mode = LABEL_MODE_FULL
        self._hover_bbox_id = None
        
        # Canvas item IDs
        self._image_id: Optional[int] = None
        self._bbox_items: dict = {}  # bbox.id -> {rect, text, handles}
        
        # Space key state for panning
        self._space_pressed = False
        
        # Right-button drag / context menu
        self._right_down_pos: Optional[Tuple[float, float]] = None
        self._right_dragging = False
        self._context_menu: Optional[tk.Menu] = None
        self._on_image_context_menu = None
        
        # Zoom redraw debounce
        self._quality_upgrade_id = None
        self._redraw_after_id = None
        self._redraw_high_quality = False
        self._zoom_idle_id = None
        
        self._setup_bindings()
    
    def _setup_bindings(self):
        """Setup mouse and keyboard event bindings."""
        self.bind('<ButtonPress-1>', self._on_mouse_down)
        self.bind('<B1-Motion>', self._on_mouse_drag)
        self.bind('<ButtonRelease-1>', self._on_mouse_up)
        self.bind('<Motion>', self._on_mouse_move)
        self.bind('<MouseWheel>', self._on_mouse_wheel)
        self.bind('<Button-4>', self._on_mouse_wheel_linux)
        self.bind('<Button-5>', self._on_mouse_wheel_linux)
        self.bind('<ButtonPress-2>', self._on_pan_start)
        self.bind('<B2-Motion>', self._on_pan_move)
        self.bind('<ButtonRelease-2>', self._on_pan_end)
        self.bind('<ButtonPress-3>', self._on_right_down)
        self.bind('<B3-Motion>', self._on_right_drag)
        self.bind('<ButtonRelease-3>', self._on_right_up)
        self.bind('<Configure>', self._on_resize)
        self.bind('<KeyPress-space>', self._on_space_down)
        self.bind('<KeyRelease-space>', self._on_space_up)
        self.bind('<Escape>', self._on_escape)
        self.focus_set()
    
    # --- Public API ---
    
    def set_context_menu(self, menu: tk.Menu):
        self._context_menu = menu

    def set_image_context_menu_handler(self, handler):
        """Handler(event) for right-click on image blank area (no bbox selected)."""
        self._on_image_context_menu = handler
    
    def set_label_manager(self, label_manager: LabelManager):
        self._label_manager = label_manager

    @property
    def label_mode(self) -> str:
        return self._label_mode

    def set_label_mode(self, mode: str):
        """Set the label display mode (no redraw if unchanged)."""
        if mode not in LABEL_MODE_CYCLE or mode == self._label_mode:
            return
        self._label_mode = mode
        if self._current_image:
            self._redraw()

    def cycle_label_mode(self) -> str:
        """Advance to the next label display mode and redraw. Returns new mode."""
        idx = LABEL_MODE_CYCLE.index(self._label_mode)
        self._label_mode = LABEL_MODE_CYCLE[(idx + 1) % len(LABEL_MODE_CYCLE)]
        if self._current_image:
            self._redraw()
        return self._label_mode

    def set_image(self, image_item: ImageItem, pil_image: Image.Image = None):
        """Set the current image to display."""
        self._current_image = image_item
        self._bbox_items.clear()
        self._hover_bbox_id = None
        
        if pil_image is None and image_item._pil_image is not None:
            pil_image = image_item._pil_image
        
        if pil_image is None:
            self.delete('all')
            self._source_pil_image = None
            self._photo_image = None
            return
        
        # Reference only — avoid copying multi-megapixel images
        self._source_pil_image = pil_image
        self.fit_to_window()
    
    def fit_to_window(self):
        """Fit image to canvas window."""
        if not self._current_image or not self._source_pil_image:
            return
        
        canvas_w = self.winfo_width()
        canvas_h = self.winfo_height()
        if canvas_w <= 1 or canvas_h <= 1:
            self.after(50, self.fit_to_window)
            return
        
        img_w = self._current_image.width
        img_h = self._current_image.height
        
        if img_w <= 0 or img_h <= 0:
            return
        
        scale_x = canvas_w / img_w
        scale_y = canvas_h / img_h
        self._scale = min(scale_x, scale_y) * 0.95
        
        self._offset_x = (canvas_w - img_w * self._scale) / 2
        self._offset_y = (canvas_h - img_h * self._scale) / 2
        
        self._redraw(high_quality=False)
        if self._quality_upgrade_id is not None:
            self.after_cancel(self._quality_upgrade_id)
        self._quality_upgrade_id = self.after(150, self._upgrade_display_quality)
    
    def _upgrade_display_quality(self):
        self._quality_upgrade_id = None
        if self._source_pil_image and self._current_image:
            self._redraw(high_quality=True)
    
    def refresh(self):
        """Refresh the canvas (redraw all)."""
        self._redraw(high_quality=True)

    def deselect_all_annotations(self):
        """Clear bbox selection and redraw."""
        if not self._current_image:
            return
        self._current_image.deselect_all()
        self._redraw()
        if self._on_annotation_changed:
            self._on_annotation_changed()

    def cancel_or_deselect(self):
        """Escape: cancel in-progress draw/select, or clear selection."""
        if self._mode == CanvasMode.DRAWING:
            if self._draw_rect_id:
                self.delete(self._draw_rect_id)
                self._draw_rect_id = None
            self._draw_start = None
            self._mode = CanvasMode.IDLE
            self._clear_press_state()
            self._redraw()
            return
        if self._mode == CanvasMode.SELECTING and self._sel_rect_id:
            self.delete(self._sel_rect_id)
            self._sel_rect_id = None
            self._sel_start = None
            self._mode = CanvasMode.IDLE
            self._clear_press_state()
            return
        self.deselect_all_annotations()
    
    def clear_all(self):
        """Clear all canvas items."""
        if self._quality_upgrade_id is not None:
            self.after_cancel(self._quality_upgrade_id)
            self._quality_upgrade_id = None
        if self._redraw_after_id is not None:
            self.after_cancel(self._redraw_after_id)
            self._redraw_after_id = None
        self.delete('all')
        self._bbox_items.clear()
        self._photo_image = None
        self._source_pil_image = None
        self._current_image = None
    
    @property
    def mode(self) -> CanvasMode:
        return self._mode
    
    @mode.setter
    def mode(self, value: CanvasMode):
        self._mode = value
        if self._on_mode_changed:
            self._on_mode_changed(value)
    
    def canvas_to_image(self, cx: float, cy: float) -> Tuple[float, float]:
        """Convert canvas coordinates to image pixel coordinates."""
        ix = (cx - self._offset_x) / self._scale
        iy = (cy - self._offset_y) / self._scale
        return ix, iy
    
    def image_to_canvas(self, ix: float, iy: float) -> Tuple[float, float]:
        """Convert image pixel coordinates to canvas coordinates."""
        cx = ix * self._scale + self._offset_x
        cy = iy * self._scale + self._offset_y
        return cx, cy
    
    def _schedule_redraw(self, high_quality: bool = False):
        """Debounce redraws during rapid zoom."""
        if high_quality:
            self._redraw_high_quality = True
        if self._redraw_after_id is not None:
            self.after_cancel(self._redraw_after_id)
        self._redraw_after_id = self.after(
            ZOOM_REDRAW_DELAY_MS, self._run_scheduled_redraw
        )
    
    def _run_scheduled_redraw(self):
        self._redraw_after_id = None
        quality = self._redraw_high_quality
        self._redraw_high_quality = False
        self._redraw(high_quality=quality)
    
    def _redraw(self, high_quality: bool = True):
        """Redraw all canvas elements."""
        if high_quality and self._redraw_after_id is not None:
            self.after_cancel(self._redraw_after_id)
            self._redraw_after_id = None
        
        self.delete('all')
        self._bbox_items.clear()
        
        if not self._source_pil_image or not self._current_image:
            return
        
        src_w, src_h = self._source_pil_image.size
        display_w = max(1, int(round(src_w * self._scale)))
        display_h = max(1, int(round(src_h * self._scale)))
        resample = Image.LANCZOS if high_quality else Image.BILINEAR

        canvas_w = self.winfo_width()
        canvas_h = self.winfo_height()

        if canvas_w <= 1 or canvas_h <= 1:
            # Canvas not realized yet: render the whole image (legacy path).
            if (display_w, display_h) != (src_w, src_h):
                display_img = self._source_pil_image.resize(
                    (display_w, display_h), resample
                )
            else:
                display_img = self._source_pil_image
            self._photo_image = ImageTk.PhotoImage(display_img)
            self._image_id = self.create_image(
                self._offset_x, self._offset_y,
                anchor='nw', image=self._photo_image,
            )
        else:
            # Only resize the part of the image visible in the viewport. This
            # keeps the generated bitmap ~canvas-sized at any zoom level, so
            # zooming in past the window edge stays fast.
            crop_x1 = max(0, int(math.floor((0 - self._offset_x) / self._scale)))
            crop_y1 = max(0, int(math.floor((0 - self._offset_y) / self._scale)))
            crop_x2 = min(src_w, int(math.ceil((canvas_w - self._offset_x) / self._scale)))
            crop_y2 = min(src_h, int(math.ceil((canvas_h - self._offset_y) / self._scale)))

            if crop_x2 > crop_x1 and crop_y2 > crop_y1:
                region_w = max(1, int(round((crop_x2 - crop_x1) * self._scale)))
                region_h = max(1, int(round((crop_y2 - crop_y1) * self._scale)))

                full = (crop_x1, crop_y1, crop_x2, crop_y2) == (0, 0, src_w, src_h)
                if full and (region_w, region_h) == (src_w, src_h):
                    display_img = self._source_pil_image
                else:
                    display_img = self._source_pil_image.resize(
                        (region_w, region_h), resample,
                        box=(crop_x1, crop_y1, crop_x2, crop_y2),
                    )

                self._photo_image = ImageTk.PhotoImage(display_img)
                place_x = crop_x1 * self._scale + self._offset_x
                place_y = crop_y1 * self._scale + self._offset_y
                self._image_id = self.create_image(
                    place_x, place_y, anchor='nw', image=self._photo_image,
                )
            else:
                # Image entirely outside the viewport: nothing to blit.
                self._photo_image = None
                self._image_id = None
        
        # Draw annotations (selected last so it renders on top)
        self._draw_all_annotations()
        
        # Redraw crosshair if enabled
        if self._show_crosshair:
            self._crosshair_h = self.create_line(
                0, 0, 0, 0,
                fill=CANVAS_CROSSHAIR_COLOR,
                width=CANVAS_CROSSHAIR_WIDTH,
                dash=CANVAS_CROSSHAIR_DASH,
            )
            self._crosshair_v = self.create_line(
                0, 0, 0, 0,
                fill=CANVAS_CROSSHAIR_COLOR,
                width=CANVAS_CROSSHAIR_WIDTH,
                dash=CANVAS_CROSSHAIR_DASH,
            )

    def _draw_all_annotations(self):
        """Draw every bbox (selected ones last so they render on top)."""
        if not self._current_image:
            return
        anns = self._current_image.annotations
        for i, bbox in enumerate(anns):
            if not bbox.is_selected:
                self._draw_bbox(bbox, i + 1)
        for i, bbox in enumerate(anns):
            if bbox.is_selected:
                self._draw_bbox(bbox, i + 1)

    def _redraw_annotations_only(self):
        """Redraw just the boxes, keeping the image bitmap in place.

        Used during move/resize drags so the (unchanged) image is not
        re-resized every frame, which keeps dragging smooth.
        """
        if not self._current_image:
            return
        self.delete(ANNOTATION_TAG)
        self._bbox_items.clear()
        self._draw_all_annotations()
    
    def _draw_bbox(self, bbox: BBox, index: int):
        """Draw a single bounding box on canvas."""
        if not self._current_image:
            return
        
        class_color = get_color_for_class(bbox.class_id)
        if self._label_manager:
            class_color = self._label_manager.get_color(bbox.class_id)
        
        cx1, cy1 = self.image_to_canvas(bbox.x1, bbox.y1)
        cx2, cy2 = self.image_to_canvas(bbox.x2, bbox.y2)
        
        if bbox.is_selected:
            outline_color = '#ffffff'
            line_width = BBOX_LINE_WIDTH_SELECTED
            # Thin black stroke underneath so the white selection stays visible
            # on light/white backgrounds.
            self.create_rectangle(
                cx1, cy1, cx2, cy2,
                outline='#000000', width=line_width + 2,
                tags=ANNOTATION_TAG,
            )
        else:
            outline_color = class_color
            line_width = BBOX_LINE_WIDTH
        
        rect_id = self.create_rectangle(
            cx1, cy1, cx2, cy2,
            outline=outline_color, width=line_width,
            tags=ANNOTATION_TAG,
        )

        # Decide how much of the label to show, to avoid covering the image
        # when there are many boxes. The active (selected/hovered) box always
        # shows the full label.
        is_active = bbox.is_selected or bbox.id == self._hover_bbox_id
        show_full = self._label_mode == LABEL_MODE_FULL or is_active
        show_compact = (not show_full) and self._label_mode == LABEL_MODE_COMPACT

        text_id = None
        bg_id = None
        if show_full:
            label_text = f'#{index} {bbox.class_name}'
            if bbox.confidence < 1.0:
                label_text += f' {bbox.confidence:.2f}'

            # Plain white text on the solid colored block (no outline), so the
            # font reads thinner. The block already provides enough contrast.
            text_id = self.create_text(
                cx1 + 2, cy1 - 2, anchor='sw',
                text=label_text, fill='#ffffff', font=BBOX_LABEL_FONT,
                tags=ANNOTATION_TAG,
            )

            text_bbox = self.bbox(text_id)
            if text_bbox:
                bg_id = self.create_rectangle(
                    text_bbox[0] - 3, text_bbox[1] - 1,
                    text_bbox[2] + 3, text_bbox[3] + 1,
                    fill=class_color, outline=class_color, width=1,
                    tags=ANNOTATION_TAG,
                )
                self.tag_lower(bg_id, text_id)
                self.tag_lower(bg_id, rect_id)
                self.tag_raise(text_id, bg_id)
        elif show_compact:
            # Just the index, outlined text only (no opaque background)
            text_id, _ = self._draw_outlined_text(cx1 + 2, cy1 - 2, f'#{index}')

        handles = []
        if bbox.is_selected:
            hs = HANDLE_SIZE
            handle_color = class_color
            positions = [
                (cx1, cy1), (cx2, cy1), (cx1, cy2), (cx2, cy2),
                ((cx1 + cx2) / 2, cy1), ((cx1 + cx2) / 2, cy2),
                (cx1, (cy1 + cy2) / 2), (cx2, (cy1 + cy2) / 2),
            ]
            for px, py in positions:
                hid = self.create_rectangle(
                    px - hs, py - hs, px + hs, py + hs,
                    fill='#ffffff', outline=handle_color, width=2,
                    tags=ANNOTATION_TAG,
                )
                handles.append(hid)
        
        self._bbox_items[bbox.id] = {
            'rect': rect_id, 'text': text_id, 'bg': bg_id, 'handles': handles,
        }
    
    def _draw_outlined_text(self, x: float, y: float, text: str, anchor='sw'):
        """Draw label text: black fill with a thin white outline."""
        outline_ids = []
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            oid = self.create_text(
                x + dx, y + dy, anchor=anchor,
                text=text, fill='#ffffff', font=BBOX_LABEL_FONT,
                tags=ANNOTATION_TAG,
            )
            outline_ids.append(oid)
        text_id = self.create_text(
            x, y, anchor=anchor,
            text=text, fill='#000000', font=BBOX_LABEL_FONT,
            tags=ANNOTATION_TAG,
        )
        return text_id, outline_ids
    
    def _on_right_down(self, event):
        """Start right-button drag selection."""
        self.focus_set()
        if not self._current_image or self._space_pressed:
            return
        self._right_down_pos = (event.x, event.y)
        self._right_dragging = False
        self._sel_start = (event.x, event.y)
    
    def _on_right_drag(self, event):
        if not self._current_image or self._right_down_pos is None:
            return
        if not self._right_dragging:
            dx = abs(event.x - self._right_down_pos[0])
            dy = abs(event.y - self._right_down_pos[1])
            if dx > 3 or dy > 3:
                self._right_dragging = True
                self._mode = CanvasMode.SELECTING
                if self._sel_rect_id:
                    self.delete(self._sel_rect_id)
                self._sel_rect_id = self.create_rectangle(
                    self._sel_start[0], self._sel_start[1],
                    event.x, event.y,
                    outline=CANVAS_SELECT_BOX_COLOR,
                    width=CANVAS_SELECT_BOX_WIDTH,
                    dash=CANVAS_SELECT_BOX_DASH,
                )
        if self._mode == CanvasMode.SELECTING and self._sel_rect_id:
            self.coords(
                self._sel_rect_id,
                self._sel_start[0], self._sel_start[1],
                event.x, event.y,
            )
    
    def _on_right_up(self, event):
        if self._right_dragging and self._mode == CanvasMode.SELECTING:
            self._finish_rubber_band_select(event)
        elif self._current_image and self._current_image.get_selected_annotations():
            if self._context_menu:
                try:
                    self._context_menu.tk_popup(event.x_root, event.y_root)
                finally:
                    self._context_menu.grab_release()
        elif self._current_image and self._on_image_context_menu:
            self._on_image_context_menu(event)
        self._right_down_pos = None
        self._right_dragging = False
        self._sel_start = None
    
    def _finish_rubber_band_select(self, event):
        """Complete rubber-band multi-select."""
        if not self._current_image or not self._sel_start:
            self._mode = CanvasMode.IDLE
            return
        
        sel_x1, sel_y1 = self.canvas_to_image(
            min(self._sel_start[0], event.x),
            min(self._sel_start[1], event.y),
        )
        sel_x2, sel_y2 = self.canvas_to_image(
            max(self._sel_start[0], event.x),
            max(self._sel_start[1], event.y),
        )
        
        for bbox in self._current_image.annotations:
            cx, cy = bbox.center()
            bbox.is_selected = (sel_x1 <= cx <= sel_x2 and sel_y1 <= cy <= sel_y2)
        
        if self._sel_rect_id:
            self.delete(self._sel_rect_id)
            self._sel_rect_id = None
        
        self._sel_start = None
        self._mode = CanvasMode.IDLE
        self._redraw()
        if self._on_annotation_changed:
            self._on_annotation_changed()
    
    def _update_crosshair(self, x: float, y: float):
        """Update crosshair position."""
        if self._crosshair_h is not None:
            self.coords(self._crosshair_h, 0, y, self.winfo_width(), y)
        if self._crosshair_v is not None:
            self.coords(self._crosshair_v, x, 0, x, self.winfo_height())

    def _clear_press_state(self):
        self._press_canvas = None
        self._press_image = None
        self._press_bbox = None

    def _press_drag_distance(self, event) -> float:
        if not self._press_canvas:
            return 0.0
        dx = event.x - self._press_canvas[0]
        dy = event.y - self._press_canvas[1]
        return (dx * dx + dy * dy) ** 0.5

    def _start_drawing_from_press(self):
        """Begin a new bbox from a deferred mouse press."""
        if not self._current_image or not self._press_image:
            return
        self._current_image.deselect_all()
        self._mode = CanvasMode.DRAWING
        self._draw_start = self._press_image
        self._redraw()

    def _handle_idle_click(self, event):
        """Click without drag: select bbox or clear selection."""
        if not self._current_image or not self._press_canvas:
            return
        if self._press_drag_distance(event) > self._press_drag_threshold:
            return
        if self._press_bbox:
            if event.state & 0x4:  # Ctrl
                self._press_bbox.is_selected = not self._press_bbox.is_selected
            else:
                self._current_image.deselect_all()
                self._press_bbox.is_selected = True
        else:
            self._current_image.deselect_all()
        self._redraw()
        if self._on_annotation_changed:
            self._on_annotation_changed()

    def _on_escape(self, event=None):
        self.cancel_or_deselect()
        return 'break'
    
    # --- Mouse events ---
    
    def _on_mouse_down(self, event):
        """Handle mouse button press."""
        self.focus_set()
        self._clear_press_state()
        
        # Pan with space+click
        if self._space_pressed:
            self._on_pan_start(event)
            return
        
        if not self._current_image:
            return
        
        img_x, img_y = self.canvas_to_image(event.x, event.y)
        
        if self._mode == CanvasMode.IDLE:
            # Check if clicked on a resize handle of selected bbox
            handle, bbox = self._find_handle(event.x, event.y)
            if handle and bbox:
                self._mode = CanvasMode.RESIZING
                self._resize_handle = handle
                self._resize_bbox = bbox
                self._resize_original = (bbox.x1, bbox.y1, bbox.x2, bbox.y2)
                return

            clicked_bbox = self._find_bbox_at(img_x, img_y)

            if event.state & 0x1:  # Shift — rubber-band multi-select
                self._mode = CanvasMode.SELECTING
                self._sel_start = (event.x, event.y)
                self._sel_rect_id = self.create_rectangle(
                    event.x, event.y, event.x, event.y,
                    outline=CANVAS_SELECT_BOX_COLOR,
                    width=CANVAS_SELECT_BOX_WIDTH,
                    dash=CANVAS_SELECT_BOX_DASH,
                )
                return

            # Only an already-selected bbox can be dragged to move
            if clicked_bbox and clicked_bbox.is_selected:
                self._mode = CanvasMode.MOVING
                self._move_start = (img_x, img_y)
                selected = self._current_image.get_selected_annotations()
                self._move_original = [(b.x1, b.y1, b.x2, b.y2) for b in selected]
                return

            # Otherwise defer: drag draws a new box, click selects/deselects
            self._press_canvas = (event.x, event.y)
            self._press_image = (img_x, img_y)
            self._press_bbox = clicked_bbox
    
    def _on_mouse_drag(self, event):
        """Handle mouse drag."""
        if self._mode == CanvasMode.PANNING:
            self._on_pan_move(event)
            return

        if self._show_crosshair and self._current_image:
            self._update_crosshair(event.x, event.y)

        if self._mode == CanvasMode.IDLE and self._press_canvas:
            if self._press_drag_distance(event) > self._press_drag_threshold:
                self._start_drawing_from_press()
        
        if not self._current_image:
            return
        
        if self._mode == CanvasMode.DRAWING:
            img_x, img_y = self.canvas_to_image(event.x, event.y)
            cx1, cy1 = self.image_to_canvas(*self._draw_start)
            cx2, cy2 = event.x, event.y
            
            if self._draw_rect_id:
                self.delete(self._draw_rect_id)
            
            color = '#0078d7'
            if self._label_manager:
                color = self._label_manager.get_color(self._label_manager.current_class_id)
            
            self._draw_rect_id = self.create_rectangle(
                cx1, cy1, cx2, cy2,
                outline=color,
                width=CANVAS_PREVIEW_BOX_WIDTH,
                dash=CANVAS_PREVIEW_BOX_DASH,
            )
        
        elif self._mode == CanvasMode.MOVING:
            img_x, img_y = self.canvas_to_image(event.x, event.y)
            dx = img_x - self._move_start[0]
            dy = img_y - self._move_start[1]
            
            selected = self._current_image.get_selected_annotations()
            for bbox, orig in zip(selected, self._move_original):
                bbox.x1 = orig[0] + dx
                bbox.y1 = orig[1] + dy
                bbox.x2 = orig[2] + dx
                bbox.y2 = orig[3] + dy
            
            self._redraw_annotations_only()
        
        elif self._mode == CanvasMode.RESIZING:
            img_x, img_y = self.canvas_to_image(event.x, event.y)
            self._resize_bbox.resize(
                self._resize_handle, img_x, img_y,
                self._current_image.width, self._current_image.height
            )
            self._redraw_annotations_only()
        
        elif self._mode == CanvasMode.SELECTING:
            if self._sel_rect_id:
                self.coords(self._sel_rect_id,
                           self._sel_start[0], self._sel_start[1],
                           event.x, event.y)
    
    def _on_mouse_up(self, event):
        """Handle mouse button release."""
        if self._mode == CanvasMode.PANNING:
            self._on_pan_end(event)
            return
        
        if not self._current_image:
            self._mode = CanvasMode.IDLE
            return
        
        if self._mode == CanvasMode.DRAWING:
            if self._draw_start:
                img_x, img_y = self.canvas_to_image(event.x, event.y)
                x1, y1 = self._draw_start
                
                # Clamp to image
                x1 = max(0, min(x1, self._current_image.width))
                y1 = max(0, min(y1, self._current_image.height))
                img_x = max(0, min(img_x, self._current_image.width))
                img_y = max(0, min(img_y, self._current_image.height))
                
                # Check minimum size
                if abs(img_x - x1) >= MIN_BOX_SIZE and abs(img_y - y1) >= MIN_BOX_SIZE:
                    # Normalize coordinates
                    bx1, bx2 = min(x1, img_x), max(x1, img_x)
                    by1, by2 = min(y1, img_y), max(y1, img_y)
                    
                    class_id = self._label_manager.current_class_id if self._label_manager else 0
                    class_name = self._label_manager.get_name(class_id) if self._label_manager else 'object'
                    
                    new_bbox = BBox(
                        x1=bx1, y1=by1, x2=bx2, y2=by2,
                        class_id=class_id, class_name=class_name
                    )
                    
                    if self._on_new_bbox:
                        self._on_new_bbox(new_bbox)
                    else:
                        self._current_image.add_annotation(new_bbox)
                    
                    if self._on_annotation_changed:
                        self._on_annotation_changed()
                
                if self._draw_rect_id:
                    self.delete(self._draw_rect_id)
                    self._draw_rect_id = None
            
        elif self._mode == CanvasMode.MOVING:
            if self._move_start and self._move_original:
                selected = self._current_image.get_selected_annotations()
                changes = []
                for bbox, orig in zip(selected, self._move_original):
                    before = orig
                    after = (bbox.x1, bbox.y1, bbox.x2, bbox.y2)
                    dx = after[0] - before[0]
                    dy = after[1] - before[1]
                    if abs(dx) > 0.5 or abs(dy) > 0.5:
                        changes.append((bbox, before, after))
                if changes:
                    self._current_image.mark_dirty()
                    if self._on_geometry_changed:
                        self._on_geometry_changed('移动标注框', changes)
                    elif self._on_annotation_changed:
                        self._on_annotation_changed()
        
        elif self._mode == CanvasMode.RESIZING:
            if self._resize_bbox and self._resize_original:
                before = self._resize_original
                after = (
                    self._resize_bbox.x1, self._resize_bbox.y1,
                    self._resize_bbox.x2, self._resize_bbox.y2,
                )
                changed = any(
                    abs(a - b) > 0.5 for a, b in zip(before, after)
                )
                if changed:
                    self._current_image.mark_dirty()
                    changes = [(self._resize_bbox, before, after)]
                    if self._on_geometry_changed:
                        self._on_geometry_changed('调整标注框', changes)
                    elif self._on_annotation_changed:
                        self._on_annotation_changed()
        
        elif self._mode == CanvasMode.SELECTING:
            self._finish_rubber_band_select(event)
            return

        if self._mode == CanvasMode.IDLE:
            self._handle_idle_click(event)

        self._mode = CanvasMode.IDLE
        self._draw_start = None
        self._move_start = None
        self._move_original = None
        self._resize_handle = ''
        self._resize_bbox = None
        self._resize_original = None
        self._sel_start = None
        self._clear_press_state()
    
    def _on_mouse_move(self, event):
        """Handle mouse move (hover)."""
        if self._show_crosshair:
            self._update_crosshair(event.x, event.y)
        
        if self._mode != CanvasMode.IDLE or not self._current_image:
            return
        
        # Update cursor shape
        img_x, img_y = self.canvas_to_image(event.x, event.y)

        # Reveal the hovered box's full label in compact/hidden modes
        if self._label_mode != LABEL_MODE_FULL:
            hovered = self._find_bbox_at(img_x, img_y)
            hovered_id = hovered.id if hovered else None
            if hovered_id != self._hover_bbox_id:
                self._hover_bbox_id = hovered_id
                self._redraw()
        
        # Check resize handles
        handle, bbox = self._find_handle(event.x, event.y)
        if handle:
            cursors = {
                'nw': 'top_left_corner', 'ne': 'top_right_corner',
                'sw': 'bottom_left_corner', 'se': 'bottom_right_corner',
                'n': 'top_side', 's': 'bottom_side',
                'w': 'left_side', 'e': 'right_side'
            }
            self.config(cursor=cursors.get(handle, 'arrow'))
            return
        
        # Check if over a selected bbox (only selected boxes can move)
        clicked = self._find_bbox_at(img_x, img_y)
        if clicked and clicked.is_selected:
            self.config(cursor='fleur')
        else:
            self.config(cursor='cross')
    
    def _on_mouse_wheel(self, event):
        """Handle mouse wheel for zoom."""
        if not self._current_image:
            return
        
        if event.delta > 0:
            factor = ZOOM_FACTOR
        else:
            factor = 1 / ZOOM_FACTOR
        
        new_scale = self._scale * factor
        new_scale = max(MIN_ZOOM, min(MAX_ZOOM, new_scale))
        
        self._offset_x = event.x - (event.x - self._offset_x) * (new_scale / self._scale)
        self._offset_y = event.y - (event.y - self._offset_y) * (new_scale / self._scale)
        self._scale = new_scale
        
        self._schedule_redraw(high_quality=False)
        if self._on_zoom_changed:
            self._on_zoom_changed(self._scale)
        
        if self._zoom_idle_id is not None:
            self.after_cancel(self._zoom_idle_id)
        self._zoom_idle_id = self.after(150, self._finalize_zoom)
    
    def _finalize_zoom(self):
        """Sharpen image once zooming stops."""
        self._zoom_idle_id = None
        self._redraw(high_quality=True)
        if self._on_zoom_changed:
            self._on_zoom_changed(self._scale)
    
    def _on_mouse_wheel_linux(self, event):
        """Linux scroll-wheel fallback."""
        event.delta = 120 if event.num == 4 else -120
        self._on_mouse_wheel(event)
    
    # --- Pan ---
    
    def _on_pan_start(self, event):
        self._mode = CanvasMode.PANNING
        self._pan_start = (event.x, event.y)
        self._pan_offset_start = (self._offset_x, self._offset_y)
        self.config(cursor='fleur')
    
    def _on_pan_move(self, event):
        if self._pan_start and self._pan_offset_start:
            dx = event.x - self._pan_start[0]
            dy = event.y - self._pan_start[1]
            self._offset_x = self._pan_offset_start[0] + dx
            self._offset_y = self._pan_offset_start[1] + dy
            self._schedule_redraw(high_quality=False)
    
    def _on_pan_end(self, event):
        self._mode = CanvasMode.IDLE
        self._pan_start = None
        self._pan_offset_start = None
        self.config(cursor='cross')
    
    def _on_space_down(self, event):
        self._space_pressed = True
        self.config(cursor='fleur')
    
    def _on_space_up(self, event):
        self._space_pressed = False
        self.config(cursor='cross')
    
    def _on_resize(self, event):
        """Handle canvas resize."""
        if self._source_pil_image and self._scale <= 0:
            self.fit_to_window()
        elif self._source_pil_image:
            self._schedule_redraw(high_quality=True)
    
    # --- Hit testing ---
    
    def _find_bbox_at(self, img_x: float, img_y: float) -> Optional[BBox]:
        """Find the bbox at the given image coordinates.

        Among all boxes containing the point, prefer the smallest-area one so a
        small box nested inside a larger box is selected. When the point only
        falls inside one box (a non-overlapping area), that box is returned.
        """
        if not self._current_image:
            return None

        candidates = [
            bbox for bbox in self._current_image.annotations
            if bbox.contains_point(img_x, img_y)
        ]
        if not candidates:
            return None

        def _area(bbox: BBox) -> float:
            return abs((bbox.x2 - bbox.x1) * (bbox.y2 - bbox.y1))

        return min(candidates, key=_area)
    
    def _find_handle(self, canvas_x: float, canvas_y: float) -> Tuple[Optional[str], Optional[BBox]]:
        """Find a resize handle at canvas coordinates.

        Hit area is slightly larger than the visible handle. For small boxes,
        a handle only wins when the pointer is closer to that handle than to
        the box center so the interior still shows the move cursor.
        """
        if not self._current_image:
            return None, None

        hit_r = HANDLE_SIZE + HANDLE_HIT_EXTRA
        hit_r_sq = hit_r * hit_r
        best: Tuple[Optional[str], Optional[BBox]] = (None, None)
        best_dist_sq = hit_r_sq + 1

        for bbox in self._current_image.annotations:
            if not bbox.is_selected:
                continue

            cx1, cy1 = self.image_to_canvas(bbox.x1, bbox.y1)
            cx2, cy2 = self.image_to_canvas(bbox.x2, bbox.y2)
            center_x = (cx1 + cx2) / 2
            center_y = (cy1 + cy2) / 2
            center_dist_sq = (canvas_x - center_x) ** 2 + (canvas_y - center_y) ** 2

            positions = {
                'nw': (cx1, cy1), 'ne': (cx2, cy1),
                'sw': (cx1, cy2), 'se': (cx2, cy2),
                'n': ((cx1 + cx2) / 2, cy1), 's': ((cx1 + cx2) / 2, cy2),
                'w': (cx1, (cy1 + cy2) / 2), 'e': (cx2, (cy1 + cy2) / 2),
            }

            for handle_name, (hx, hy) in positions.items():
                handle_dist_sq = (canvas_x - hx) ** 2 + (canvas_y - hy) ** 2
                if handle_dist_sq > hit_r_sq:
                    continue
                if handle_dist_sq >= center_dist_sq:
                    continue
                if handle_dist_sq < best_dist_sq:
                    best_dist_sq = handle_dist_sq
                    best = (handle_name, bbox)

        return best

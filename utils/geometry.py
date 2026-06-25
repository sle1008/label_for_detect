"""Geometry utilities for coordinate transformations."""

from typing import Tuple


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value to a range."""
    return max(min_val, min(max_val, value))


def clamp_bbox(x1: float, y1: float, x2: float, y2: float,
               img_width: int, img_height: int) -> Tuple[float, float, float, float]:
    """Clamp bounding box coordinates to image bounds."""
    x1 = clamp(x1, 0, img_width)
    y1 = clamp(y1, 0, img_height)
    x2 = clamp(x2, 0, img_width)
    y2 = clamp(y2, 0, img_height)
    # Ensure x1 < x2 and y1 < y2
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1
    return x1, y1, x2, y2


def pixel_to_yolo(x1: float, y1: float, x2: float, y2: float,
                  img_width: int, img_height: int) -> Tuple[float, float, float, float]:
    """Convert pixel coordinates to YOLO normalized format (cx, cy, w, h)."""
    cx = ((x1 + x2) / 2.0) / img_width
    cy = ((y1 + y2) / 2.0) / img_height
    w = (x2 - x1) / img_width
    h = (y2 - y1) / img_height
    return cx, cy, w, h


def yolo_to_pixel(cx: float, cy: float, w: float, h: float,
                  img_width: int, img_height: int) -> Tuple[float, float, float, float]:
    """Convert YOLO normalized format to pixel coordinates (x1, y1, x2, y2)."""
    x1 = (cx - w / 2.0) * img_width
    y1 = (cy - h / 2.0) * img_height
    x2 = (cx + w / 2.0) * img_width
    y2 = (cy + h / 2.0) * img_height
    return x1, y1, x2, y2


def point_in_rect(px: float, py: float, x1: float, y1: float,
                  x2: float, y2: float) -> bool:
    """Check if a point is inside a rectangle."""
    return x1 <= px <= x2 and y1 <= py <= y2


def point_on_rect_edge(px: float, py: float, x1: float, y1: float,
                       x2: float, y2: float, tolerance: float = 6.0) -> str:
    """Check if point is near an edge/corner of rectangle.
    
    Returns: handle name ('nw', 'ne', 'sw', 'se', 'n', 's', 'e', 'w') or '' if not on edge.
    """
    near_left = abs(px - x1) <= tolerance
    near_right = abs(px - x2) <= tolerance
    near_top = abs(py - y1) <= tolerance
    near_bottom = abs(py - y2) <= tolerance
    in_x = x1 - tolerance <= px <= x2 + tolerance
    in_y = y1 - tolerance <= py <= y2 + tolerance
    
    # Corners
    if near_left and near_top:
        return 'nw'
    if near_right and near_top:
        return 'ne'
    if near_left and near_bottom:
        return 'sw'
    if near_right and near_bottom:
        return 'se'
    
    # Edges
    if near_top and in_x:
        return 'n'
    if near_bottom and in_x:
        return 's'
    if near_left and in_y:
        return 'w'
    if near_right and in_y:
        return 'e'
    
    return ''


def rects_intersect(r1_x1: float, r1_y1: float, r1_x2: float, r1_y2: float,
                    r2_x1: float, r2_y1: float, r2_x2: float, r2_y2: float) -> bool:
    """Check if two rectangles intersect."""
    return not (r1_x2 < r2_x1 or r1_x1 > r2_x2 or r1_y2 < r2_y1 or r1_y1 > r2_y2)


def resize_bbox(x1: float, y1: float, x2: float, y2: float,
                handle: str, new_x: float, new_y: float,
                img_width: int = None, img_height: int = None) -> Tuple[float, float, float, float]:
    """Resize a bounding box by moving a specific handle to new position."""
    if handle == 'nw':
        x1, y1 = new_x, new_y
    elif handle == 'ne':
        x2, y1 = new_x, new_y
    elif handle == 'sw':
        x1, y2 = new_x, new_y
    elif handle == 'se':
        x2, y2 = new_x, new_y
    elif handle == 'n':
        y1 = new_y
    elif handle == 's':
        y2 = new_y
    elif handle == 'w':
        x1 = new_x
    elif handle == 'e':
        x2 = new_x
    
    # Normalize (ensure x1 < x2, y1 < y2)
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1
    
    # Clamp to image bounds if provided
    if img_width is not None and img_height is not None:
        x1, y1, x2, y2 = clamp_bbox(x1, y1, x2, y2, img_width, img_height)
    
    return x1, y1, x2, y2

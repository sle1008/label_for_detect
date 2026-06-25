"""Bounding box annotation data class."""

import uuid
from dataclasses import dataclass, field
from typing import Optional
import xml.etree.ElementTree as ET

from utils.geometry import (
    pixel_to_yolo, yolo_to_pixel, clamp_bbox, point_in_rect, resize_bbox
)


@dataclass
class BBox:
    """Bounding box annotation.
    
    Stores coordinates in pixel space relative to original image.
    (x1, y1) = top-left, (x2, y2) = bottom-right
    """
    x1: float
    y1: float
    x2: float
    y2: float
    class_id: int
    class_name: str
    confidence: float = 1.0
    is_selected: bool = False
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    
    def normalize(self):
        """Ensure x1 <= x2 and y1 <= y2."""
        if self.x1 > self.x2:
            self.x1, self.x2 = self.x2, self.x1
        if self.y1 > self.y2:
            self.y1, self.y2 = self.y2, self.y1
    
    def clamp_to_image(self, img_width: int, img_height: int):
        """Clamp coordinates to image bounds."""
        self.x1, self.y1, self.x2, self.y2 = clamp_bbox(
            self.x1, self.y1, self.x2, self.y2, img_width, img_height
        )
    
    def contains_point(self, px: float, py: float) -> bool:
        """Check if point is inside this box."""
        return point_in_rect(px, py, self.x1, self.y1, self.x2, self.y2)
    
    def area(self) -> float:
        """Calculate box area in pixels."""
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)
    
    def width(self) -> float:
        return self.x2 - self.x1
    
    def height(self) -> float:
        return self.y2 - self.y1
    
    def center(self) -> tuple:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)
    
    def move(self, dx: float, dy: float):
        """Move the box by (dx, dy) pixels."""
        self.x1 += dx
        self.y1 += dy
        self.x2 += dx
        self.y2 += dy
    
    def resize(self, handle: str, new_x: float, new_y: float,
               img_width: int = None, img_height: int = None):
        """Resize box by moving a handle to new position."""
        self.x1, self.y1, self.x2, self.y2 = resize_bbox(
            self.x1, self.y1, self.x2, self.y2,
            handle, new_x, new_y, img_width, img_height
        )
    
    def to_yolo(self, img_width: int, img_height: int) -> str:
        """Convert to YOLO format line."""
        cx, cy, w, h = pixel_to_yolo(
            self.x1, self.y1, self.x2, self.y2, img_width, img_height
        )
        return f"{self.class_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
    
    def to_coco(self, image_id: int, ann_id: int) -> dict:
        """Convert to COCO annotation format."""
        w = self.x2 - self.x1
        h = self.y2 - self.y1
        return {
            'id': ann_id,
            'image_id': image_id,
            'category_id': self.class_id,
            'bbox': [round(self.x1, 2), round(self.y1, 2), round(w, 2), round(h, 2)],
            'area': round(w * h, 2),
            'iscrowd': 0,
            'segmentation': []
        }
    
    def to_voc_element(self, class_name: str = None) -> ET.Element:
        """Convert to Pascal VOC XML element."""
        name = class_name or self.class_name
        obj = ET.Element('object')
        ET.SubElement(obj, 'name').text = name
        ET.SubElement(obj, 'pose').text = 'Unspecified'
        ET.SubElement(obj, 'truncated').text = '0'
        ET.SubElement(obj, 'difficult').text = '0'
        bndbox = ET.SubElement(obj, 'bndbox')
        ET.SubElement(bndbox, 'xmin').text = str(int(self.x1))
        ET.SubElement(bndbox, 'ymin').text = str(int(self.y1))
        ET.SubElement(bndbox, 'xmax').text = str(int(self.x2))
        ET.SubElement(bndbox, 'ymax').text = str(int(self.y2))
        return obj
    
    @classmethod
    def from_yolo(cls, class_id: int, class_name: str,
                  cx: float, cy: float, w: float, h: float,
                  img_width: int, img_height: int,
                  confidence: float = 1.0) -> 'BBox':
        """Create BBox from YOLO normalized coordinates."""
        x1, y1, x2, y2 = yolo_to_pixel(cx, cy, w, h, img_width, img_height)
        return cls(x1=x1, y1=y1, x2=x2, y2=y2,
                   class_id=class_id, class_name=class_name,
                   confidence=confidence)
    
    def copy(self) -> 'BBox':
        """Create a copy of this bbox."""
        return BBox(
            x1=self.x1, y1=self.y1, x2=self.x2, y2=self.y2,
            class_id=self.class_id, class_name=self.class_name,
            confidence=self.confidence, id=self.id
        )
    
    def __eq__(self, other):
        if not isinstance(other, BBox):
            return False
        return self.id == other.id

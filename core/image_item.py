"""Image item data class."""

from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional
from PIL import Image, ImageTk

from core.annotation import BBox


@dataclass
class ImageItem:
    """Represents an image with its annotations."""
    path: Path
    width: int = 0
    height: int = 0
    annotations: List[BBox] = field(default_factory=list)
    is_dirty: bool = False
    is_loaded: bool = False
    _annotations_loaded: bool = False
    manual_annotation_status: Optional[str] = None  # annotated | unannotated | uncertain
    _pil_image: Optional[Image.Image] = None
    _photo_image: Optional[ImageTk.PhotoImage] = None
    
    def mark_dirty(self):
        self.is_dirty = True
        self._annotated_status_cached = None
    
    def mark_clean(self):
        self.is_dirty = False
        self._annotated_status_cached = None
    
    def annotation_count(self) -> int:
        return len(self.annotations)
    
    def get_selected_annotations(self) -> List[BBox]:
        return [ann for ann in self.annotations if ann.is_selected]
    
    def deselect_all(self):
        for ann in self.annotations:
            ann.is_selected = False
    
    def select_all(self):
        for ann in self.annotations:
            ann.is_selected = True
    
    def inverse_selection(self):
        for ann in self.annotations:
            ann.is_selected = not ann.is_selected
    
    def remove_selected(self) -> List[BBox]:
        """Remove and return selected annotations."""
        removed = [ann for ann in self.annotations if ann.is_selected]
        if not removed:
            return removed
        self.annotations = [ann for ann in self.annotations if not ann.is_selected]
        self.mark_dirty()
        return removed
    
    def add_annotation(self, bbox: BBox):
        self.manual_annotation_status = None
        self.annotations.append(bbox)
        self.mark_dirty()
    
    def remove_annotation(self, bbox: BBox) -> Optional[BBox]:
        if bbox in self.annotations:
            self.annotations.remove(bbox)
            self.mark_dirty()
            return bbox
        return None
    
    def clear_annotations(self):
        self.annotations.clear()
        self.mark_dirty()
    
    @property
    def name(self) -> str:
        return self.path.name
    
    @property
    def stem(self) -> str:
        return self.path.stem

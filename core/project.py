"""Project state management."""

import os
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

from core.image_item import ImageItem
from io_ops.annotation_status import (
    get_image_category, is_image_annotated, annotation_file_contains_class,
    resolve_annotation_txt_path,
    IMAGE_CATEGORY_ANNOTATED, IMAGE_CATEGORY_UNANNOTATED,
)
from utils.constants import IMAGE_EXTENSIONS


class ImageFilter(Enum):
    ALL = 'all'
    ANNOTATED = 'annotated'
    UNANNOTATED = 'unannotated'
    UNCERTAIN = 'uncertain'


@dataclass
class Project:
    """Holds the current project state."""
    
    image_list: List[ImageItem] = field(default_factory=list)
    current_index: int = -1
    image_dir: Optional[Path] = None
    is_modified: bool = False
    image_filter: ImageFilter = ImageFilter.ALL
    label_filter_class_id: Optional[int] = None
    _label_contains_cache: dict = field(default_factory=dict, repr=False)
    _filtered_indices_cache: Optional[List[int]] = field(default=None, repr=False)
    _visible_indices_snapshot: Optional[List[int]] = field(default=None, repr=False)
    
    @property
    def current_image(self) -> Optional[ImageItem]:
        if 0 <= self.current_index < len(self.image_list):
            return self.image_list[self.current_index]
        return None
    
    @property
    def total_images(self) -> int:
        return len(self.image_list)
    
    @property
    def has_images(self) -> bool:
        return len(self.image_list) > 0
    
    @staticmethod
    def _is_image_file(path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    
    @staticmethod
    def scan_image_paths(dir_path: str) -> List[Path]:
        """Scan all image paths under a directory without loading pixels."""
        root = Path(dir_path)
        if not root.is_dir():
            return []
        
        files: List[Path] = []
        try:
            for dirpath, _, filenames in os.walk(root):
                for name in filenames:
                    path = Path(dirpath) / name
                    if Project._is_image_file(path):
                        files.append(path)
        except OSError:
            pass
        
        return sorted(set(files))
    
    def load_directory(self, dir_path: str) -> int:
        """Load images from a directory (path scan only, no decode)."""
        paths = self.scan_image_paths(dir_path)
        return self.set_image_paths(dir_path, paths)
    
    def set_image_paths(self, dir_path: str, paths: List[Path]) -> int:
        """Replace image list from pre-scanned paths."""
        self.image_dir = Path(dir_path)
        self.image_list = [ImageItem(path=p) for p in paths]
        self.current_index = 0 if self.image_list else -1
        self._visible_indices_snapshot = None
        self.invalidate_filter_cache()
        return len(self.image_list)

    def invalidate_filter_cache(self):
        self._filtered_indices_cache = None

    def invalidate_label_cache(self):
        self._label_contains_cache.clear()
        self._filtered_indices_cache = None

    def cache_label_contains(self, path: Path, class_ids: set):
        self._label_contains_cache[str(path.resolve())] = set(class_ids)

    def label_contains_class(self, item: ImageItem, class_id: int) -> bool:
        key = str(item.path.resolve())
        cached = self._label_contains_cache.get(key)
        if cached is not None:
            return class_id in cached
        if item.annotations:
            class_ids = {ann.class_id for ann in item.annotations}
            self._label_contains_cache[key] = class_ids
            return class_id in class_ids
        txt_path = resolve_annotation_txt_path(item.path)
        if txt_path is None:
            self._label_contains_cache[key] = set()
            return False
        class_ids = set()
        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split()
                    if parts:
                        class_ids.add(int(float(parts[0])))
        except Exception:
            pass
        self._label_contains_cache[key] = class_ids
        return class_id in class_ids

    def lingers_in_unannotated_while_editing(self, index: int, item: ImageItem) -> bool:
        """Keep the current image in the unannotated filter while it is being edited."""
        if self.image_filter != ImageFilter.UNANNOTATED:
            return False
        if index != self.current_index:
            return False
        if get_image_category(item) != IMAGE_CATEGORY_ANNOTATED:
            return False
        return item.annotation_count() > 0

    def matches_image_filter(self, index: int, item: ImageItem) -> bool:
        status_matches = self.image_filter == ImageFilter.ALL
        if not status_matches:
            category = get_image_category(item)
            status_matches = category == self.image_filter.value
            if not status_matches:
                status_matches = self.lingers_in_unannotated_while_editing(index, item)
        if not status_matches:
            return False
        if self.label_filter_class_id is None:
            return True
        return self.label_contains_class(item, self.label_filter_class_id)

    def get_filtered_indices(self) -> List[int]:
        """Indices into image_list that match the active filters, in original order."""
        cacheable = self.image_filter != ImageFilter.UNANNOTATED
        if cacheable and self._filtered_indices_cache is not None:
            return self._filtered_indices_cache

        indices: List[int] = []
        for i, item in enumerate(self.image_list):
            if self.matches_image_filter(i, item):
                indices.append(i)
        if cacheable:
            self._filtered_indices_cache = indices
        return indices

    def set_visible_indices(self, indices: List[int]):
        """Freeze navigation to the image indices currently shown in the UI."""
        self._visible_indices_snapshot = list(indices)

    def get_visible_indices(self) -> List[int]:
        """Return the current UI list snapshot, or the active filter result."""
        if self._visible_indices_snapshot is not None:
            return list(self._visible_indices_snapshot)
        return list(self.get_filtered_indices())

    def _visible_indices(self) -> List[int]:
        return self.get_visible_indices()
    
    def next_image(self) -> bool:
        """Move to next image in the current filter. Returns True if moved."""
        indices = self._visible_indices()
        if not indices:
            return False

        if self.current_index in indices:
            pos = indices.index(self.current_index)
            if pos < len(indices) - 1:
                self.current_index = indices[pos + 1]
                return True
            return False

        for idx in indices:
            if idx > self.current_index:
                self.current_index = idx
                return True
        return False
    
    def prev_image(self) -> bool:
        """Move to previous image in the current filter. Returns True if moved."""
        indices = self._visible_indices()
        if not indices:
            return False

        if self.current_index in indices:
            pos = indices.index(self.current_index)
            if pos > 0:
                self.current_index = indices[pos - 1]
                return True
            return False

        for idx in reversed(indices):
            if idx < self.current_index:
                self.current_index = idx
                return True
        return False
    
    def goto_image(self, index: int) -> bool:
        """Go to specific image index. Returns True if moved."""
        if 0 <= index < len(self.image_list):
            self.current_index = index
            return True
        return False
    
    def goto_first(self) -> bool:
        indices = self._visible_indices()
        if indices:
            self.current_index = indices[0]
            return True
        return False
    
    def goto_last(self) -> bool:
        indices = self._visible_indices()
        if indices:
            self.current_index = indices[-1]
            return True
        return False
    
    def total_annotations(self) -> int:
        """Get total annotation count across all images."""
        return sum(img.annotation_count() for img in self.image_list)
    
    def annotated_image_count(self) -> int:
        """Get count of images with at least one annotation."""
        return sum(1 for img in self.image_list if is_image_annotated(img))

    def next_filtered_index_after(self, full_index: int) -> Optional[int]:
        """Return the next filtered image index after full_index in sort order."""
        for idx in self.get_filtered_indices():
            if idx > full_index:
                return idx
        return None

    def adjacent_visible_item_for_removal(self, full_index: int) -> Optional[ImageItem]:
        """Pick the visible next item, or the previous item when removing the last."""
        indices = self.get_visible_indices()
        if full_index not in indices:
            return None
        position = indices.index(full_index)
        if position + 1 < len(indices):
            return self.image_list[indices[position + 1]]
        if position > 0:
            return self.image_list[indices[position - 1]]
        return None

    def remove_image_at(self, index: int) -> int:
        """Remove an image from the list. Returns the new current_index."""
        if not (0 <= index < len(self.image_list)):
            return self.current_index

        del self.image_list[index]
        self._visible_indices_snapshot = None
        self.invalidate_filter_cache()

        if not self.image_list:
            self.current_index = -1
            return -1

        if index < self.current_index:
            self.current_index -= 1
        elif index == self.current_index:
            self.current_index = min(index, len(self.image_list) - 1)

        return self.current_index
    
    @staticmethod
    def resolve_refresh_index(
        new_paths: List[Path],
        prior_paths: List[Path],
        current_path: Optional[Path],
        current_index: int,
    ) -> int:
        """Pick the image index to show after refreshing an open directory.

        Keeps the same file when it still exists; otherwise the nearest prior
        image in the previous sorted order; otherwise 0.
        """
        if not new_paths:
            return -1

        if current_path is not None:
            current_resolved = current_path.resolve()
            for i, path in enumerate(new_paths):
                if path.resolve() == current_resolved:
                    return i

        if prior_paths and 0 <= current_index < len(prior_paths):
            for j in range(current_index - 1, -1, -1):
                prev_resolved = prior_paths[j].resolve()
                for i, path in enumerate(new_paths):
                    if path.resolve() == prev_resolved:
                        return i

        return 0

    def clear(self):
        """Clear the project."""
        self.image_list.clear()
        self.current_index = -1
        self.image_dir = None
        self.is_modified = False
        self._visible_indices_snapshot = None
        self.invalidate_filter_cache()

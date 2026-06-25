"""Async image loader with memory-budget LRU cache."""

import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, List, Optional, Set

from PIL import Image

from core.image_item import ImageItem
from utils.constants import (
    MAX_CACHE_BYTES, PRELOAD_FORWARD, PRELOAD_BACKWARD,
)


def _estimate_image_bytes(img: Image.Image) -> int:
    w, h = img.size
    return w * h * max(len(img.getbands()), 3)


class AsyncImageLoader:
    """Loads images asynchronously with byte-budget LRU caching."""

    def __init__(
        self,
        max_workers: int = 8,
        max_bytes: int = MAX_CACHE_BYTES,
    ):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._cache: OrderedDict[str, Image.Image] = OrderedDict()
        self._cache_bytes = 0
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._pinned_paths: Set[str] = set()

    def load_image_sync(self, image_item: ImageItem) -> bool:
        """Load an image synchronously (blocking)."""
        try:
            if self._ensure_loaded(image_item):
                return True
            return False
        except Exception as e:
            print(f"Failed to load {image_item.path}: {e}")
            return False

    def load_image_async(
        self,
        image_item: ImageItem,
        callback: Callable[[ImageItem, bool], None],
        root=None,
        main_thread_schedule: Callable[[Callable], None] = None,
    ):
        """Load an image asynchronously."""

        def do_load():
            success = self._ensure_loaded(image_item)
            if main_thread_schedule:
                main_thread_schedule(lambda: callback(image_item, success))
            elif root:
                root.after(0, callback, image_item, success)
            else:
                callback(image_item, success)

        self._executor.submit(do_load)

    def _ensure_loaded(self, item: ImageItem) -> bool:
        if item.is_loaded and item._pil_image is not None:
            return True

        path_str = str(item.path)
        with self._lock:
            if path_str in self._cache:
                img = self._cache[path_str]
                self._cache.move_to_end(path_str)
            else:
                img = None

        if img is None:
            if not self._load_pil_image(item.path):
                return False
            with self._lock:
                img = self._cache.get(path_str)
            if img is None:
                return False

        item.width = img.width
        item.height = img.height
        item._pil_image = img
        item.is_loaded = True
        return True

    def _load_pil_image(self, path: Path) -> bool:
        """Load PIL image and add to cache. Thread-safe."""
        path_str = str(path)

        with self._lock:
            if path_str in self._cache:
                self._cache.move_to_end(path_str)
                return True

        try:
            img = Image.open(path)
            img.load()
            img_bytes = _estimate_image_bytes(img)

            with self._lock:
                if path_str not in self._cache:
                    self._cache[path_str] = img
                    self._cache_bytes += img_bytes
                    self._evict_if_needed()
                self._cache.move_to_end(path_str)
            return True
        except Exception as e:
            print(f"Failed to load {path}: {e}")
            return False

    def get_cached(self, path: Path) -> Optional[Image.Image]:
        """Get cached PIL image."""
        path_str = str(path)
        with self._lock:
            if path_str in self._cache:
                self._cache.move_to_end(path_str)
                return self._cache[path_str]
        return None

    def preload_neighbors(
        self,
        current_index: int,
        image_list: List[ImageItem],
        forward: int = PRELOAD_FORWARD,
        backward: int = PRELOAD_BACKWARD,
    ):
        """Preload current window: backward + current + forward images."""
        if not image_list:
            return

        start = max(0, current_index - backward)
        end = min(len(image_list), current_index + forward + 1)

        pinned = {str(image_list[i].path) for i in range(start, end)}
        with self._lock:
            self._pinned_paths = pinned

        for i in range(start, end):
            item = image_list[i]
            self._executor.submit(self._preload_item, item)

    def _preload_item(self, item: ImageItem):
        try:
            self._ensure_loaded(item)
        except Exception as e:
            print(f"Preload failed for {item.path}: {e}")

    def _evict_if_needed(self):
        """Evict oldest unpinned entries when over byte budget. Must hold lock."""
        while self._cache_bytes > self._max_bytes and len(self._cache) > 1:
            evicted = False
            for path_str in list(self._cache.keys()):
                if path_str in self._pinned_paths:
                    continue
                img = self._cache.pop(path_str)
                self._cache_bytes -= _estimate_image_bytes(img)
                evicted = True
                break
            if not evicted:
                break

    def release_outside_window(
        self,
        image_list: List[ImageItem],
        center_index: int,
        forward: int = PRELOAD_FORWARD,
        backward: int = PRELOAD_BACKWARD,
    ):
        """Drop decoded pixels for images outside the preload window."""
        if not image_list:
            return

        start = max(0, center_index - backward)
        end = min(len(image_list), center_index + forward + 1)
        keep_paths = {str(image_list[i].path) for i in range(start, end)}

        for item in image_list:
            if str(item.path) not in keep_paths:
                item._pil_image = None
                item.is_loaded = False

        with self._lock:
            self._pinned_paths = keep_paths
            self._evict_if_needed()

    def clear_cache(self):
        """Clear loader cache and drop item references."""
        with self._lock:
            self._cache.clear()
            self._cache_bytes = 0
            self._pinned_paths.clear()

    def evict_path(self, path: Path):
        """Remove one path from the decode cache."""
        path_str = str(path)
        with self._lock:
            img = self._cache.pop(path_str, None)
            if img is not None:
                self._cache_bytes -= _estimate_image_bytes(img)
            self._pinned_paths.discard(path_str)

    def cancel_all(self):
        """Cancel pending loads."""
        self._cancel_event.set()

    def shutdown(self):
        """Shutdown the executor."""
        self._executor.shutdown(wait=False)
        self.clear_cache()

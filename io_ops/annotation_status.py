"""Helpers for determining whether an image has annotations."""

import json
from pathlib import Path
from typing import Dict, Optional

from core.image_item import ImageItem

STATUS_FILENAME = '.annotation_status.json'

IMAGE_CATEGORY_ANNOTATED = 'annotated'
IMAGE_CATEGORY_UNANNOTATED = 'unannotated'
IMAGE_CATEGORY_UNCERTAIN = 'uncertain'
VALID_IMAGE_CATEGORIES = frozenset({
    IMAGE_CATEGORY_ANNOTATED,
    IMAGE_CATEGORY_UNANNOTATED,
    IMAGE_CATEGORY_UNCERTAIN,
})


def resolve_annotation_txt_path(image_path: Path) -> Optional[Path]:
    """Return the YOLO label file path for an image, if it exists."""
    txt_path = image_path.with_suffix('.txt')

    if not txt_path.exists():
        labels_dir = image_path.parent.parent / 'labels' / image_path.parent.name
        if labels_dir.exists():
            candidate = labels_dir / (image_path.stem + '.txt')
            if candidate.exists():
                return candidate

    if not txt_path.exists():
        labels_dir = image_path.parent.parent / 'labels'
        if labels_dir.exists():
            candidate = labels_dir / (image_path.stem + '.txt')
            if candidate.exists():
                return candidate

    return txt_path if txt_path.exists() else None


def label_file_exists(item: ImageItem) -> bool:
    """True when a label (.txt) file exists for the image, even if empty."""
    return resolve_annotation_txt_path(item.path) is not None


def natural_has_annotations(item: ImageItem) -> bool:
    """True when boxes exist in memory or a label file exists on disk.

    A label file is treated as 'annotated' even when empty, matching the
    YOLO convention where an empty .txt marks a background/negative sample.
    """
    if item.annotation_count() > 0:
        return True
    if item.is_dirty or item._annotations_loaded:
        # In-session edits are authoritative, but an existing label file
        # (e.g. an empty/background sample) still counts as annotated.
        return label_file_exists(item)
    cached = getattr(item, '_annotated_status_cached', None)
    if cached is not None:
        return cached
    result = label_file_exists(item)
    item._annotated_status_cached = result
    return result


def get_image_category(item: ImageItem) -> str:
    """Return annotated / unannotated / uncertain category for filtering."""
    manual = getattr(item, 'manual_annotation_status', None)
    if manual in VALID_IMAGE_CATEGORIES:
        return manual
    if natural_has_annotations(item):
        return IMAGE_CATEGORY_ANNOTATED
    return IMAGE_CATEGORY_UNANNOTATED


def is_image_annotated(item: ImageItem) -> bool:
    """True when the image is in the annotated category."""
    return get_image_category(item) == IMAGE_CATEGORY_ANNOTATED


def invalidate_annotation_status(item: ImageItem):
    """Clear cached on-disk annotation status for one image."""
    item._annotated_status_cached = None


def load_manual_statuses(image_dir: Path) -> Dict[str, str]:
    """Load persisted manual annotation statuses for a directory.

    Returns a dict mapping posix-relative-path -> status string.
    """
    status_file = Path(image_dir) / STATUS_FILENAME
    if not status_file.exists():
        return {}
    try:
        with open(status_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if v in VALID_IMAGE_CATEGORIES}
    except Exception as e:
        print(f"Failed to load annotation statuses: {e}")
        return {}


def save_manual_statuses(image_dir: Path, statuses: Dict[str, str]) -> None:
    """Persist manual annotation statuses for a directory.

    ``statuses`` maps posix-relative-path -> status string.
    An empty dict removes the file.
    """
    status_file = Path(image_dir) / STATUS_FILENAME
    if not statuses:
        try:
            status_file.unlink(missing_ok=True)
        except Exception:
            pass
        return
    try:
        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(statuses, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to save annotation statuses: {e}")

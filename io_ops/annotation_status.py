"""Helpers for determining whether an image has annotations."""

import json
from pathlib import Path
from typing import Dict, Optional, Sequence

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
IMAGE_FOLDER_NAMES = frozenset({'images', 'image', 'imgs'})


def candidate_annotation_txt_paths(image_path: Path) -> list[Path]:
    """Return possible YOLO label paths for an image, ordered by preference."""
    stem_txt = image_path.stem + '.txt'
    paths = [image_path.with_suffix('.txt')]

    parent = image_path.parent
    grandparent = parent.parent
    great_grandparent = grandparent.parent

    # YOLO flat split: test/images/img.jpg -> test/labels/img.txt
    paths.append(grandparent / 'labels' / stem_txt)

    # YOLO class-subfolder split:
    # test/images/class/img.jpg -> test/labels/img.txt
    paths.append(great_grandparent / 'labels' / stem_txt)

    # YOLO mirrored class folders:
    # test/images/class/img.jpg -> test/labels/class/img.txt
    paths.append(grandparent / 'labels' / parent.name / stem_txt)
    paths.append(great_grandparent / 'labels' / parent.name / stem_txt)

    unique_paths = []
    seen = set()
    for path in paths:
        key = path.resolve() if path.exists() else path.absolute()
        if key not in seen:
            seen.add(key)
            unique_paths.append(path)
    return unique_paths


def resolve_annotation_txt_path(image_path: Path) -> Optional[Path]:
    """Return the YOLO label file path for an image, if it exists."""
    for txt_path in candidate_annotation_txt_paths(image_path):
        if txt_path.exists():
            return txt_path
    return None


def preferred_annotation_txt_path(image_path: Path) -> Path:
    """Return where a new YOLO label file should be written for an image."""
    existing = resolve_annotation_txt_path(image_path)
    if existing is not None:
        return existing

    parent = image_path.parent
    grandparent = parent.parent
    great_grandparent = grandparent.parent
    stem_txt = image_path.stem + '.txt'

    if grandparent.name.lower() in IMAGE_FOLDER_NAMES:
        return great_grandparent / 'labels' / stem_txt
    if parent.name.lower() in IMAGE_FOLDER_NAMES:
        return grandparent / 'labels' / stem_txt

    return image_path.with_suffix('.txt')


def label_class_folder_for_image(image_path: Path) -> Optional[Path]:
    """Return the target label folder for class-based datasets, if any."""
    parent = image_path.parent
    grandparent = parent.parent
    great_grandparent = grandparent.parent

    if grandparent.name.lower() in IMAGE_FOLDER_NAMES:
        return grandparent / 'labels' / parent.name
    if parent.name.lower() in IMAGE_FOLDER_NAMES:
        return grandparent / 'labels' / parent.name
    return None


def infer_label_category_from_annotations(annotation_names: Sequence[str]) -> Optional[str]:
    """Infer the image category name from annotations using dominant class rules."""
    if not annotation_names:
        return None
    counts: Dict[str, int] = {}
    first_seen: Dict[str, int] = {}
    for idx, name in enumerate(annotation_names):
        counts[name] = counts.get(name, 0) + 1
        first_seen.setdefault(name, idx)
    return max(counts.keys(), key=lambda name: (counts[name], -first_seen[name]))


def annotation_file_contains_class(item: ImageItem, class_id: int) -> bool:
    """Return true when the image's YOLO label file contains class_id."""
    if item._annotations_loaded or item.is_dirty:
        return any(ann.class_id == class_id for ann in item.annotations)

    txt_path = resolve_annotation_txt_path(item.path)
    if txt_path is None:
        return False
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split()
                if parts and int(float(parts[0])) == class_id:
                    return True
    except Exception:
        return False
    return False


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

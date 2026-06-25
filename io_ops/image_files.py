"""Delete image and associated label files from disk."""

from pathlib import Path
from typing import Tuple

from core.image_item import ImageItem
from io_ops.annotation_status import resolve_annotation_txt_path


def delete_image_and_labels(item: ImageItem) -> Tuple[bool, str]:
    """Delete the image file and its YOLO label file(s). Returns (ok, error_message)."""
    errors = []
    path = item.path

    if path.is_file():
        try:
            path.unlink()
        except OSError as e:
            errors.append(f'无法删除图片: {e}')

    txt_paths = set()
    resolved = resolve_annotation_txt_path(path)
    if resolved:
        txt_paths.add(resolved)
    sidecar = path.with_suffix('.txt')
    if sidecar.is_file():
        txt_paths.add(sidecar)

    for txt in txt_paths:
        try:
            txt.unlink()
        except OSError as e:
            errors.append(f'无法删除标注文件: {e}')

    if path.is_file():
        return False, '\n'.join(errors) if errors else '图片未能删除'
    return True, '\n'.join(errors) if errors else ''


def delete_annotation_files(image_path: Path) -> None:
    """Remove label files for an image without deleting the image."""
    txt_paths = set()
    resolved = resolve_annotation_txt_path(image_path)
    if resolved:
        txt_paths.add(resolved)
    sidecar = image_path.with_suffix('.txt')
    if sidecar.is_file():
        txt_paths.add(sidecar)
    for txt in txt_paths:
        try:
            txt.unlink()
        except OSError:
            pass

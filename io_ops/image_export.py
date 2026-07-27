"""Copy or move selected images and their YOLO label files."""

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Callable, Iterable, Optional

from core.image_item import ImageItem
from io_ops.annotation_status import resolve_annotation_txt_path


EXPORT_MODE_COPY = 'copy'
EXPORT_MODE_MOVE = 'move'
VALID_EXPORT_MODES = frozenset({EXPORT_MODE_COPY, EXPORT_MODE_MOVE})
CONFLICT_SUFFIX = 'suffix'
CONFLICT_SKIP = 'skip'
VALID_CONFLICT_POLICIES = frozenset({CONFLICT_SUFFIX, CONFLICT_SKIP})


@dataclass
class ImageExportResult:
    """Result for one exported image and its optional label file."""

    item: ImageItem
    success: bool
    image_destination: Optional[Path] = None
    label_destination: Optional[Path] = None
    source_removed: bool = False
    skipped: bool = False
    warning: str = ''
    error: str = ''


def _occupied_stems(*directories: Path) -> set[str]:
    stems: set[str] = set()
    for directory in directories:
        try:
            stems.update(
                path.stem.casefold()
                for path in directory.iterdir()
                if path.is_file()
            )
        except OSError:
            continue
    return stems


def _allocate_stem(source_stem: str, occupied: set[str]) -> str:
    candidate = source_stem
    suffix = 2
    while candidate.casefold() in occupied:
        candidate = f'{source_stem}_{suffix}'
        suffix += 1
    return candidate


def _remove_destination(path: Optional[Path]):
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def find_export_conflicts(
    items: Iterable[ImageItem], output_dir: Path,
) -> list[ImageItem]:
    """Return items whose image stem already exists in the export target.

    Duplicate stems within the selected batch are included too, because a
    flat ``images``/``labels`` export cannot preserve both under one name.
    """
    output_dir = Path(output_dir)
    occupied = _occupied_stems(output_dir / 'images', output_dir / 'labels')
    conflicts = []
    for item in items:
        stem = item.stem.casefold()
        if stem in occupied:
            conflicts.append(item)
        else:
            occupied.add(stem)
    return conflicts


def _export_one(
    item: ImageItem,
    images_dir: Path,
    labels_dir: Path,
    mode: str,
    occupied: set[str],
    conflict_policy: str,
) -> ImageExportResult:
    source_image = item.path
    if not source_image.is_file():
        return ImageExportResult(
            item=item,
            success=False,
            error='源图片不存在或无法访问',
        )

    if source_image.stem.casefold() in occupied and conflict_policy == CONFLICT_SKIP:
        return ImageExportResult(
            item=item,
            success=False,
            skipped=True,
            error='目标位置存在同名文件，已跳过',
        )
    source_label = resolve_annotation_txt_path(source_image)
    target_stem = _allocate_stem(source_image.stem, occupied)
    target_image = images_dir / f'{target_stem}{source_image.suffix}'
    target_label = labels_dir / f'{target_stem}.txt' if source_label else None

    try:
        shutil.copy2(source_image, target_image)
        if source_label is not None:
            shutil.copy2(source_label, target_label)
    except OSError as exc:
        _remove_destination(target_label)
        _remove_destination(target_image)
        return ImageExportResult(
            item=item,
            success=False,
            error=f'复制文件失败: {exc}',
        )

    occupied.add(target_stem.casefold())
    if mode == EXPORT_MODE_COPY:
        return ImageExportResult(
            item=item,
            success=True,
            image_destination=target_image,
            label_destination=target_label,
        )

    try:
        source_image.unlink()
    except OSError as exc:
        occupied.discard(target_stem.casefold())
        _remove_destination(target_label)
        _remove_destination(target_image)
        return ImageExportResult(
            item=item,
            success=False,
            error=f'无法删除源图片，已撤销本张移动: {exc}',
        )

    warning = ''
    if source_label is not None:
        try:
            source_label.unlink()
        except OSError as exc:
            warning = f'源标签文件未能删除: {exc}'

    return ImageExportResult(
        item=item,
        success=True,
        image_destination=target_image,
        label_destination=target_label,
        source_removed=True,
        warning=warning,
    )


def export_images_and_labels(
    items: Iterable[ImageItem],
    output_dir: Path,
    mode: str = EXPORT_MODE_COPY,
    progress: Optional[Callable[[int, int, ImageItem], None]] = None,
    conflict_policy: str = CONFLICT_SUFFIX,
) -> list[ImageExportResult]:
    """Export images into ``images`` and labels into ``labels``.

    Existing destination files are never overwritten. Items sharing a stem
    receive matching numeric suffixes in both destination directories.
    """
    if mode not in VALID_EXPORT_MODES:
        raise ValueError(f'Unsupported image export mode: {mode}')
    if conflict_policy not in VALID_CONFLICT_POLICIES:
        raise ValueError(f'Unsupported export conflict policy: {conflict_policy}')

    items = list(items)
    output_dir = Path(output_dir)
    images_dir = output_dir / 'images'
    labels_dir = output_dir / 'labels'
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    occupied = _occupied_stems(images_dir, labels_dir)

    results = []
    total = len(items)
    for completed, item in enumerate(items, start=1):
        results.append(
            _export_one(
                item, images_dir, labels_dir, mode, occupied, conflict_policy,
            )
        )
        if progress is not None:
            progress(completed, total, item)
    return results

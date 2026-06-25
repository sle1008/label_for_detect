"""Detect class-per-subfolder dataset layouts and export label lists."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from utils.constants import IMAGE_EXTENSIONS

# Common directory names that are usually splits/containers, not class labels.
NON_CLASS_DIR_NAMES = frozenset({
    'images', 'image', 'imgs', 'pictures', 'pics', 'photos',
    'labels', 'label', 'annotations', 'annotation', 'anns', 'gt',
    'train', 'training', 'val', 'valid', 'validation', 'test', 'testing',
    'data', 'dataset', 'raw', 'processed', 'output', 'exports', 'export',
    'tmp', 'temp', 'cache', 'checkpoints', 'weights', 'models',
    '__pycache__', '.git', 'background', 'negatives', 'hard',
})


@dataclass
class ClassFolderInfo:
    name: str
    image_count: int
    median_depth: float


@dataclass
class FolderLabelDetection:
    detected: bool
    confidence: str  # high | medium | low
    class_names: List[str] = field(default_factory=list)
    class_infos: List[ClassFolderInfo] = field(default_factory=list)
    reason: str = ''
    tree_summary: str = ''
    root_image_count: int = 0


def _is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS


def _count_images_under(folder: Path) -> Tuple[int, List[int]]:
    """Return (count, file depths relative to folder)."""
    count = 0
    depths: List[int] = []
    for path in folder.rglob('*'):
        if _is_image(path):
            count += 1
            depths.append(len(path.relative_to(folder).parts) - 1)
    return count, depths


def infer_immediate_subfolder_name(image_path: Path, root_dir: Path) -> Optional[str]:
    """First path segment under root — e.g. root/bear/img.jpg -> bear."""
    try:
        rel = image_path.resolve().relative_to(root_dir.resolve())
    except ValueError:
        return None
    if len(rel.parts) >= 2:
        return rel.parts[0]
    return None


def detect_class_folder_layout(root: Path) -> FolderLabelDetection:
    """Guess whether immediate subfolders are class names."""
    root = Path(root)
    if not root.is_dir():
        return FolderLabelDetection(False, 'low', reason='路径不是目录')

    children = sorted(
        [p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')],
        key=lambda p: p.name.lower(),
    )
    root_images = [p for p in root.iterdir() if _is_image(p)]

    if not children:
        return FolderLabelDetection(
            False, 'low', reason='根目录下没有子文件夹',
            root_image_count=len(root_images),
        )

    blocklist_hits = [c.name for c in children if c.name.lower() in NON_CLASS_DIR_NAMES]
    class_infos: List[ClassFolderInfo] = []

    for child in children:
        if child.name.lower() in NON_CLASS_DIR_NAMES:
            continue
        img_count, depths = _count_images_under(child)
        if img_count <= 0:
            continue
        median_depth = sorted(depths)[len(depths) // 2] if depths else 0
        class_infos.append(ClassFolderInfo(child.name, img_count, float(median_depth)))

    n_subdirs = len(children)
    n_class = len(class_infos)
    class_ratio = n_class / n_subdirs if n_subdirs else 0.0

    lines = [f'{root.name}/']
    for info in class_infos[:12]:
        lines.append(f'  ├─ {info.name}/ ({info.image_count} 张)')
    if len(class_infos) > 12:
        lines.append(f'  └─ ... 共 {len(class_infos)} 个类别文件夹')
    tree_summary = '\n'.join(lines)

    if n_class < 2:
        return FolderLabelDetection(
            False, 'low',
            class_infos=class_infos,
            reason='具有图片的子文件夹少于 2 个',
            tree_summary=tree_summary,
            root_image_count=len(root_images),
        )

    if blocklist_hits and n_class < n_subdirs:
        return FolderLabelDetection(
            False, 'low',
            class_infos=class_infos,
            reason=f'存在非类别目录: {", ".join(blocklist_hits[:3])}',
            tree_summary=tree_summary,
            root_image_count=len(root_images),
        )

    deep_dirs = sum(1 for info in class_infos if info.median_depth > 2)
    deep_ratio = deep_dirs / n_class

    score = 0
    if len(root_images) == 0:
        score += 2
    elif len(root_images) <= max(2, n_class // 10):
        score += 1

    if class_ratio >= 0.9:
        score += 2
    elif class_ratio >= 0.75:
        score += 1

    if deep_ratio <= 0.2:
        score += 1

    if blocklist_hits:
        score -= 2

    names = [info.name for info in class_infos]

    if score >= 4:
        conf = 'high'
        reason = '子文件夹名与图片分布符合「每类一个文件夹」结构'
    elif score >= 2:
        conf = 'medium'
        reason = '结构可能为类别文件夹，建议确认后导入'
    else:
        return FolderLabelDetection(
            False, 'low',
            class_names=names,
            class_infos=class_infos,
            reason='目录结构不符合类别文件夹特征',
            tree_summary=tree_summary,
            root_image_count=len(root_images),
        )

    return FolderLabelDetection(
        True, conf,
        class_names=names,
        class_infos=class_infos,
        reason=reason,
        tree_summary=tree_summary,
        root_image_count=len(root_images),
    )


def save_labels_txt(label_manager, path: Path) -> int:
    """Write labels in ``class_id: name`` format."""
    labels = label_manager.all_labels()
    path = Path(path)
    with open(path, 'w', encoding='utf-8') as f:
        for label in labels:
            f.write(f'{label.class_id}: {label.name}\n')
    return len(labels)

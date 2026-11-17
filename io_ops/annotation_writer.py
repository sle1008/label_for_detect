"""Durable YOLO annotation file writes."""

import os
import tempfile
from pathlib import Path
from typing import Iterable

from core.annotation import BBox
from io_ops.label_set_detector import is_label_set_file

# ==================== 标注写入参数设置区 ====================
ANNOTATION_TEXT_ENCODING = 'utf-8'


def write_yolo_annotations_atomic(
    path: Path,
    annotations: Iterable[BBox],
    image_width: int,
    image_height: int,
) -> None:
    """Write a complete YOLO file and atomically replace the prior version."""
    path = Path(path)
    if not path.parent.is_dir():
        raise FileNotFoundError(f'Annotation directory does not exist: {path.parent}')
    # 拒绝复用类别定义路径，避免自动保存将同名标签集替换为坐标记录。
    if is_label_set_file(path):
        raise FileExistsError(f'目标文件是标签集，不能覆盖为图片标注: {path}')
    content = ''.join(
        annotation.to_yolo(image_width, image_height) + '\n'
        for annotation in annotations
    )

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding=ANNOTATION_TEXT_ENCODING,
            newline='\n',
            dir=path.parent,
            prefix=f'.{path.name}.',
            suffix='.tmp',
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

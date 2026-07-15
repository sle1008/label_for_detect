"""Durable YOLO annotation file writes."""

import os
import tempfile
from pathlib import Path
from typing import Iterable

from core.annotation import BBox


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
    content = ''.join(
        annotation.to_yolo(image_width, image_height) + '\n'
        for annotation in annotations
    )

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
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

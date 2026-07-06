"""Label file parser for TXT and YAML formats."""

from pathlib import Path
from typing import List

from core.annotation import BBox
from core.label_manager import LabelDef
from io_ops.annotation_status import resolve_annotation_txt_path


def parse_txt_labels(path: str) -> List[LabelDef]:
    """Parse TXT label definition file.
    
    Format: "class_id: name | threshold" or "class_id: name"
    """
    labels = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            try:
                parts = line.split('|')
                id_name = parts[0].strip()
                threshold = float(parts[1].strip()) if len(parts) > 1 else 0.5
                
                id_parts = id_name.split(':', 1)
                class_id = int(id_parts[0].strip())
                name = id_parts[1].strip() if len(id_parts) > 1 else f'class_{class_id}'
                
                labels.append(LabelDef(
                    class_id=class_id, name=name, threshold=threshold
                ))
            except (ValueError, IndexError):
                continue
    
    return labels


def parse_yaml_labels(path: str) -> List[LabelDef]:
    """Parse YOLO dataset YAML file."""
    import yaml
    
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    
    if not data or 'names' not in data:
        return []
    
    labels = []
    names = data['names']
    
    if isinstance(names, dict):
        for class_id, name in names.items():
            labels.append(LabelDef(class_id=int(class_id), name=str(name)))
    elif isinstance(names, list):
        for class_id, name in enumerate(names):
            labels.append(LabelDef(class_id=class_id, name=str(name)))
    
    return labels


def load_annotation_file(image_path: Path, label_manager,
                         img_width: int = None, img_height: int = None) -> List[BBox]:
    """Load YOLO format annotation file for an image."""
    annotations = []
    txt_path = resolve_annotation_txt_path(image_path)
    if txt_path is None:
        return annotations
    
    if img_width is None or img_height is None:
        from PIL import Image
        try:
            with Image.open(image_path) as img:
                img_w, img_h = img.size
        except Exception:
            return annotations
    else:
        img_w, img_h = img_width, img_height
    
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue
                
                class_id = int(parts[0])
                cx, cy, w, h = map(float, parts[1:5])
                confidence = float(parts[5]) if len(parts) > 5 else 1.0
                
                class_name = label_manager.get_name(class_id)
                
                bbox = BBox.from_yolo(
                    class_id, class_name, cx, cy, w, h,
                    img_w, img_h, confidence
                )
                annotations.append(bbox)
    except Exception as e:
        print(f"Error loading annotations from {txt_path}: {e}")
    
    return annotations

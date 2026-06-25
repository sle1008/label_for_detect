"""YOLO format exporter."""

import shutil
from pathlib import Path
from typing import List

from core.image_item import ImageItem
from core.label_manager import LabelManager


def export_yolo(image_items: List[ImageItem], label_manager: LabelManager,
                output_dir: str, copy_images: bool = True) -> bool:
    """Export annotations in YOLO format.
    
    Creates:
        output_dir/
        ├── images/  (if copy_images)
        │   └── *.jpg
        ├── labels/
        │   └── *.txt
        └── classes.txt
    
    Returns True if successful.
    """
    try:
        output_path = Path(output_dir)
        labels_dir = output_path / 'labels'
        labels_dir.mkdir(parents=True, exist_ok=True)
        
        if copy_images:
            images_dir = output_path / 'images'
            images_dir.mkdir(parents=True, exist_ok=True)
        
        # Export annotations
        for item in image_items:
            if not item.is_loaded or item.width == 0 or item.height == 0:
                continue
            
            # Write label file
            txt_path = labels_dir / f'{item.stem}.txt'
            with open(txt_path, 'w', encoding='utf-8') as f:
                for ann in item.annotations:
                    line = ann.to_yolo(item.width, item.height)
                    f.write(line + '\n')
            
            # Copy image
            if copy_images:
                img_dest = images_dir / item.name
                if not img_dest.exists():
                    shutil.copy2(item.path, img_dest)
        
        # Write classes.txt
        classes_path = output_path / 'classes.txt'
        with open(classes_path, 'w', encoding='utf-8') as f:
            for label in label_manager.all_labels():
                f.write(f'{label.name}\n')
        
        return True
    
    except Exception as e:
        print(f"YOLO export error: {e}")
        return False

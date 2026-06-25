"""COCO JSON format exporter."""

import json
from pathlib import Path
from typing import List

from core.image_item import ImageItem
from core.label_manager import LabelManager


def export_coco(image_items: List[ImageItem], label_manager: LabelManager,
                output_path: str) -> bool:
    """Export annotations in COCO JSON format.
    
    Creates a single JSON file with images, annotations, categories.
    Returns True if successful.
    """
    try:
        coco_data = {
            'images': [],
            'annotations': [],
            'categories': [],
            'info': {
                'description': 'Exported from Annotation Tool',
                'version': '1.0'
            }
        }
        
        # Categories
        for label in label_manager.all_labels():
            coco_data['categories'].append({
                'id': label.class_id,
                'name': label.name,
                'supercategory': ''
            })
        
        # Images and annotations
        ann_id = 1
        for img_id, item in enumerate(image_items, start=1):
            if not item.is_loaded or item.width == 0 or item.height == 0:
                continue
            
            coco_data['images'].append({
                'id': img_id,
                'file_name': item.name,
                'width': item.width,
                'height': item.height
            })
            
            for ann in item.annotations:
                coco_data['annotations'].append(
                    ann.to_coco(img_id, ann_id)
                )
                ann_id += 1
        
        # Write JSON
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(coco_data, f, ensure_ascii=False, indent=2)
        
        return True
    
    except Exception as e:
        print(f"COCO export error: {e}")
        return False

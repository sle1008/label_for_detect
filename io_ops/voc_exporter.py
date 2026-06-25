"""Pascal VOC XML format exporter."""

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List

from core.image_item import ImageItem
from core.label_manager import LabelManager


def _indent_xml(elem, level=0):
    """Add indentation to XML for pretty printing."""
    indent = '\n' + '  ' * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + '  '
        if not elem.tail or not elem.tail.strip():
            elem.tail = indent
        for child in elem:
            _indent_xml(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = indent
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = indent


def export_voc(image_items: List[ImageItem], label_manager: LabelManager,
               output_dir: str, copy_images: bool = True) -> bool:
    """Export annotations in Pascal VOC XML format.
    
    Creates one XML file per image.
    Returns True if successful.
    """
    try:
        output_path = Path(output_dir)
        annotations_dir = output_path / 'Annotations'
        annotations_dir.mkdir(parents=True, exist_ok=True)
        
        if copy_images:
            images_dir = output_path / 'JPEGImages'
            images_dir.mkdir(parents=True, exist_ok=True)
        
        for item in image_items:
            if not item.is_loaded or item.width == 0 or item.height == 0:
                continue
            
            # Build XML
            annotation = ET.Element('annotation')
            
            ET.SubElement(annotation, 'folder').text = output_path.name
            ET.SubElement(annotation, 'filename').text = item.name
            ET.SubElement(annotation, 'path').text = str(item.path)
            
            source = ET.SubElement(annotation, 'source')
            ET.SubElement(source, 'database').text = 'Annotation Tool'
            
            size = ET.SubElement(annotation, 'size')
            ET.SubElement(size, 'width').text = str(item.width)
            ET.SubElement(size, 'height').text = str(item.height)
            ET.SubElement(size, 'depth').text = '3'
            
            ET.SubElement(annotation, 'segmented').text = '0'
            
            # Objects
            for ann in item.annotations:
                obj_elem = ann.to_voc_element(
                    class_name=label_manager.get_name(ann.class_id)
                )
                annotation.append(obj_elem)
            
            # Write XML
            _indent_xml(annotation)
            tree = ET.ElementTree(annotation)
            xml_path = annotations_dir / f'{item.stem}.xml'
            tree.write(xml_path, encoding='utf-8', xml_declaration=True)
            
            # Copy image
            if copy_images:
                img_dest = images_dir / item.name
                if not img_dest.exists():
                    shutil.copy2(item.path, img_dest)
        
        return True
    
    except Exception as e:
        print(f"VOC export error: {e}")
        return False

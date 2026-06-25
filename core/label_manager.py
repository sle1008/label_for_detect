"""Label manager for managing class definitions."""

from dataclasses import dataclass, field
from typing import List, Dict, Optional

from utils.colors import get_color_for_class


@dataclass
class LabelDef:
    """Label class definition."""
    class_id: int
    name: str
    color: str = ''
    threshold: float = 0.5
    
    def __post_init__(self):
        if not self.color:
            self.color = get_color_for_class(self.class_id)


class LabelManager:
    """Manages label class definitions."""
    
    def __init__(self):
        self._labels: Dict[int, LabelDef] = {}
        self._next_id: int = 0
        self._current_class_id: int = 0
        self._sort_by_name: bool = True
    
    @property
    def current_class_id(self) -> int:
        return self._current_class_id
    
    @current_class_id.setter
    def current_class_id(self, value: int):
        if value in self._labels:
            self._current_class_id = value
    
    @property
    def current_label(self) -> Optional[LabelDef]:
        return self._labels.get(self._current_class_id)
    
    def load_from_txt(self, path: str) -> int:
        """Load labels from TXT file.
        
        Format: "class_id: name | threshold" or "class_id: name"
        Returns number of labels loaded.
        """
        count = 0
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                try:
                    # Parse "class_id: name | threshold"
                    parts = line.split('|')
                    id_name = parts[0].strip()
                    threshold = float(parts[1].strip()) if len(parts) > 1 else 0.5
                    
                    # Parse "class_id: name"
                    id_parts = id_name.split(':', 1)
                    class_id = int(id_parts[0].strip())
                    name = id_parts[1].strip() if len(id_parts) > 1 else f'class_{class_id}'
                    
                    self.add_label(name, class_id=class_id, threshold=threshold)
                    count += 1
                except (ValueError, IndexError):
                    continue
        
        return count
    
    def load_from_yaml(self, path: str) -> int:
        """Load labels from YOLO dataset YAML file.
        
        Supports both dict and list formats for 'names'.
        Returns number of labels loaded.
        """
        import yaml
        
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        if not data or 'names' not in data:
            return 0
        
        names = data['names']
        count = 0
        
        if isinstance(names, dict):
            # Dict format: {0: 'bear', 1: 'cat'}
            for class_id, name in names.items():
                self.add_label(str(name), class_id=int(class_id))
                count += 1
        elif isinstance(names, list):
            # List format: ['bear', 'cat']
            for class_id, name in enumerate(names):
                self.add_label(str(name), class_id=class_id)
                count += 1
        
        return count
    
    def add_label(self, name: str, class_id: int = None,
                  color: str = None, threshold: float = 0.5) -> int:
        """Add a new label class.
        
        Returns the class_id of the added label.
        """
        if class_id is None:
            class_id = self._next_id
        
        if class_id in self._labels:
            # Update existing
            self._labels[class_id].name = name
            if color:
                self._labels[class_id].color = color
            self._labels[class_id].threshold = threshold
        else:
            if not color:
                color = get_color_for_class(class_id)
            self._labels[class_id] = LabelDef(
                class_id=class_id, name=name, color=color, threshold=threshold
            )
        
        # Update next_id
        self._next_id = max(self._next_id, class_id + 1)
        
        # Set current if first label
        if len(self._labels) == 1:
            self._current_class_id = class_id
        
        return class_id
    
    def remove_label(self, class_id: int) -> bool:
        """Remove a label class."""
        if class_id in self._labels:
            del self._labels[class_id]
            if self._current_class_id == class_id:
                if self._labels:
                    self._current_class_id = min(self._labels.keys())
                else:
                    self._current_class_id = 0
            return True
        return False
    
    def get_color(self, class_id: int) -> str:
        """Get color for a class."""
        if class_id in self._labels:
            return self._labels[class_id].color
        return get_color_for_class(class_id)
    
    def get_name(self, class_id: int) -> str:
        """Get name for a class."""
        if class_id in self._labels:
            return self._labels[class_id].name
        return f'class_{class_id}'
    
    def get_threshold(self, class_id: int) -> float:
        """Get detection threshold for a class."""
        if class_id in self._labels:
            return self._labels[class_id].threshold
        return 0.5
    
    def set_threshold(self, class_id: int, value: float):
        """Set detection threshold for a class."""
        if class_id in self._labels:
            self._labels[class_id].threshold = max(0.0, min(1.0, value))
    
    def all_labels(self) -> List[LabelDef]:
        """Get all labels sorted by class_id (for export/IO)."""
        return sorted(self._labels.values(), key=lambda x: x.class_id)
    
    def labels_for_display(self) -> List[LabelDef]:
        """Get labels sorted for UI (by name or class_id)."""
        labels = list(self._labels.values())
        if self._sort_by_name:
            return sorted(labels, key=lambda x: x.name.lower())
        return sorted(labels, key=lambda x: x.class_id)
    
    @property
    def sort_by_name(self) -> bool:
        return self._sort_by_name
    
    @sort_by_name.setter
    def sort_by_name(self, value: bool):
        self._sort_by_name = bool(value)
    
    def toggle_display_sort(self) -> bool:
        """Toggle name/class_id sort. Returns new sort_by_name value."""
        self._sort_by_name = not self._sort_by_name
        return self._sort_by_name
    
    def count(self) -> int:
        return len(self._labels)
    
    def has_class(self, class_id: int) -> bool:
        return class_id in self._labels
    
    def to_dict_list(self) -> List[dict]:
        """Serialize to list of dicts for config saving."""
        return [
            {
                'class_id': label.class_id,
                'name': label.name,
                'color': label.color,
                'threshold': label.threshold
            }
            for label in self.all_labels()
        ]
    
    def from_dict_list(self, data: List[dict]):
        """Load from dict list (for config loading).

        Colors are always derived from the class id (there is no per-label
        color customization), so any stored color is ignored. This ensures
        palette changes take effect for previously-saved labels too.
        """
        for item in data:
            self.add_label(
                name=item['name'],
                class_id=item['class_id'],
                threshold=item.get('threshold', 0.5)
            )
    
    def load_from_folder_names(self, names: List[str], clear: bool = True) -> int:
        """Create labels from subfolder names (sorted alphabetically by name)."""
        if clear:
            self.clear()
        for i, name in enumerate(sorted(names, key=lambda x: x.lower())):
            self.add_label(name.strip(), class_id=i)
        return len(names)
    
    def find_class_id_by_name(self, name: str) -> Optional[int]:
        """Case-insensitive label lookup by name."""
        key = name.strip().lower()
        for label in self._labels.values():
            if label.name.lower() == key:
                return label.class_id
        return None
    
    def clear(self):
        """Clear all labels."""
        self._labels.clear()
        self._next_id = 0
        self._current_class_id = 0

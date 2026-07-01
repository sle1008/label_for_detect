"""Configuration persistence."""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List

from utils.paths import get_app_root


CONFIG_FILENAME = 'config.json'


@dataclass
class AppConfig:
    """Application configuration."""
    last_directory: str = ''
    last_image_path: str = ''
    last_image_index: int = 0
    last_label_file: str = ''
    last_weights_file: str = ''
    label_sort_by_name: bool = True
    confidence_threshold: float = 0.25
    window_geometry: str = '1400x900'
    recent_dirs: List[str] = field(default_factory=list)
    label_definitions: List[dict] = field(default_factory=list)
    image_filter: str = 'all'
    label_mode: str = 'full'
    box_list_column_widths: Dict[str, int] = field(default_factory=lambda: {
        'id': 30, 'class': 80, 'conf': 50, 'coords': 120,
    })
    left_panel_width: int = 0  # 0 = use LEFT_PANEL_WIDTH default at runtime
    right_panel_width: int = 0  # 0 = use RIGHT_PANEL_WIDTH default at runtime
    right_pane_sash_positions: List[int] = field(default_factory=list)
    
    def add_recent_dir(self, dir_path: str):
        """Add a directory to recent list (max 10)."""
        if dir_path in self.recent_dirs:
            self.recent_dirs.remove(dir_path)
        self.recent_dirs.insert(0, dir_path)
        self.recent_dirs = self.recent_dirs[:10]


class ConfigManager:
    """Manages configuration file read/write."""
    
    def __init__(self, config_dir: str = None):
        if config_dir:
            self._path = Path(config_dir) / CONFIG_FILENAME
        else:
            self._path = get_app_root() / CONFIG_FILENAME
    
    def load(self) -> AppConfig:
        """Load config from file."""
        if not self._path.exists():
            return AppConfig()
        
        try:
            with open(self._path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return AppConfig(**{k: v for k, v in data.items()
                               if k in AppConfig.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError, ValueError):
            return AppConfig()
    
    def save(self, config: AppConfig):
        """Save config to file."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, 'w', encoding='utf-8') as f:
                json.dump(asdict(config), f, ensure_ascii=False, indent=2)
        except (IOError, OSError) as e:
            print(f"Failed to save config: {e}")
    
    @property
    def path(self) -> Path:
        return self._path

"""Application paths (development vs PyInstaller bundle)."""

import sys
from pathlib import Path


def get_app_root() -> Path:
    """Directory for config/data — exe folder when frozen, project root otherwise."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_icon_path() -> Path:
    return get_app_root() / 'app.ico'


def format_image_display_path(path: Path, root_dir: Path = None) -> str:
    """Show the last two directory levels plus the filename."""
    parts = path.resolve().parts
    tail = parts[-3:] if len(parts) >= 3 else parts
    return '/'.join(tail).replace('\\', '/')

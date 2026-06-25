"""Annotation Tool - Main Entry Point."""

import sys
import os
import ctypes
import argparse
from pathlib import Path

# Suppress libpng iCCP warnings from PNGs with bad embedded profiles (harmless).
class _StderrFilter:
    __slots__ = ('_stream',)

    def __init__(self, stream):
        self._stream = stream

    def write(self, data):
        if 'iCCP: known incorrect sRGB profile' not in data:
            self._stream.write(data)

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


sys.stderr = _StderrFilter(sys.stderr)

# Enable high-DPI awareness on Windows (must be before Tk init)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Add project root to path
from utils.paths import get_app_root

PROJECT_ROOT = get_app_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.app import AnnotationApp


def main():
    parser = argparse.ArgumentParser(description='目标检测标注工具')
    parser.add_argument('--dir', type=str, help='初始图片目录')
    parser.add_argument('--labels', type=str, help='标签文件路径')
    parser.add_argument('--weights', type=str, help='预标注模型 (.pt/.onnx/.engine/.trt)')
    args = parser.parse_args()
    
    app = AnnotationApp()
    
    # Apply command-line overrides
    if args.dir:
        app._load_directory(args.dir)
    if args.labels:
        ext = Path(args.labels).suffix.lower()
        if ext == '.txt':
            app._label_manager.load_from_txt(args.labels)
        elif ext in ('.yaml', '.yml'):
            app._label_manager.load_from_yaml(args.labels)
        app._label_panel.refresh()
    if args.weights:
        app._pre_annotator.load_weights(args.weights)
    
    app.mainloop()


if __name__ == '__main__':
    main()

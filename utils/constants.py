"""Global constants for the annotation tool."""

# Supported image extensions
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp", ".gif"}

# Default window size
DEFAULT_WINDOW_WIDTH = 1400
DEFAULT_WINDOW_HEIGHT = 900

# UI theme (unified light gray)
UI_BG_COLOR = "#f0f0f0"
UI_PANEL_BG = "#e8e8e8"
UI_SURFACE_BG = "#f5f5f5"
UI_TEXT_COLOR = "#333333"
UI_TEXT_MUTED = "#666666"
UI_ACCENT = "#0078d7"
UI_BORDER = "#d0d0d0"
UI_TOOLBAR_BG = "#ececec"

# Panel default widths
LEFT_PANEL_ORIGINAL = 180
RIGHT_PANEL_ORIGINAL = 330
LEFT_PANEL_WIDTH = LEFT_PANEL_ORIGINAL + 16  # reserve space for list scrollbar
RIGHT_PANEL_WIDTH = int(RIGHT_PANEL_ORIGINAL * 1.25 * 1.2)  # +25% then +20% ≈ 495

# BBox rendering (screen pixels, not scaled with zoom)
BBOX_LINE_WIDTH = 3
BBOX_LINE_WIDTH_SELECTED = 4
BBOX_LABEL_FONT = ("Microsoft YaHei UI", 10)

# Canvas settings
CANVAS_BG_COLOR = UI_PANEL_BG
CANVAS_CROSSHAIR_COLOR = "#444444"
CANVAS_CROSSHAIR_WIDTH = 2
CANVAS_CROSSHAIR_DASH = (4, 4)
CANVAS_PREVIEW_BOX_WIDTH = 3
CANVAS_PREVIEW_BOX_DASH = (6, 3)
CANVAS_SELECT_BOX_COLOR = "#0066aa"
CANVAS_SELECT_BOX_WIDTH = 2
CANVAS_SELECT_BOX_DASH = (6, 3)
MIN_ZOOM = 0.1
MAX_ZOOM = 10.0
ZOOM_FACTOR = 1.1

# Annotation settings
MIN_BOX_SIZE = 3  # Minimum box size in pixels (smaller = ignore)
HANDLE_SIZE = 6  # Resize handle visual half-extent in screen pixels
HANDLE_HIT_EXTRA = 8  # Extra hit padding beyond HANDLE_SIZE (screen px)

# LRU cache settings (memory budget up to 8GB)
MAX_CACHE_BYTES = 8 * 1024 * 1024 * 1024
PRELOAD_FORWARD = 10
PRELOAD_BACKWARD = 7
# Legacy alias
PRELOAD_NEIGHBORS = PRELOAD_FORWARD
MAX_CACHE_SIZE = 200

# Canvas zoom debounce (ms) — coalesce wheel events
ZOOM_REDRAW_DELAY_MS = 16

# Undo/redo settings
MAX_UNDO_STACK = 100

# Auto-save interval (seconds)
AUTO_SAVE_INTERVAL = 30

# Debounce interval for image switching (ms)
IMAGE_SWITCH_DEBOUNCE = 200

# Supported label file extensions
LABEL_FILE_EXTENSIONS = {".txt", ".yaml", ".yml"}

# Export format names
EXPORT_YOLO = "YOLO"
EXPORT_COCO = "COCO"
EXPORT_VOC = "Pascal VOC"

# Default confidence threshold
DEFAULT_CONFIDENCE_THRESHOLD = 0.25

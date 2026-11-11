"""跨界面共享的画布状态与标签显示约定。"""

from enum import Enum, auto

# ==================== 画布状态参数设置区 ====================
LABEL_MODE_COMPACT = 'compact'
LABEL_MODE_FULL = 'full'
LABEL_MODE_HIDDEN = 'hidden'
LABEL_MODE_CYCLE = (LABEL_MODE_FULL, LABEL_MODE_COMPACT, LABEL_MODE_HIDDEN)
LABEL_MODE_NAMES = {LABEL_MODE_COMPACT: '精简', LABEL_MODE_FULL: '全部', LABEL_MODE_HIDDEN: '隐藏'}


class CanvasMode(Enum):
    IDLE = auto()
    DRAWING = auto()
    MOVING = auto()
    RESIZING = auto()
    SELECTING = auto()
    PANNING = auto()

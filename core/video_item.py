"""视频列表条目与会话内的逐帧标注结果。"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from core.annotation import BBox


# ==================== 视频条目参数设置区 ====================
VIDEO_ENTRY_PREFIX = '视频: '


@dataclass
class VideoItem:
    """视频条目与图片索引相互独立，切换图片时仍保留已推理结果。"""

    path: Path
    annotations: Optional[List[List[BBox]]] = None
    fps: float = 0.0
    frame_count: int = 0

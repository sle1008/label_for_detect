"""识别类别定义 TXT，避免将逐图片的坐标标注作为标签集加载。"""

import math
import re
from pathlib import Path
from typing import Iterator, Optional

# ==================== 标签集识别参数设置区 ====================
LABEL_NAME_KEYWORD = 'class'
PREFERRED_LABEL_STEMS = ('classes', 'class')
CONTENT_MIN_LABELS = 2
DEFAULT_LABEL_THRESHOLD = 0.5
INDEXED_LABEL_PATTERN = re.compile(r'^(\d+)\s*:\s*(.+)$')
NUMERIC_SEPARATOR_PATTERN = re.compile(r'[\s,;]+')


def _is_numeric_record(text: str) -> bool:
    """纯数值记录不作为类别名，包括检测框、分割点及坐标列表。"""
    tokens = NUMERIC_SEPARATOR_PATTERN.split(text.strip())
    try:
        for token in tokens:
            float(token)
        return bool(tokens)
    except ValueError:
        return False


def read_label_set_txt(path: Path, require_indexed: bool = False,
                       min_labels: int = 1) -> Optional[list]:
    """严格校验完整文件，支持编号类别及常见的一行一个类别名格式。"""
    definitions = []
    seen_ids = set()
    indexed_format = None
    try:
        with Path(path).open('r', encoding='utf-8-sig') as stream:
            for raw_line in stream:
                line = raw_line.strip()
                if not line or line.startswith('#'):
                    continue
                match = INDEXED_LABEL_PATTERN.fullmatch(line)
                indexed = match is not None
                if require_indexed and not indexed:
                    return None
                if indexed_format is not None and indexed != indexed_format:
                    return None
                indexed_format = indexed
                class_id = int(match.group(1)) if indexed else len(definitions)
                text = match.group(2) if indexed else line
                parts = text.split('|')
                name = parts[0].strip()
                if not name or _is_numeric_record(name) or class_id in seen_ids:
                    return None
                if len(parts) > 2:
                    return None
                threshold = float(parts[1]) if len(parts) == 2 else DEFAULT_LABEL_THRESHOLD
                if not math.isfinite(threshold) or not 0 <= threshold <= 1:
                    return None
                seen_ids.add(class_id)
                definitions.append({'class_id': class_id, 'name': name, 'threshold': threshold})
    except (OSError, UnicodeError, ValueError):
        return None
    return definitions if len(definitions) >= min_labels else None


def iter_label_set_candidates(directory: Path) -> Iterator[tuple]:
    """只扫描当前目录，按标准文件名、包含 class 的名称、内容匹配依次查找。"""
    try:
        paths = [path for path in Path(directory).iterdir()
                 if path.suffix.lower() == '.txt' and path.is_file()]
    except OSError:
        return

    def priority(path):
        stem = path.stem.casefold()
        rank = (PREFERRED_LABEL_STEMS.index(stem)
                if stem in PREFERRED_LABEL_STEMS else len(PREFERRED_LABEL_STEMS))
        return rank, path.name.casefold(), path.name

    paths.sort(key=priority)
    for named_pass in (True, False):
        for path in paths:
            named = LABEL_NAME_KEYWORD in path.stem.casefold()
            if named != named_pass:
                continue
            definitions = read_label_set_txt(
                path, require_indexed=not named,
                min_labels=1 if named else CONTENT_MIN_LABELS,
            )
            if definitions:
                yield path, definitions

"""标签类别缓存的批量读取，按目录复用文件索引并保留原有路径优先级。"""

import os
from io_ops.annotation_status import candidate_annotation_txt_paths

# ==================== 标签缓存参数设置区 ====================
LABEL_CACHE_BATCH_SIZE = 128


class AnnotationDirectoryIndex:
    """单次扫描内复用目录枚举结果；刷新时重新创建，避免持久化过期路径。"""
    def __init__(self):
        self._directories = {}
        self._candidate_directories = {}

    def _files(self, directory):
        if directory not in self._directories:
            try:
                with os.scandir(directory) as entries:
                    self._directories[directory] = {
                        os.path.normcase(entry.name) for entry in entries
                        if entry.name.lower().endswith('.txt') and entry.is_file()
                    }
            except FileNotFoundError:
                self._directories[directory] = set()
            except OSError:
                # 无法枚举时沿用逐文件检查，兼容特殊权限和文件系统。
                self._directories[directory] = None
        return self._directories[directory]

    def resolve(self, image_path):
        parent = image_path.parent
        if parent not in self._candidate_directories:
            # 同一图片目录共享标签位置，省去逐图遍历祖先目录和路径去重。
            self._candidate_directories[parent] = tuple(dict.fromkeys(
                path.parent for path in candidate_annotation_txt_paths(image_path, inspect_files=False)
            ))
        name = image_path.stem + '.txt'
        for directory in self._candidate_directories[parent]:
            files = self._files(directory)
            if ((files is None and (directory / name).is_file())
                    or (files is not None and os.path.normcase(name) in files)):
                return directory / name
        return None


def iter_label_cache_batches(items, is_cancelled=lambda: False,
                            batch_size=LABEL_CACHE_BATCH_SIZE):
    """只读取磁盘快照，分批返回结果；调用方负责在主线程合并会话中的编辑。"""
    index = AnnotationDirectoryIndex()
    batch = []
    for item in items:
        if is_cancelled():
            return
        class_ids = set()
        exists = False
        try:
            path = index.resolve(item.path)
            if path is not None:
                with path.open('r', encoding='utf-8') as stream:
                    for line in stream:
                        if is_cancelled():
                            return
                        parts = line.split(maxsplit=1)
                        if parts:
                            class_ids.add(int(float(parts[0])))
                exists = True
        except (OSError, UnicodeError, ValueError, OverflowError):
            class_ids.clear()
        batch.append((item, str(item.path.resolve()), class_ids, exists))
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch and not is_cancelled():
        yield batch

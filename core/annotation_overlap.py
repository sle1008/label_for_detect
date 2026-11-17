"""按几何重合程度识别需要人工检查的重复框，不修改原始标注。"""

import math

# ==================== 重复框识别参数设置区 ====================
# 高交并比用于区分重复定位与正常遮挡；同类及不同类别使用相同规则。
DUPLICATE_IOU_THRESHOLD = 0.90


def find_duplicate_groups(boxes, threshold=DUPLICATE_IOU_THRESHOLD):
    """返回重复组的原始索引；每组多余框数为成员数减一。"""
    entries = []
    for index, box in enumerate(boxes):
        x1, y1, x2, y2 = box.x1, box.y1, box.x2, box.y2
        if all(math.isfinite(value) for value in (x1, y1, x2, y2)) and x2 > x1 and y2 > y1:
            entries.append((x1, y1, x2, y2, (x2 - x1) * (y2 - y1), index))
    entries.sort()
    parents = {entry[-1]: entry[-1] for entry in entries}

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for position, (x1, y1, x2, y2, area, index) in enumerate(entries):
        # 沿横轴扫描，只比较可能相交且面积接近的框，减少密集重绘的计算。
        for following in range(position + 1, len(entries)):
            other = entries[following]
            ox1, oy1, ox2, oy2, other_area, other_index = other
            if ox1 >= x2:
                break
            if oy1 >= y2 or oy2 <= y1 or min(area, other_area) < threshold * max(area, other_area):
                continue
            intersection = (min(x2, ox2) - ox1) * (min(y2, oy2) - max(y1, oy1))
            if intersection >= threshold * (area + other_area - intersection):
                parents[root(other_index)] = root(index)
    groups = {}
    for index in parents:
        groups.setdefault(root(index), []).append(index)
    return tuple(sorted(tuple(sorted(group)) for group in groups.values() if len(group) > 1))

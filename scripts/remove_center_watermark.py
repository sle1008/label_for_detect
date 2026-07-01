"""Remove Veer/iStock watermarks with OCR + LaMa (standalone, read-only source).

Targets exactly two fixed watermark styles:

  1. Center two-line block: large "VEER" + smaller "by iStock".
     Skipped when the center mask overlaps the likely head zone too much
     (conservative — corner bar removal is always kept when present).

  2. Bottom-left bar: "图片来源：Veer图库 www.veer.com".
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.constants import IMAGE_EXTENSIONS  # noqa: E402

OcrHit = Tuple[Tuple[int, int, int, int], np.ndarray, str, float]

CENTER_MARKERS = ('veer', 'istock')
CENTER_COMPANION = ('by', 'stock')
CENTER_W_RATIO = 0.72
CENTER_H_RATIO = 0.48

CORNER_MARKERS = ('图片来源', 'veer图库', 'veer.com', 'www.veer', 'veer')
CORNER_W_RATIO = 0.62
CORNER_H_RATIO = 0.11

MAX_MASK_AREA_RATIO = 0.14
COMPANION_OCR_CONF = 0.06
CENTER_MASK_DILATE = 2
CORNER_MASK_DILATE = 3

# Likely head region for centered wildlife portraits (heuristic, not animal detection).
HEAD_ZONE_W_RATIO = 0.52
HEAD_ZONE_Y1_RATIO = 0.30
HEAD_ZONE_Y2_RATIO = 0.56
# Skip center inpaint when this much of the mask lies in the head zone.
HEAD_OVERLAP_SKIP_RATIO = 0.40
# Also require a non-trivial center mask before skipping (avoid noise).
HEAD_SKIP_MIN_MASK_RATIO = 0.007

_LOG_RESULT_PREFIXES = ('NONE:', 'OK:', 'SKIP', 'REJECT', 'FAIL', 'DETECT')


def normalize_rel_path(path: str) -> str:
    return path.replace('\\', '/').strip()


def extract_logged_rel_path(line: str) -> Optional[str]:
    """Extract image relative path from a batch log result line."""
    stripped = line.strip()
    for prefix in ('NONE: ', 'OK: '):
        if stripped.startswith(prefix):
            return normalize_rel_path(stripped[len(prefix):].split(' [')[0])
    m = re.match(r'^(?:SKIP|REJECT|FAIL(?: \(write\))?).*?:\s*(.+?)(?:\s*\[|$)', stripped)
    if m:
        return normalize_rel_path(m.group(1))
    if stripped.startswith('DETECT:'):
        return normalize_rel_path(stripped[len('DETECT:'):].split(' [')[0])
    return None


def parse_resume_state(log_path: Path) -> Tuple[set[str], set[str]]:
    """Return (completed_folder_names, processed_image_relpaths) from an existing log."""
    if not log_path.is_file():
        return set(), set()

    text = log_path.read_text(encoding='utf-8')
    folder_order: List[str] = []
    processed: set[str] = set()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('=== Folder:'):
            name = stripped[len('=== Folder:'):].strip()
            if name.endswith('==='):
                name = name[:-3].strip()
            if ' (skipped' in name:
                name = name.split(' (', 1)[0].strip()
            folder_order.append(name)
            continue
        rel = extract_logged_rel_path(stripped)
        if rel:
            processed.add(rel)

    if 'Done in' in text:
        completed = set(folder_order)
    elif len(folder_order) > 1:
        completed = set(folder_order[:-1])
    else:
        completed = set()

    return completed, processed


def iter_images(folder: Path) -> Iterable[Path]:
    for path in sorted(folder.rglob('*')):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            yield path


def sorted_subfolders(root: Path) -> List[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir())


def keyword_match(text: str, keywords: Sequence[str]) -> bool:
    lowered = text.lower().strip()
    compact = re.sub(r'[\s\-_\.:：]+', '', lowered)
    for kw in keywords:
        k = kw.lower()
        k_compact = re.sub(r'[\s\-_\.:：]+', '', k)
        if k in lowered or k_compact in compact:
            return True
    return False


def center_region(w: int, h: int) -> Tuple[int, int, int, int]:
    rw = int(w * CENTER_W_RATIO)
    rh = int(h * CENTER_H_RATIO)
    x1 = (w - rw) // 2
    y1 = (h - rh) // 2
    return x1, y1, x1 + rw, y1 + rh


def corner_region(w: int, h: int) -> Tuple[int, int, int, int]:
    rw = int(w * CORNER_W_RATIO)
    rh = int(h * CORNER_H_RATIO)
    return 0, h - rh, rw, h


def union_box(boxes: Sequence[Tuple[int, int, int, int]]) -> Optional[Tuple[int, int, int, int]]:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def clamp_box(box: Tuple[int, int, int, int], w: int, h: int) -> Tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    return max(0, x1), max(0, y1), min(w - 1, x2), min(h - 1, y2)


def box_center(box: Tuple[int, int, int, int]) -> Tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def box_area(box: Tuple[int, int, int, int]) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def box_iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    return inter / float(box_area(a) + box_area(b) - inter)


def get_ocr_reader():
    try:
        import easyocr  # type: ignore
    except ImportError as exc:
        raise SystemExit('EasyOCR is required:\n  pip install easyocr --user') from exc
    if not hasattr(get_ocr_reader, '_reader'):
        get_ocr_reader._reader = easyocr.Reader(['en', 'ch_sim'], gpu=False, verbose=False)
    return get_ocr_reader._reader


def horiz_overlap(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> int:
    return max(0, min(a[2], b[2]) - max(a[0], b[0]))


def enhance_for_ocr(bgr: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    return cv2.cvtColor(cv2.merge([clahe.apply(l), a, b]), cv2.COLOR_LAB2BGR)


def ocr_in_roi(bgr: np.ndarray, roi: Tuple[int, int, int, int], min_conf: float) -> List[OcrHit]:
    x1, y1, x2, y2 = roi
    crop = bgr[y1:y2, x1:x2]
    if crop.size == 0:
        return []

    reader = get_ocr_reader()
    hits: List[OcrHit] = []
    for bbox, text, conf in reader.readtext(crop):
        if conf < min_conf:
            continue
        xs = [int(p[0]) + x1 for p in bbox]
        ys = [int(p[1]) + y1 for p in bbox]
        box = (min(xs), min(ys), max(xs), max(ys))
        poly = np.array([[int(p[0]) + x1, int(p[1]) + y1] for p in bbox], dtype=np.int32)
        hits.append((box, poly, text, conf))
    return hits


def merge_ocr_hits(*hit_lists: Sequence[OcrHit]) -> List[OcrHit]:
    merged: List[OcrHit] = []
    for hits in hit_lists:
        for hit in hits:
            box, poly, text, conf = hit
            dup = False
            for i, (mbox, _mpoly, mtext, mconf) in enumerate(merged):
                if horiz_overlap(box, mbox) > 0 and abs(box[1] - mbox[1]) < 8:
                    if conf > mconf:
                        merged[i] = hit
                    dup = True
                    break
            if not dup:
                merged.append(hit)
    return merged


def ocr_center_passes(bgr: np.ndarray, roi: Tuple[int, int, int, int], min_conf: float) -> List[OcrHit]:
    x1, y1, x2, y2 = roi
    enhanced = enhance_for_ocr(bgr[y1:y2, x1:x2])
    full = bgr.copy()
    full[y1:y2, x1:x2] = enhanced
    return merge_ocr_hits(
        ocr_in_roi(bgr, roi, min_conf),
        ocr_in_roi(full, roi, min_conf),
    )


def cluster_center_hits(ocr_hits: Sequence[OcrHit], anchors: Sequence[Tuple[int, int, int, int]], ocr_conf: float) -> List[OcrHit]:
    anchor_union = union_box(anchors)
    if anchor_union is None:
        return []

    ax1, ay1, ax2, ay2 = anchor_union
    ah = max(ay2 - ay1, 20)
    aw = max(ax2 - ax1, 30)
    acx = (ax1 + ax2) / 2
    anchor_set = set(anchors)
    cluster: List[OcrHit] = []

    for hit in ocr_hits:
        box, poly, text, conf = hit
        if box in anchor_set:
            cluster.append(hit)
            continue
        cx, cy = box_center(box)
        v_stack = ay1 - ah * 3.5 <= cy <= ay2 + ah * 0.7
        h_align = horiz_overlap(box, anchor_union) >= max(8, int(aw * 0.12)) or abs(cx - acx) <= aw * 1.2
        is_marker = conf >= ocr_conf and keyword_match(text, CENTER_MARKERS)
        is_companion = keyword_match(text, CENTER_COMPANION) and v_stack and h_align
        if is_marker or is_companion or (v_stack and h_align and conf >= COMPANION_OCR_CONF):
            cluster.append(hit)
    return cluster


def dedupe_overlapping_hits(hits: Sequence[OcrHit]) -> List[OcrHit]:
    kept: List[OcrHit] = []
    for hit in hits:
        box = hit[0]
        replaced = False
        for i, kept_hit in enumerate(kept):
            if box_iou(box, kept_hit[0]) > 0.45:
                if box_area(box) < box_area(kept_hit[0]):
                    kept[i] = hit
                replaced = True
                break
        if not replaced:
            kept.append(hit)
    return kept


def filter_oversized_center_hits(hits: Sequence[OcrHit], anchor_union: Tuple[int, int, int, int]) -> List[OcrHit]:
    ax1, ay1, ax2, ay2 = anchor_union
    ah = max(ay2 - ay1, 18)
    aw = max(ax2 - ax1, 30)
    kept: List[OcrHit] = []
    for hit in hits:
        box = hit[0]
        bh = box[3] - box[1]
        bw = box[2] - box[0]
        if bh > ah * 3.2 or bw > aw * 5.0:
            continue
        kept.append(hit)
    return kept


def faint_veer_polys_above(bgr: np.ndarray, anchor_union: Tuple[int, int, int, int]) -> List[np.ndarray]:
    h, w = bgr.shape[:2]
    ax1, ay1, ax2, ay2 = anchor_union
    ah = max(ay2 - ay1, 20)
    aw = max(ax2 - ax1, 40)
    acx = (ax1 + ax2) / 2

    band_w = int(max(aw * 1.4, 60))
    x1 = max(0, int(acx - band_w / 2))
    x2 = min(w, int(acx + band_w / 2))
    y2 = max(0, ay1 - 2)
    y1 = max(0, int(ay1 - ah * 3.2))
    if y2 <= y1 or x2 <= x1:
        return []

    crop = bgr[y1:y2, x1:x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    tophat = cv2.morphologyEx(
        cv2.GaussianBlur(gray, (3, 3), 0),
        cv2.MORPH_TOPHAT,
        cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11)),
    )
    _, binary = cv2.threshold(tophat, 7, 255, cv2.THRESH_BINARY)

    crop_area = crop.shape[0] * crop.shape[1]
    polys: List[np.ndarray] = []
    max_letter_h = int(ah * 2.4)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < crop_area * 0.001 or area > crop_area * 0.12:
            continue
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if bh > max_letter_h or bw > band_w * 0.45:
            continue
        if bw / max(bh, 1) < 0.25 or bw / max(bh, 1) > 6:
            continue
        polys.append((cnt + np.array([[x1, y1]])).astype(np.int32))
    return polys


def polys_to_mask(shape: Tuple[int, ...], polys: Sequence[np.ndarray], dilate: int) -> np.ndarray:
    h, w = shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    for poly in polys:
        if poly.size > 0:
            cv2.fillPoly(mask, [poly], 255)
    if dilate > 0:
        k = dilate * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        mask = cv2.dilate(mask, kernel, iterations=1)
    return mask


def detect_center_mask(bgr: np.ndarray, ocr_conf: float) -> Optional[np.ndarray]:
    h, w = bgr.shape[:2]
    roi = center_region(w, h)
    ocr_hits = ocr_center_passes(bgr, roi, COMPANION_OCR_CONF)

    anchors = [box for box, _poly, text, conf in ocr_hits if conf >= ocr_conf and keyword_match(text, CENTER_MARKERS)]
    if not anchors:
        return None

    anchor_union = union_box(anchors)
    cluster = dedupe_overlapping_hits(filter_oversized_center_hits(
        cluster_center_hits(ocr_hits, anchors, ocr_conf), anchor_union,
    ))

    polys = [poly for _box, poly, _text, _conf in cluster]
    has_veer = any(keyword_match(text, ('veer',)) for _box, _poly, text, conf in cluster if conf >= ocr_conf)
    if not has_veer and anchor_union is not None:
        polys.extend(faint_veer_polys_above(bgr, anchor_union))

    if not polys:
        return None
    return polys_to_mask(bgr.shape, polys, CENTER_MASK_DILATE)


def detect_corner_watermark(bgr: np.ndarray, ocr_conf: float) -> Optional[Tuple[int, int, int, int]]:
    h, w = bgr.shape[:2]
    roi = corner_region(w, h)
    ocr_hits = ocr_in_roi(bgr, roi, min_conf=COMPANION_OCR_CONF)

    anchors = [box for box, _poly, text, conf in ocr_hits if conf >= ocr_conf and keyword_match(text, CORNER_MARKERS)]
    if not anchors:
        return None

    ux1, uy1, ux2, uy2 = union_box(anchors)
    bar_h = max(int(h * CORNER_H_RATIO), uy2 - uy1 + 8)
    return clamp_box((0, max(0, h - bar_h - 4), min(w - 1, max(ux2 + 12, int(w * 0.45))), h), w, h)


def boxes_to_mask(shape: Tuple[int, ...], boxes: Sequence[Tuple[int, int, int, int]], box_pad: int, dilate: int) -> np.ndarray:
    h, w = shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    for x1, y1, x2, y2 in boxes:
        cv2.rectangle(
            mask,
            (max(0, x1 - box_pad), max(0, y1 - box_pad)),
            (min(w - 1, x2 + box_pad), min(h - 1, y2 + box_pad)),
            255, thickness=-1,
        )
    if dilate > 0:
        k = dilate * 2 + 1
        mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)), iterations=1)
    return mask


def build_watermark_mask(bgr: np.ndarray, ocr_conf: float, box_pad: int) -> Tuple[Optional[np.ndarray], List[str]]:
    tags: List[str] = []
    h, w = bgr.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)

    center = detect_center_mask(bgr, ocr_conf)
    if center is not None and np.any(center):
        skip, reason = should_skip_center_inpaint(center)
        if skip:
            tags.append(f'center-skipped({reason})')
        else:
            mask = cv2.bitwise_or(mask, center)
            tags.append('center')

    corner = detect_corner_watermark(bgr, ocr_conf)
    if corner:
        mask = cv2.bitwise_or(mask, boxes_to_mask(bgr.shape, [corner], box_pad, CORNER_MASK_DILATE))
        tags.append('corner')

    if not tags:
        return None, []
    if not np.any(mask):
        return None, tags
    return mask, tags


def mask_area_ratio(mask: np.ndarray) -> float:
    return float(np.count_nonzero(mask)) / float(mask.size)


def head_risk_zone(w: int, h: int) -> Tuple[int, int, int, int]:
    rw = int(w * HEAD_ZONE_W_RATIO)
    rh = int(h * (HEAD_ZONE_Y2_RATIO - HEAD_ZONE_Y1_RATIO))
    x1 = (w - rw) // 2
    y1 = int(h * HEAD_ZONE_Y1_RATIO)
    return x1, y1, x1 + rw, y1 + rh


def mask_head_overlap_ratio(mask: np.ndarray, w: int, h: int) -> float:
    """Fraction of center-mask pixels that fall inside the likely head zone."""
    mask_px = int(np.count_nonzero(mask))
    if mask_px == 0:
        return 0.0
    x1, y1, x2, y2 = head_risk_zone(w, h)
    zone = np.zeros_like(mask)
    zone[y1:y2, x1:x2] = 255
    inter = cv2.bitwise_and(mask, zone)
    return float(np.count_nonzero(inter)) / float(mask_px)


def should_skip_center_inpaint(center_mask: np.ndarray) -> Tuple[bool, str]:
    """Conservative rule: skip risky center repair that may destroy the subject."""
    h, w = center_mask.shape[:2]
    ratio = mask_area_ratio(center_mask)
    overlap = mask_head_overlap_ratio(center_mask, w, h)
    if ratio >= HEAD_SKIP_MIN_MASK_RATIO and overlap >= HEAD_OVERLAP_SKIP_RATIO:
        return True, f'head overlap {overlap:.0%}'
    return False, ''


def load_lama(device: str = 'cpu'):
    try:
        from simple_lama_inpainting import SimpleLama  # type: ignore
    except ImportError as exc:
        raise SystemExit('LaMa inpainting is required:\n  pip install simple-lama-inpainting --user') from exc
    return SimpleLama(device=device)


def inpaint_lama(lama, bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    result = lama(Image.fromarray(rgb), Image.fromarray(mask).convert('L'))
    return cv2.cvtColor(np.array(result), cv2.COLOR_RGB2BGR)


def imread_unicode(path: Path) -> Optional[np.ndarray]:
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite_unicode(path: Path, bgr: np.ndarray) -> bool:
    ext = path.suffix.lower()
    encode_ext = '.jpg' if ext in ('.jpg', '.jpeg') else ext or '.png'
    ok, buf = cv2.imencode(encode_ext, bgr)
    if not ok:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    buf.tofile(str(path))
    return True


def process_one(
    src: Path, dst: Path, *, rel_root: Path, ocr_conf: float, box_pad: int,
    max_mask_ratio: float, lama, dry_run: bool,
) -> Tuple[str, bool]:
    bgr = imread_unicode(src)
    if bgr is None:
        return f'SKIP (unreadable): {src.name}', False

    mask, tags = build_watermark_mask(bgr, ocr_conf, box_pad)
    if not tags:
        return f'NONE: {src.relative_to(rel_root)}', False
    if mask is None or not np.any(mask):
        return f'SKIP (center only, head overlap): {src.relative_to(rel_root)} [{",".join(tags)}]', False

    ratio = mask_area_ratio(mask)
    if ratio > max_mask_ratio:
        return f'REJECT (mask {ratio:.1%} > {max_mask_ratio:.0%}): {src.relative_to(rel_root)} [{",".join(tags)}]', False

    tag_str = '+'.join(tags)
    if dry_run:
        ys, xs = np.where(mask > 0)
        return (
            f'DETECT: {src.relative_to(rel_root)} [{tag_str}] mask={ratio:.1%} '
            f'box={xs.min()},{ys.min()}-{xs.max()},{ys.max()}', True,
        )

    cleaned = inpaint_lama(lama, bgr, mask)
    if not imwrite_unicode(dst, cleaned):
        return f'FAIL (write): {src.relative_to(rel_root)}', False
    return f'OK: {src.relative_to(rel_root)} [{tag_str}] (mask {ratio:.1%})', True


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Remove Veer/iStock center + corner watermarks (OCR + LaMa).')
    p.add_argument('-i', '--input', type=Path, default=Path(r'D:\网络爬取'))
    p.add_argument('-o', '--output', type=Path, default=Path(r'D:\图片去水印'))
    p.add_argument('--all-subfolders', action='store_true')
    p.add_argument('--ocr-conf', type=float, default=0.10)
    p.add_argument('--box-pad', type=int, default=5)
    p.add_argument('--max-mask-ratio', type=float, default=MAX_MASK_AREA_RATIO)
    p.add_argument('--device', default='cpu', choices=('cpu', 'cuda', 'mps'))
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--resume', action='store_true',
                   help='Skip folders/images already recorded in the log; append to log file.')
    p.add_argument('--log', type=Path, default=None)
    return p


def log_line(msg: str, log_fp):
    print(msg, flush=True)
    if log_fp is not None:
        log_fp.write(msg + '\n')
        log_fp.flush()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.input.is_dir():
        print(f'Input folder not found: {args.input}', file=sys.stderr)
        return 1

    if args.all_subfolders:
        folders = sorted_subfolders(args.input) or [args.input]
    else:
        subs = sorted_subfolders(args.input)
        folders = [subs[0]] if subs else [args.input]
        if subs:
            print(f'First subfolder only: {folders[0].name}')

    log_path = args.log or (args.output / 'batch_log.txt')
    if not args.dry_run:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    completed_folders: set[str] = set()
    processed_paths: set[str] = set()
    if args.resume and log_path.is_file():
        completed_folders, processed_paths = parse_resume_state(log_path)
        log_fp = open(log_path, 'a', encoding='utf-8')
        log_line(
            f'\n--- Resume {time.strftime("%Y-%m-%d %H:%M:%S")} — '
            f'skip {len(completed_folders)} folders, {len(processed_paths)} images ---',
            log_fp,
        )
    else:
        if args.resume:
            print('No existing log found; starting fresh.')
        log_fp = open(log_path, 'w', encoding='utf-8')

    lama = None
    if not args.dry_run:
        print('Loading LaMa model (first run may download weights)...')
        lama = load_lama(args.device)

    total = hit = reject = skipped = 0
    t0 = time.time()
    for folder in folders:
        folder_key = normalize_rel_path(str(folder.relative_to(args.input)))
        if folder_key in completed_folders:
            log_line(f'\n=== Folder: {folder_key} === (skipped, already done)', log_fp)
            continue

        log_line(f'\n=== Folder: {folder_key} ===', log_fp)
        for src in iter_images(folder):
            rel_key = normalize_rel_path(str(src.relative_to(args.input)))
            if rel_key in processed_paths:
                skipped += 1
                continue

            total += 1
            msg, processed = process_one(
                src, args.output / src.relative_to(args.input),
                rel_root=args.input, ocr_conf=args.ocr_conf, box_pad=args.box_pad,
                max_mask_ratio=args.max_mask_ratio, lama=lama, dry_run=args.dry_run,
            )
            log_line(msg, log_fp)
            processed_paths.add(rel_key)
            if processed:
                hit += 1
            elif msg.startswith('REJECT'):
                reject += 1

    log_line(
        f'\nDone in {time.time() - t0:.0f}s — processed={hit}, rejected={reject}, '
        f'no_watermark={total - hit - reject}, skipped={skipped}, total={total}', log_fp,
    )
    log_fp.close()
    print(f'Log: {log_path}')
    return 0


if __name__ == '__main__':
    import os
    os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')
    raise SystemExit(main())

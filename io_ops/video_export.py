"""逐帧绘制检测结果并编码视频，完成后原子发布目标文件。"""

import math
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from io_ops.video_player import DEFAULT_VIDEO_FPS
from utils.colors import get_color_for_class, hex_to_rgb


# ==================== 视频导出参数设置区 ====================
VIDEO_EXPORT_SUFFIX = '.mp4'
VIDEO_EXPORT_CRF = 18
VIDEO_EXPORT_PRESET = 'fast'
VIDEO_EXPORT_PROGRESS_INTERVAL = 0.1
VIDEO_EXPORT_WAIT_SECONDS = 60
VIDEO_EXPORT_FONT_CANDIDATES = (
    str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'msyh.ttc'),
    str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'simsun.ttc'),
    'DejaVuSans.ttf',
)


@dataclass(frozen=True)
class VideoExportResult:
    output_path: Path
    frame_count: int
    fps: float


class VideoExportCancelled(Exception):
    """取消任务时不留下不完整视频，也不覆盖已有目标文件。"""


def validate_video_export_path(source, output):
    source, output = Path(source), Path(output)
    same_file = source.resolve() == output.resolve()
    if source.exists() and output.exists():
        same_file = same_file or os.path.samefile(source, output)
    if same_file:
        raise ValueError('导出路径不能与原视频相同，请另选文件名')
    if output.suffix.lower() != VIDEO_EXPORT_SUFFIX:
        raise ValueError('视频导出格式为 MP4')


def _load_label_font(height):
    size = max(14, min(32, int(height / 40)))
    for name in VIDEO_EXPORT_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_video_annotations(frame, annotations, font):
    """在原始分辨率上绘制类别色框和可显示中文的类别、置信度文本。"""
    if not annotations:
        return frame
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(image)
    width, height = image.size
    line_width = max(2, int(height / 360))
    for box in annotations:
        x1, x2 = sorted((max(0, min(width - 1, int(box.x1))),
                         max(0, min(width - 1, int(box.x2)))))
        y1, y2 = sorted((max(0, min(height - 1, int(box.y1))),
                         max(0, min(height - 1, int(box.y2)))))
        color = hex_to_rgb(get_color_for_class(box.class_id))
        draw.rectangle((x1, y1, x2, y2), outline=color, width=line_width)
        text = f'{box.class_name} {box.confidence:.2f}'
        bounds = draw.textbbox((0, 0), text, font=font)
        text_width, text_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        left = max(0, min(x1, width - text_width - 6))
        top = max(0, min(y1, height - text_height - 6))
        draw.rectangle((left, top, left + text_width + 6, top + text_height + 6), fill=color)
        draw.text((left + 3 - bounds[0], top + 3 - bounds[1]), text, font=font, fill='white')
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def export_annotated_video(source, output, annotations, stop_event=None,
                           progress_callback=None):
    """保持原帧率与分辨率导出；原视频音轨存在时一起编码到 MP4。"""
    import imageio_ffmpeg

    source, output = Path(source), Path(output)
    validate_video_export_path(source, output)
    if not annotations:
        raise ValueError('视频尚无完整逐帧推理结果')
    output.parent.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(source))
    process = None
    temporary_path = None
    diagnostics = tempfile.TemporaryFile()
    try:
        if not capture.isOpened():
            raise RuntimeError('无法读取原视频')
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        if not math.isfinite(fps) or fps <= 0:
            fps = DEFAULT_VIDEO_FPS
        processed = 0
        last_progress = 0.0
        while True:
            if stop_event is not None and stop_event.is_set():
                raise VideoExportCancelled()
            ok, frame = capture.read()
            if not ok:
                break
            if processed >= len(annotations):
                raise RuntimeError('视频帧数与推理结果不匹配，请重新推理')
            if process is None:
                height, width = frame.shape[:2]
                font = _load_label_font(height)
                with tempfile.NamedTemporaryFile(
                    prefix=f'.{output.stem}-', suffix='.mp4', dir=output.parent, delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                # 视频图像来自绘制后的 BGR 帧，音频从原文件读取；不使用预览倍速。
                command = [
                    imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-loglevel', 'error',
                    '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{width}x{height}',
                    '-r', f'{fps:.12g}', '-i', 'pipe:0', '-i', str(source),
                    '-map', '0:v:0', '-map', '1:a?', '-c:v', 'libx264',
                    '-preset', VIDEO_EXPORT_PRESET, '-crf', str(VIDEO_EXPORT_CRF),
                    '-pix_fmt', 'yuv420p' if width % 2 == 0 and height % 2 == 0 else 'yuv444p',
                    '-c:a', 'aac', '-movflags', '+faststart', str(temporary_path),
                ]
                process = subprocess.Popen(
                    command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=diagnostics,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                )
            rendered = draw_video_annotations(frame, annotations[processed], font)
            process.stdin.write(rendered.tobytes())
            processed += 1
            now = time.perf_counter()
            if progress_callback and now - last_progress >= VIDEO_EXPORT_PROGRESS_INTERVAL:
                progress_callback(processed, len(annotations))
                last_progress = now
        if processed != len(annotations):
            raise RuntimeError(f'视频解码不完整，仅导出 {processed}/{len(annotations)} 帧')
        process.stdin.close()
        process.wait(timeout=VIDEO_EXPORT_WAIT_SECONDS)
        if process.returncode:
            diagnostics.seek(0)
            raise RuntimeError(diagnostics.read().decode('utf-8', errors='replace')[-2000:])
        if stop_event is not None and stop_event.is_set():
            raise VideoExportCancelled()
        if temporary_path.stat().st_size == 0:
            raise RuntimeError('编码器未生成有效视频')
        os.replace(temporary_path, output)
        return VideoExportResult(output, processed, fps)
    finally:
        capture.release()
        if process is not None:
            if process.stdin and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        diagnostics.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

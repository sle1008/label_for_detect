"""YOLOv8 pre-annotation engine."""

import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Dict, Set

from core.annotation import BBox
from core.image_item import ImageItem
from core.label_manager import LabelManager
from utils.constants import DEFAULT_CONFIDENCE_THRESHOLD

SUPPORTED_WEIGHT_EXTENSIONS: Set[str] = {'.pt', '.onnx', '.engine', '.trt'}


class PreAnnotator:
    """Uses YOLOv8 for pre-annotation."""
    
    def __init__(self):
        self._model = None
        self._model_path: Optional[str] = None
        self._cancel_event = threading.Event()
        self._busy = False
    
    @property
    def is_loaded(self) -> bool:
        return self._model is not None
    
    @property
    def is_busy(self) -> bool:
        return self._busy
    
    @property
    def model_path(self) -> Optional[str]:
        return self._model_path
    
    def load_weights(self, path: str) -> bool:
        """Load YOLO model weights (.pt / .onnx / .engine / .trt)."""
        ext = Path(path).suffix.lower()
        if ext not in SUPPORTED_WEIGHT_EXTENSIONS:
            print(
                f'Unsupported model format: {ext}. '
                f'Supported: {", ".join(sorted(SUPPORTED_WEIGHT_EXTENSIONS))}'
            )
            return False
        
        try:
            from ultralytics import YOLO
            self._model = YOLO(path)
            self._model_path = path
            return True
        except Exception as e:
            print(f"Failed to load model ({path}): {e}")
            if ext in ('.engine', '.trt'):
                print(
                    'TensorRT 模型需要 NVIDIA GPU + CUDA 版 PyTorch + tensorrt 包。'
                    '本机若无 NVIDIA 显卡，请改用 .onnx 或 .pt 模型。'
                )
            elif ext == '.onnx' and 'onnxruntime' in str(e).lower():
                print('ONNX 推理需要安装: pip install onnxruntime')
            self._model = None
            self._model_path = None
            return False
    
    def predict(self, image_path: Path, threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
                label_manager: LabelManager = None) -> List[BBox]:
        """Run prediction on a single image (blocking — prefer predict_async in UI)."""
        if not self._model:
            return []
        
        try:
            results = self._model(image_path, conf=threshold, verbose=False)
            return self._results_to_bboxes(results, label_manager)
        except Exception as e:
            print(f"Prediction error for {image_path}: {e}")
            return []
    
    def _results_to_bboxes(self, results, label_manager: LabelManager = None) -> List[BBox]:
        annotations = []
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue
            
            img_w = result.orig_shape[1]
            img_h = result.orig_shape[0]
            
            for box in boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                class_id = int(box.cls[0].cpu().numpy())
                confidence = float(box.conf[0].cpu().numpy())
                
                if label_manager and label_manager.has_class(class_id):
                    class_name = label_manager.get_name(class_id)
                elif result.names:
                    class_name = result.names.get(class_id, f'class_{class_id}')
                else:
                    class_name = f'class_{class_id}'
                
                bbox = BBox(
                    x1=float(x1), y1=float(y1),
                    x2=float(x2), y2=float(y2),
                    class_id=class_id,
                    class_name=class_name,
                    confidence=confidence,
                )
                bbox.clamp_to_image(img_w, img_h)
                annotations.append(bbox)
        
        return annotations
    
    def predict_async(
        self,
        image_path: Path,
        threshold: float,
        label_manager: LabelManager,
        done_callback: Callable[[List[BBox], float], None],
        main_thread_schedule: Callable[[Callable], None] = None,
    ):
        """Run single-image prediction in a background thread."""
        self._cancel_event.clear()
        self._busy = True
        
        def run():
            started = time.perf_counter()
            try:
                annotations = self.predict(image_path, threshold, label_manager)
                elapsed = time.perf_counter() - started
            except Exception as e:
                print(f'Async prediction error: {e}')
                annotations = []
                elapsed = time.perf_counter() - started
            finally:
                self._busy = False
            
            def finish():
                done_callback(annotations, elapsed)
            
            if main_thread_schedule:
                main_thread_schedule(finish)
            else:
                finish()
        
        threading.Thread(target=run, daemon=True).start()
    
    def batch_predict(
        self,
        image_items: List[ImageItem],
        threshold: float,
        label_manager: LabelManager,
        progress_callback: Callable[[int, int], None] = None,
        done_callback: Callable[[Dict[str, List[BBox]], float, int], None] = None,
        main_thread_schedule: Callable[[Callable], None] = None,
        item_done_callback: Callable[[ImageItem, List[BBox]], None] = None,
        root=None,
    ) -> threading.Event:
        """Run batch prediction in a background thread."""
        self._cancel_event.clear()
        self._busy = True
        results: Dict[str, List[BBox]] = {}
        
        def run():
            started = time.perf_counter()
            total = len(image_items)
            processed = 0
            
            try:
                for i, item in enumerate(image_items):
                    if self._cancel_event.is_set():
                        break
                    
                    annotations = self.predict(item.path, threshold, label_manager)
                    results[str(item.path)] = annotations
                    processed = i + 1
                    
                    if item_done_callback:
                        if main_thread_schedule:
                            main_thread_schedule(
                                lambda it=item, anns=annotations: item_done_callback(it, anns)
                            )
                        elif root:
                            root.after(0, item_done_callback, item, annotations)
                        else:
                            item_done_callback(item, annotations)
                    
                    if progress_callback:
                        current, count = processed, total
                        if main_thread_schedule:
                            main_thread_schedule(
                                lambda c=current, t=count: progress_callback(c, t)
                            )
                        elif root:
                            root.after(0, progress_callback, current, total)
                        else:
                            progress_callback(current, total)
            finally:
                elapsed = time.perf_counter() - started
                self._busy = False
                
                if done_callback:
                    def finish():
                        done_callback(results, elapsed, processed)
                    
                    if main_thread_schedule:
                        main_thread_schedule(finish)
                    elif root:
                        root.after(0, finish)
                    else:
                        finish()
        
        threading.Thread(target=run, daemon=True).start()
        return self._cancel_event
    
    @property
    def was_cancelled(self) -> bool:
        return self._cancel_event.is_set()
    
    def cancel(self):
        """Cancel ongoing batch prediction."""
        self._cancel_event.set()

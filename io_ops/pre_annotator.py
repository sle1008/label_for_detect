"""YOLO pre-annotation engine."""

import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Dict, Set

from core.annotation import BBox
from core.image_item import ImageItem
from core.label_manager import LabelManager
from utils.constants import DEFAULT_CONFIDENCE_THRESHOLD


# ==================== 推理参数设置区 ====================
# 菜单未指定其他模式时，使用兼容性更好的标准检测和 NMS 后处理。
INFERENCE_MODE_ONE_TO_ONE = 'one-to-one'
INFERENCE_MODE_ONE_TO_MANY_NMS = 'one-to-many+NMS'
INFERENCE_MODE_BACKEND_AUTO = 'backend-auto'
DEFAULT_INFERENCE_MODE = INFERENCE_MODE_ONE_TO_MANY_NMS
SUPPORTED_WEIGHT_EXTENSIONS: Set[str] = {'.pt', '.onnx', '.engine', '.trt'}


class PreAnnotator:
    """Uses YOLO for pre-annotation."""
    
    def __init__(self):
        self._model = None
        self._model_path: Optional[str] = None
        self._cancel_event = threading.Event()
        self._busy = False
        self._requested_inference_mode = DEFAULT_INFERENCE_MODE
        self._inference_mode = DEFAULT_INFERENCE_MODE
    
    @property
    def is_loaded(self) -> bool:
        return self._model is not None
    
    @property
    def is_busy(self) -> bool:
        return self._busy
    
    @property
    def model_path(self) -> Optional[str]:
        return self._model_path

    @property
    def inference_mode(self) -> str:
        return self._inference_mode

    @staticmethod
    def _native_detection_head(model):
        """返回原生 PyTorch 模型的检测头；导出后端由 Ultralytics 自动处理。"""
        if model is None:
            return None
        native_model = getattr(model, 'model', None)
        layers = getattr(native_model, 'model', None)
        if layers is None:
            return None
        try:
            return layers[-1]
        except (IndexError, KeyError, TypeError):
            return None

    def _sync_active_predictor_mode(self, end2end: bool):
        """同步已创建的预测后端，避免菜单切换后继续沿用旧检测分支。"""
        predictor = getattr(self._model, 'predictor', None)
        backend = getattr(predictor, 'model', None)
        if backend is None:
            return

        backend_head = self._native_detection_head(backend)
        if backend_head is not None:
            try:
                backend_head.end2end = end2end
            except (AttributeError, RuntimeError, TypeError):
                pass
        try:
            backend.end2end = end2end
        except (AttributeError, RuntimeError, TypeError):
            pass

    @staticmethod
    def _has_one_to_many_head(head) -> bool:
        """判断检测头是否仍保留 one-to-many 分支。"""
        return (
            getattr(head, 'cv2', None) is not None
            and getattr(head, 'cv3', None) is not None
        )

    @staticmethod
    def _supports_one_to_one(head) -> bool:
        """判断检测头是否包含完整的 one-to-one 分支。"""
        one_to_one = None
        try:
            one_to_one = getattr(head, 'one2one', None)
        except (AttributeError, RuntimeError, TypeError):
            pass
        return (
            isinstance(one_to_one, dict)
            and bool(one_to_one)
            and all(branch is not None for branch in one_to_one.values())
        )

    def _prepare_native_model_for_branch_switching(self) -> bool:
        """预先融合原生模型，同时保留两套可切换的检测分支。"""
        head = self._native_detection_head(self._model)
        if head is None or not self._supports_one_to_one(head):
            return True

        try:
            head.end2end = False
            native_model = getattr(self._model, 'model', None)
            fuse = getattr(native_model, 'fuse', None)
            if callable(fuse):
                try:
                    fuse(verbose=False)
                except TypeError:
                    fuse()
            return self._has_one_to_many_head(head)
        except Exception as exc:
            print(f'Failed to prepare switchable inference branches: {exc}')
            return False

    def _reload_native_model(self) -> bool:
        """重新载入原生权重，恢复被端到端融合移除的普通检测分支。"""
        if not self._model_path or Path(self._model_path).suffix.lower() != '.pt':
            return False
        try:
            from ultralytics import YOLO

            self._model = YOLO(self._model_path)
            return self._prepare_native_model_for_branch_switching()
        except Exception as exc:
            print(f'Failed to reload model ({self._model_path}): {exc}')
            return False

    def _configure_inference_mode(self) -> str:
        """根据用户选择配置检测分支，不支持时回落到标准 NMS。"""
        head = self._native_detection_head(self._model)
        if head is None:
            # ONNX/TensorRT 的推理图已经固化，后端会依据输出形状和元数据
            # 自动判断是否为端到端模型，运行时不应修改其网络结构。
            self._inference_mode = INFERENCE_MODE_BACKEND_AUTO
            return self._inference_mode

        supports_one_to_one = self._supports_one_to_one(head)
        if (
            self._requested_inference_mode == INFERENCE_MODE_ONE_TO_MANY_NMS
            and supports_one_to_one
            and not self._has_one_to_many_head(head)
        ):
            # Ultralytics 在端到端融合时会移除 one-to-many 分支，切回 NMS 前
            # 需要从原权重恢复完整检测头，避免后续前向计算缺少检测层。
            if not self._reload_native_model():
                head = self._native_detection_head(self._model)
                if head is not None:
                    try:
                        head.end2end = True
                    except (AttributeError, RuntimeError, TypeError):
                        pass
                self._sync_active_predictor_mode(True)
                self._inference_mode = INFERENCE_MODE_ONE_TO_ONE
                return self._inference_mode
            head = self._native_detection_head(self._model)
            if head is None:
                self._inference_mode = INFERENCE_MODE_ONE_TO_ONE
                return self._inference_mode
            supports_one_to_one = self._supports_one_to_one(head)
        if (
            self._requested_inference_mode == INFERENCE_MODE_ONE_TO_ONE
            and supports_one_to_one
        ):
            try:
                head.end2end = True
                if bool(getattr(head, 'end2end', False)):
                    self._sync_active_predictor_mode(True)
                    self._inference_mode = INFERENCE_MODE_ONE_TO_ONE
                    return self._inference_mode
            except (AttributeError, RuntimeError, TypeError):
                pass

        # 普通检测头由 Ultralytics 的预测器执行 one-to-many 后处理和 NMS。
        try:
            head.end2end = False
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._sync_active_predictor_mode(False)
        self._inference_mode = INFERENCE_MODE_ONE_TO_MANY_NMS
        return self._inference_mode

    def set_inference_mode(self, mode: str) -> str:
        """设置后续预标注使用的检测分支，并返回当前实际生效模式。"""
        if mode not in {
            INFERENCE_MODE_ONE_TO_ONE,
            INFERENCE_MODE_ONE_TO_MANY_NMS,
        }:
            mode = DEFAULT_INFERENCE_MODE
        self._requested_inference_mode = mode

        # 推理线程运行期间不修改模型检测头，避免影响当前任务；新选择会在
        # 当前任务结束后、下一次预标注开始前自动应用。
        if self._model is None or self._busy:
            return self._inference_mode
        return self._configure_inference_mode()
    
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
            self._prepare_native_model_for_branch_switching()
            self._configure_inference_mode()
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
            self._inference_mode = DEFAULT_INFERENCE_MODE
            return False
    
    def predict(self, image_path: Path, threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
                label_manager: LabelManager = None) -> List[BBox]:
        """Run prediction on a single image (blocking — prefer predict_async in UI)."""
        if not self._model:
            return []

        if not self._busy:
            self._configure_inference_mode()
        
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
        if self._model:
            self._configure_inference_mode()
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
        if self._model:
            self._configure_inference_mode()
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

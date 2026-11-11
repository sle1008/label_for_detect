"""应用会话状态初始化，与窗口创建分离。"""
from ui.qt.controller import *

# ==================== 会话参数设置区 ====================

def initialize_state(self, config_directory=None):
    self._project = Project()
    self._label_manager = LabelManager()
    self._label_note_store = LabelNoteStore(Path(config_directory) / 'label_notes.json' if config_directory else None)
    self._undo_manager = UndoRedoManager()
    self._image_undo_managers = {}
    self._navigation_undo_stack = []
    self._config_manager = ConfigManager(str(config_directory)) if config_directory else ConfigManager()
    self._config = self._config_manager.load()
    self._image_loader = AsyncImageLoader()
    self._pre_annotator = PreAnnotator()
    self._threshold = DEFAULT_CONFIDENCE_THRESHOLD
    self._scan_generation = 0
    self._directory_loading = False
    self._restore_dir_path = ''
    self._restore_image_path = ''
    self._restore_image_index = 0
    self._pending_weights_path = ''
    self._weights_load_in_progress = False
    self._main_thread_queue = queue.Queue()
    self._filter_status_snapshot = {}
    self._pending_refresh = None
    self._image_export_running = False
    self._recent_class_ids: List[int] = []
    self._label_cache_ready_generation = -1
    self._label_cache_job_generation = 0
    self._label_cache_jobs = set()
    self._label_cache_progress = 0
    self._pane_layout_after_id = None
    self._pane_layout_retry_count = 0
    self._last_displayed_index = -1
    self._last_save_error = ''
    self._label_filter_var = Value(value='全部标签')
    self._label_filter_display_to_id = {'全部标签': None}
    self._label_filter_popup = None
    self._video_player = None
    self._video_item = None
    self._video_open_action = None
    self._video_opening_path = ''
    self._video_export_pending = None
    self._video_export_running = False
    self._video_export_stop_event = None
    self._video_export_token = 0
    self._video_delete_running = False
    self._video_path = ''
    self._pending_video_generation = None
    self._video_open_stop_event = None
    self._video_duration = 0.0
    self._video_fps = 0.0
    self._video_frame_index = 0
    self._video_frame_count = 0
    self._video_playing = False
    self._video_speed = 1.0
    self._video_seek_active = False
    self._video_resume_after_seek = False
    self._video_annotations = None
    self._video_show_annotations = True
    self._video_inference_running = False
    self._video_inference_stop_event = None
    self._video_error_reported = False
    self._video_generation = 0
    self._video_poll_after_id = None

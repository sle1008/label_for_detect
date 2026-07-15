"""Dialog windows."""

import tkinter as tk
from tkinter import ttk, filedialog

from ui.window_utils import setup_modal_dialog, showwarning


class RestoreDirectoryDialog:
    """Ask whether to restore the last directory, with a timed default."""

    WIDTH = 520
    HEIGHT = 235
    COUNTDOWN_SECONDS = 5

    def __init__(self, parent, directory: str):
        self.result = None
        self._remaining = self.COUNTDOWN_SECONDS
        self._after_id = None
        self._dialog = tk.Toplevel(parent)
        self._dialog.title('继续上次工作')
        self._dialog.protocol('WM_DELETE_WINDOW', self._restore)

        self._setup_ui(directory)
        setup_modal_dialog(self._dialog, parent, self.WIDTH, self.HEIGHT)
        self._restore_button.focus_set()
        self._dialog.bind('<Return>', lambda event: self._restore())
        self._update_countdown()
        self._dialog.wait_window()

    def _setup_ui(self, directory: str):
        body = ttk.Frame(self._dialog, padding=(18, 16, 18, 8))
        body.pack(fill='both', expand=True)

        ttk.Label(
            body,
            text='是否恢复上次打开的图片目录？',
            font=('Microsoft YaHei UI', 11, 'bold'),
        ).pack(anchor='w')
        ttk.Label(
            body,
            text='恢复后将继续显示上次浏览的图片和筛选状态。',
        ).pack(anchor='w', pady=(5, 10))

        path_frame = ttk.LabelFrame(body, text='上次目录', padding=(10, 7))
        path_frame.pack(fill='x')
        ttk.Label(
            path_frame,
            text=directory,
            wraplength=self.WIDTH - 70,
            justify='left',
        ).pack(anchor='w')

        self._countdown_var = tk.StringVar()
        ttk.Label(body, textvariable=self._countdown_var).pack(anchor='w', pady=(10, 0))

        button_frame = ttk.Frame(self._dialog, padding=(18, 8, 18, 14))
        button_frame.pack(side='bottom', fill='x')
        self._restore_button = ttk.Button(
            button_frame,
            text='恢复上次目录',
            command=self._restore,
            width=16,
        )
        self._restore_button.pack(side='right', padx=(8, 0))
        ttk.Button(
            button_frame,
            text='打开其他目录...',
            command=self._browse,
            width=16,
        ).pack(side='right')

    def _update_countdown(self):
        if self.result is not None or not self._dialog.winfo_exists():
            return
        if self._remaining <= 0:
            self._restore()
            return
        self._countdown_var.set(
            f'若不选择，将在 {self._remaining} 秒后自动恢复上次目录。'
        )
        self._after_id = self._dialog.after(1000, self._tick)

    def _tick(self):
        self._after_id = None
        self._remaining -= 1
        self._update_countdown()

    def _finish(self, result: str):
        if self.result is not None:
            return
        self.result = result
        if self._after_id is not None:
            try:
                self._dialog.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        self._dialog.destroy()

    def _restore(self):
        self._finish('restore')

    def _browse(self):
        self._finish('browse')


class ExportDialog:
    """Export settings dialog."""
    
    WIDTH = 400
    HEIGHT = 320
    
    def __init__(self, parent):
        self.result = None
        self._parent = parent
        self._dialog = tk.Toplevel(parent)
        self._dialog.title('导出设置')
        
        self._setup_ui()
        setup_modal_dialog(self._dialog, parent, self.WIDTH, self.HEIGHT)
        self._dialog.wait_window()
    
    def _setup_ui(self):
        body = ttk.Frame(self._dialog, padding=(10, 10, 10, 0))
        body.pack(fill='both', expand=True)
        
        ttk.Label(body, text='导出格式:').pack(anchor='w', pady=(0, 2))
        self._format_var = tk.StringVar(value='YOLO')
        for fmt in ['YOLO', 'COCO JSON', 'Pascal VOC']:
            ttk.Radiobutton(body, text=fmt, variable=self._format_var,
                          value=fmt).pack(padx=10, anchor='w')
        
        ttk.Label(body, text='输出目录:').pack(anchor='w', pady=(10, 2))
        dir_frame = ttk.Frame(body)
        dir_frame.pack(fill='x')
        self._dir_var = tk.StringVar()
        ttk.Entry(dir_frame, textvariable=self._dir_var).pack(side='left', fill='x', expand=True)
        ttk.Button(dir_frame, text='浏览...', command=self._browse_dir).pack(side='right', padx=(4, 0))
        
        self._copy_images = tk.BooleanVar(value=True)
        ttk.Checkbutton(body, text='复制图片到输出目录',
                       variable=self._copy_images).pack(anchor='w', pady=(10, 0))
        
        btn_frame = ttk.Frame(self._dialog, padding=(10, 8, 10, 12))
        btn_frame.pack(side='bottom', fill='x')
        ttk.Button(btn_frame, text='开始导出', command=self._export, width=12).pack(
            side='right', padx=(4, 0),
        )
        ttk.Button(btn_frame, text='取消', command=self._cancel, width=10).pack(side='right')
    
    def _browse_dir(self):
        d = filedialog.askdirectory(parent=self._parent)
        if d:
            self._dir_var.set(d)
    
    def _export(self):
        output_dir = self._dir_var.get().strip()
        if not output_dir:
            showwarning(self._parent, '警告', '请选择输出目录')
            return
        
        self.result = {
            'format': self._format_var.get(),
            'output_dir': output_dir,
            'copy_images': self._copy_images.get()
        }
        self._dialog.destroy()
    
    def _cancel(self):
        self._dialog.destroy()


class LabelLoadDialog:
    """Choose folder-based or file-based label import."""

    WIDTH = 460
    HEIGHT = 360

    def __init__(self, parent, detection=None, has_open_directory: bool = False):
        self.result = None
        self._dialog = tk.Toplevel(parent)
        self._dialog.title('加载标签')

        self._setup_ui(detection, has_open_directory)
        setup_modal_dialog(self._dialog, parent, self.WIDTH, self.HEIGHT)
        self._dialog.wait_window()

    def _setup_ui(self, detection, has_open_directory: bool):
        pad = {'padx': 12, 'pady': 4}
        ttk.Label(
            self._dialog,
            text='选择标签导入方式：',
            font=('Microsoft YaHei UI', 10, 'bold'),
        ).pack(anchor='w', **pad)

        can_folder = (
            has_open_directory
            and detection is not None
            and detection.detected
            and detection.class_names
        )

        if can_folder:
            conf_text = {'high': '高', 'medium': '中'}.get(detection.confidence, '低')
            summary = detection.tree_summary or ''
            detail = (
                f'检测到 {len(detection.class_names)} 个可能的类别文件夹 '
                f'（置信度: {conf_text}）\n{detection.reason}\n\n{summary}'
            )
            box = ttk.LabelFrame(self._dialog, text='目录结构预览', padding=6)
            box.pack(fill='both', expand=True, padx=12, pady=4)
            ttk.Label(
                box, text=detail, justify='left',
                font=('Consolas', 9),
                wraplength=420,
            ).pack(anchor='w')
        else:
            hint = '当前未检测到「每类一个子文件夹」的结构。'
            if not has_open_directory:
                hint = '请先打开图片目录，才能从子文件夹导入标签。'
            elif detection and detection.reason:
                hint = f'未自动识别为类别文件夹：{detection.reason}'
            ttk.Label(self._dialog, text=hint, wraplength=420, justify='left').pack(
                anchor='w', **pad,
            )

        btn_frame = ttk.Frame(self._dialog)
        btn_frame.pack(pady=12)
        folder_btn = ttk.Button(
            btn_frame, text='按子文件夹名',
            command=lambda: self._choose('folder'),
            width=16,
        )
        folder_btn.pack(side='left', padx=4)
        if not can_folder:
            folder_btn.state(['disabled'])

        ttk.Button(
            btn_frame, text='从文件导入...',
            command=lambda: self._choose('file'), width=16,
        ).pack(side='left', padx=4)
        ttk.Button(
            btn_frame, text='取消',
            command=self._cancel, width=10,
        ).pack(side='left', padx=4)

    def _choose(self, choice: str):
        self.result = choice
        self._dialog.destroy()

    def _cancel(self):
        self._dialog.destroy()


class StatisticsDialog:
    """Statistics/summary dialog."""
    
    WIDTH = 400
    HEIGHT = 350
    
    def __init__(self, parent, project, label_manager):
        self._dialog = tk.Toplevel(parent)
        self._dialog.title('项目统计')
        
        self._setup_ui(project, label_manager)
        setup_modal_dialog(self._dialog, parent, self.WIDTH, self.HEIGHT)
    
    def _setup_ui(self, project, label_manager):
        frame = ttk.Frame(self._dialog, padding=20)
        frame.pack(fill='both', expand=True)
        
        # Project stats
        ttk.Label(frame, text='项目统计', font=('Arial', 14, 'bold')).pack(anchor='w')
        ttk.Separator(frame).pack(fill='x', pady=10)
        
        stats = [
            ('图片目录', str(project.image_dir) if project.image_dir else '未设置'),
            ('总图片数', str(project.total_images)),
            ('已标注图片', str(project.annotated_image_count())),
            ('总标注数', str(project.total_annotations())),
        ]
        
        for label, value in stats:
            row = ttk.Frame(frame)
            row.pack(fill='x', pady=2)
            ttk.Label(row, text=f'{label}:', width=15, anchor='e').pack(side='left')
            ttk.Label(row, text=value, anchor='w').pack(side='left', padx=(10, 0))
        
        # Class distribution
        ttk.Label(frame, text='类别分布', font=('Arial', 12, 'bold')).pack(anchor='w', pady=(15, 5))
        ttk.Separator(frame).pack(fill='x', pady=5)
        
        # Count annotations per class
        class_counts = {}
        for item in project.image_list:
            for ann in item.annotations:
                name = label_manager.get_name(ann.class_id)
                class_counts[name] = class_counts.get(name, 0) + 1
        
        if class_counts:
            for name, count in sorted(class_counts.items(), key=lambda x: -x[1]):
                row = ttk.Frame(frame)
                row.pack(fill='x', pady=1)
                ttk.Label(row, text=f'{name}:', width=15, anchor='e').pack(side='left')
                ttk.Label(row, text=str(count), anchor='w').pack(side='left', padx=(10, 0))
        else:
            ttk.Label(frame, text='暂无标注数据').pack(anchor='w')
        
        ttk.Button(frame, text='关闭', command=self._dialog.destroy,
                  width=10).pack(pady=15)


class JumpToImageDialog:
    """Jump to a 1-based index in the currently visible image list."""

    WIDTH = 340
    HEIGHT = 150

    def __init__(self, parent, total: int, current: int = 1):
        self.result = None
        self._parent = parent
        self._total = max(0, total)
        self._dialog = tk.Toplevel(parent)
        self._dialog.title('跳转')

        self._setup_ui(current)
        setup_modal_dialog(self._dialog, parent, self.WIDTH, self.HEIGHT)
        self._entry.focus_set()
        self._entry.select_range(0, tk.END)
        self._dialog.bind('<Return>', lambda e: self._confirm())
        self._dialog.wait_window()

    def _setup_ui(self, current: int):
        body = ttk.Frame(self._dialog, padding=(12, 12, 12, 0))
        body.pack(fill='both', expand=True)

        ttk.Label(
            body,
            text=f'跳转到第几张图？（1 - {self._total}）',
        ).pack(anchor='w', pady=(0, 8))

        self._entry = ttk.Entry(body, width=12, justify='center')
        self._entry.pack(anchor='w')
        if self._total > 0:
            self._entry.insert(0, str(max(1, min(current, self._total))))

        btn_frame = ttk.Frame(self._dialog, padding=(12, 8, 12, 12))
        btn_frame.pack(side='bottom', fill='x')
        ttk.Button(btn_frame, text='确定', command=self._confirm, width=10).pack(
            side='right', padx=(4, 0),
        )
        ttk.Button(btn_frame, text='取消', command=self._cancel, width=10).pack(side='right')

    def _confirm(self):
        text = self._entry.get().strip()
        if not text:
            showwarning(self._parent, '提示', '请输入图片序号')
            return
        try:
            value = int(text)
        except ValueError:
            showwarning(self._parent, '提示', '请输入有效的数字')
            return
        if not (1 <= value <= self._total):
            showwarning(self._parent, '提示', f'请输入 1 到 {self._total} 之间的序号')
            return
        self.result = value
        self._dialog.destroy()

    def _cancel(self):
        self._dialog.destroy()


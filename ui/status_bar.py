"""Status bar / log widget."""

import tkinter as tk
from tkinter import ttk
from datetime import datetime

from utils.constants import UI_BG_COLOR, UI_TEXT_MUTED, UI_BORDER


class StatusBar(ttk.Frame):
    """Bottom status bar with scrolling log messages."""
    
    MAX_LOG_LINES = 200
    
    def __init__(self, parent):
        super().__init__(parent)
        
        self._setup_ui()
        self.log('就绪')
    
    def _setup_ui(self):
        self.columnconfigure(0, weight=1)
        
        # Log text widget (single line showing latest message, with scrollback)
        self._log_frame = ttk.Frame(self)
        self._log_frame.grid(row=0, column=0, sticky='ew')
        self._log_frame.columnconfigure(1, weight=1)
        
        # Toggle button to expand/collapse log
        self._toggle_btn = ttk.Button(
            self._log_frame, text='\u25bc', width=2,
            command=self._toggle_log_panel
        )
        self._toggle_btn.grid(row=0, column=0, padx=(2, 0))
        
        # Current message display
        self._current_msg = tk.StringVar(value='')
        self._msg_label = ttk.Label(
            self._log_frame, textvariable=self._current_msg,
            anchor='center', padding=(4, 2),
            font=('Microsoft YaHei UI', 9)
        )
        self._msg_label.grid(row=0, column=1, sticky='ew')
        
        # Progress bar (hidden by default)
        self._progress_var = tk.DoubleVar(value=0)
        self._progress = ttk.Progressbar(
            self._log_frame, variable=self._progress_var,
            maximum=100, mode='determinate', length=200
        )
        self._progress.grid(row=0, column=2, sticky='e', padx=(4, 4))
        self._progress.grid_remove()
        
        # Expandable log panel (hidden by default)
        self._log_text = tk.Text(
            self, height=6, font=('Consolas', 9),
            bg='#1e1e1e', fg='#cccccc', insertbackground='white',
            relief='sunken', bd=1, wrap='word', state='disabled'
        )
        self._log_text.grid(row=1, column=0, sticky='ew', padx=2, pady=(0, 2))
        self._log_text.grid_remove()
        self._log_expanded = False
        
        # Separator on top
        sep = ttk.Separator(self, orient='horizontal')
        sep.grid(row=0, column=0, sticky='new')
        
        # Tag configs for log coloring
        self._log_text.tag_config('info', foreground='#cccccc')
        self._log_text.tag_config('success', foreground='#6ec96e')
        self._log_text.tag_config('warning', foreground='#dcdcaa')
        self._log_text.tag_config('error', foreground='#f44747')
    
    def log(self, message: str, level: str = 'info'):
        """Add a log message. Levels: info, success, warning, error."""
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_line = f'[{timestamp}] {message}'
        
        # Update current message display
        self._current_msg.set(log_line)
        
        # Add to scrollback log
        self._log_text.config(state='normal')
        self._log_text.insert('end', log_line + '\n', level)
        
        # Trim if too many lines
        line_count = int(self._log_text.index('end-1c').split('.')[0])
        if line_count > self.MAX_LOG_LINES:
            self._log_text.delete('1.0', f'{line_count - self.MAX_LOG_LINES}.0')
        
        self._log_text.see('end')
        self._log_text.config(state='disabled')
    
    def info(self, message: str):
        self.log(message, 'info')
    
    def success(self, message: str):
        self.log(message, 'success')
    
    def warning(self, message: str):
        self.log(message, 'warning')
    
    def error(self, message: str):
        self.log(message, 'error')
    
    def set_info(self, text: str):
        """Backward compatibility - same as info()."""
        self.info(text)

    def set_overlay(self, text: str):
        """Set the current message without writing to log history."""
        self._current_msg.set(text)
    
    def update_status(self, current_img: int, total_imgs: int,
                      img_width: int = 0, img_height: int = 0,
                      ann_count: int = 0, zoom: float = 1.0,
                      mode: str = '', total_suffix: str = '',
                      category: str = ''):
        """Update status with image information."""
        parts = []
        if total_imgs > 0:
            parts.append(f'图片: {current_img}/{total_imgs}{total_suffix}')
        if category:
            parts.append(f'分类: {category}')
        if img_width > 0 and img_height > 0:
            parts.append(f'尺寸: {img_width}x{img_height}')
        if ann_count >= 0:
            parts.append(f'标注: {ann_count}')
        if zoom > 0:
            parts.append(f'缩放: {zoom*100:.0f}%')
        if mode:
            parts.append(f'模式: {mode}')
        
        status_text = ' | '.join(parts) if parts else '就绪'
        self._current_msg.set(status_text)
    
    def show_progress(self, value: float = 0):
        """Show progress bar."""
        self._progress_var.set(value)
        self._progress.grid()
    
    def update_progress(self, value: float):
        """Update progress bar value (0-100)."""
        self._progress_var.set(value)
    
    def hide_progress(self):
        """Hide progress bar."""
        self._progress.grid_remove()
    
    def _toggle_log_panel(self):
        """Toggle the expanded log panel."""
        self._log_expanded = not self._log_expanded
        if self._log_expanded:
            self._log_text.grid()
            self._toggle_btn.config(text='\u25b2')
        else:
            self._log_text.grid_remove()
            self._toggle_btn.config(text='\u25bc')

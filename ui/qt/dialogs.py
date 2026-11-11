"""统一风格的 Qt 对话框，文件选择和消息窗口均在 Qt 事件循环中运行。"""

from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox,
    QComboBox, QLineEdit, QCheckBox, QSpinBox, QProgressBar, QTextBrowser,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from ui.qt.bridge import filedialog

# ==================== 对话框参数设置区 ====================
DIALOG_WIDTH = 520
RESTORE_COUNTDOWN_SECONDS = 10


def showinfo(parent, title, message):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(DIALOG_WIDTH, 460 if len(message) > 600 else 260)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 24, 24, 20)
    text = QTextBrowser()
    text.setPlainText(message)
    text.setOpenExternalLinks(True)
    layout.addWidget(text)
    button = QPushButton('知道了')
    button.setObjectName('primary')
    button.clicked.connect(dialog.accept)
    layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignRight)
    dialog.exec()


def showwarning(parent, title, message):
    QMessageBox.warning(parent, title, message)


def showerror(parent, title, message):
    QMessageBox.critical(parent, title, message)


def askyesno(parent, title, message):
    box = QMessageBox(QMessageBox.Icon.Question, title, message, parent=parent)
    yes = box.addButton('确认', QMessageBox.ButtonRole.AcceptRole)
    box.addButton('取消', QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() == yes


class ChoiceDialog:
    def __init__(self, parent, title, description=''):
        self.result = None
        self.dialog = QDialog(parent)
        self.dialog.setWindowTitle(title)
        self.dialog.setMinimumWidth(DIALOG_WIDTH)
        self.layout = QVBoxLayout(self.dialog)
        self.layout.setContentsMargins(24, 24, 24, 24)
        self.layout.setSpacing(14)
        heading = QLabel(title)
        heading.setObjectName('brand')
        self.layout.addWidget(heading)
        if description:
            label = QLabel(description)
            label.setWordWrap(True)
            label.setObjectName('muted')
            self.layout.addWidget(label)

    def button(self, text, result, primary=False, enabled=True):
        button = QPushButton(text)
        button.setEnabled(enabled)
        if primary:
            button.setObjectName('primary')
        button.clicked.connect(lambda: self.finish(result))
        self.layout.addWidget(button)
        return button

    def finish(self, result):
        self.result = result
        self.dialog.accept()


class RestoreDirectoryDialog(ChoiceDialog):
    def __init__(self, parent, directory):
        super().__init__(parent, '继续上次工作', '恢复上次目录和浏览位置，或选择一个新目录。')
        path = QLabel(directory)
        path.setWordWrap(True)
        self.layout.addWidget(path)
        self.remaining = RESTORE_COUNTDOWN_SECONDS
        self.restore = self.button(f'继续上次工作 · {self.remaining}s', 'restore', True)
        self.button('选择其他目录', 'browse')
        self.timer = QTimer(self.dialog)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.dialog.exec()
        self.timer.stop()

    def tick(self):
        self.remaining -= 1
        self.restore.setText(f'继续上次工作 · {self.remaining}s')
        if self.remaining <= 0:
            self.finish('restore')


class LabelLoadDialog(ChoiceDialog):
    def __init__(self, parent, detection=None, has_open_directory=False):
        super().__init__(parent, '加载标签集', '选择类别定义的来源。导入后将使用原始类别编号进行标注。')
        self.button('从 TXT / YAML 文件导入', 'file', True)
        enabled = bool(has_open_directory and detection and detection.detected and detection.class_names)
        if enabled:
            preview = QTextBrowser()
            preview.setPlainText(f'{detection.reason}\n\n{detection.tree_summary}')
            preview.setMinimumHeight(160)
            self.layout.addWidget(preview)
        self.button('按当前目录的子文件夹名导入', 'folder', enabled=enabled)
        self.button('取消', None)
        self.dialog.exec()


class JumpToImageDialog(ChoiceDialog):
    def __init__(self, parent, total, current=1):
        super().__init__(parent, '跳转到图片', f'输入图片序号，范围 1–{total}')
        self.number = QSpinBox()
        self.number.setRange(1, total)
        self.number.setValue(current)
        self.layout.addWidget(self.number)
        button = QPushButton('跳转')
        button.setObjectName('primary')
        button.clicked.connect(lambda: self.finish(self.number.value()))
        self.layout.addWidget(button)
        self.dialog.exec()


class ExportDialog(ChoiceDialog):
    def __init__(self, parent):
        super().__init__(parent, '导出数据集', '选择格式和输出位置，已有的标注会先保存。')
        self.format = QComboBox()
        self.format.addItems(['YOLO', 'COCO JSON', 'Pascal VOC'])
        self.layout.addWidget(self.format)
        row = QHBoxLayout()
        self.directory = QLineEdit()
        self.directory.setPlaceholderText('选择输出目录')
        row.addWidget(self.directory)
        browse = QPushButton('选择…')
        browse.clicked.connect(lambda: self.directory.setText(filedialog.askdirectory(parent=self.dialog)))
        row.addWidget(browse)
        self.layout.addLayout(row)
        self.copy = QCheckBox('同时复制图片到输出目录')
        self.copy.setChecked(True)
        self.layout.addWidget(self.copy)
        button = QPushButton('开始导出')
        button.setObjectName('primary')
        button.clicked.connect(self.export)
        self.layout.addWidget(button)
        self.dialog.exec()

    def export(self):
        if not self.directory.text().strip():
            showwarning(self.dialog, '请选择位置', '请选择输出目录')
            return
        self.finish({'format': self.format.currentText(), 'output_dir': self.directory.text().strip(),
                     'copy_images': self.copy.isChecked()})


class ImageExportModeDialog(ChoiceDialog):
    def __init__(self, parent, image_count, default_mode='copy'):
        super().__init__(parent, '导出所选图片', f'共 {image_count} 张图片，将同时导出对应的标注文件。')
        self.button('复制到新位置 · 保留源文件', 'copy', default_mode == 'copy')
        self.button('移动到新位置 · 移除源文件', 'move', default_mode == 'move')
        self.button('取消', None)
        self.dialog.exec()


class ImageExportConflictDialog(ChoiceDialog):
    def __init__(self, parent, conflict_count):
        super().__init__(parent, '处理同名文件', f'目标位置有 {conflict_count} 个同名文件。')
        self.button('自动添加后缀，保留全部文件', 'suffix', True)
        self.button('跳过同名文件', 'skip')
        self.button('取消导出', None)
        self.dialog.exec()


class ImageExportProgressDialog:
    def __init__(self, parent, total, mode):
        self.dialog = QDialog(parent)
        self.dialog.setWindowTitle('正在导出图片')
        self.dialog.setMinimumWidth(DIALOG_WIDTH)
        self.dialog.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        layout = QVBoxLayout(self.dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        self.label = QLabel(f'正在处理 {total} 张图片…')
        self.progress = QProgressBar()
        self.progress.setRange(0, total)
        layout.addWidget(self.label)
        layout.addWidget(self.progress)

    def update_progress(self, completed, total, name):
        self.label.setText(f'{completed} / {total}  ·  {name}')
        self.progress.setValue(completed)

    def wait(self):
        self.dialog.exec()

    def close(self):
        self.dialog.accept()


class StatisticsDialog(ChoiceDialog):
    def __init__(self, parent, project, label_manager):
        stats = {'总图片数': project.total_images, '已标注图片': project.annotated_image_count(),
                 '总标注数': project.total_annotations()}
        for item in project.image_list:
            for box in item.annotations:
                key = f'类别 · {box.class_name}'
                stats[key] = stats.get(key, 0) + 1
        super().__init__(parent, '项目统计', f'当前项目共有 {len(project.image_list)} 张图片')
        text = QTextBrowser()
        text.setPlainText('\n'.join(f'{key}: {value}' for key, value in stats.items()))
        self.layout.addWidget(text)
        self.button('关闭', True, True)
        self.dialog.exec()

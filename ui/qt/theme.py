"""统一的工作台色彩、尺寸和原生 Qt 控件样式。"""

from PySide6.QtGui import QColor, QFont, QPainter, QPen, QIcon, QPixmap
from PySide6.QtCore import Qt, QPointF, QRectF

# ==================== 主题参数设置区 ====================
BACKGROUND = '#101318'
SURFACE = '#191e26'
RAISED = '#222a35'
TEXT = '#e7edf6'
MUTED = '#8e9caf'
ACCENT = '#5d91ff'
BORDER = '#2b3442'
FONT_FAMILY = 'Microsoft YaHei UI'
FONT_SIZE = 10

STYLESHEET = '''
QMainWindow, QDialog { background: #101318; color: #e7edf6; }
QWidget { font-family: "Microsoft YaHei UI"; font-size: 13px; color: #e7edf6; }
QFrame#panel, QWidget#panel { background: #191e26; border: 1px solid #2b3442; border-radius: 10px; }
QFrame#header { background: #191e26; border-bottom: 1px solid #2b3442; }
QLabel { background: transparent; }
QLabel#eyebrow { color: #8e9caf; font-size: 11px; font-weight: 600; }
QLabel#sectionTitle { color: #e7edf6; font-size: 14px; font-weight: 600; }
QLabel#brand { color: #f2f6ff; font-size: 20px; font-weight: 700; }
QLabel#muted { color: #8e9caf; }
QLabel#badge { background: #223451; color: #98baff; padding: 4px 10px; border-radius: 10px; font-size: 11px; }
QPushButton, QToolButton { background: #222a35; border: 1px solid #344052; border-radius: 6px;
    padding: 7px 12px; color: #d6e0ef; }
QPushButton:hover, QToolButton:hover { background: #2b3850; border-color: #668acc; }
QPushButton:pressed, QToolButton:pressed { background: #344b71; }
QPushButton:checked { background: #2c456c; border-color: #78a4ff; }
QPushButton#primary { background: #477df0; color: white; border-color: #6496ff; font-weight: 600; }
QPushButton#primary:hover { background: #5b8df7; }
QPushButton#danger { color: #ff939b; background: #3c242d; border-color: #63343f; }
QPushButton:disabled, QToolButton:disabled { color: #586578; background: #191f28; border-color: #26303d; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #121720; border: 1px solid #344052;
    border-radius: 6px; padding: 7px 10px; selection-background-color: #355a96; }
QLineEdit:focus, QComboBox:focus { border-color: #5d91ff; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #222a35; selection-background-color: #334f7b; }
QListView, QTableView, QTreeView { background: transparent; border: none; outline: none;
    selection-background-color: #2c456c; selection-color: #edf4ff; alternate-background-color: #1c232d; }
QListView::item { padding: 8px 7px; border-radius: 5px; }
QListView::item:hover { background: #252f3d; }
QListView::item:selected { background: #2c456c; border-left: 2px solid #78a4ff; }
QTableView::item { padding: 6px; border-bottom: 1px solid #242d39; }
QHeaderView { background: #1e2631; }
QHeaderView::section { background: #1e2631; color: #8e9caf; border: none; padding: 8px; font-size: 11px; }
QTableCornerButton::section { background: #1e2631; border: none; }
QMenuBar { background: #101318; color: #8e9caf; padding: 3px 8px; }
QMenuBar::item:selected { background: #2c3c54; color: #ffffff; border-radius: 4px; }
QMenu { background: #202733; border: 1px solid #3a4659; padding: 6px; }
QMenu::item { padding: 8px 28px 8px 12px; border-radius: 4px; }
QMenu::item:selected { background: #34527f; }
QMenu::item:disabled { color: #596779; }
QMenu::separator { height: 1px; background: #354154; margin: 5px; }
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: #477df0; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle { background: #39485e; border-radius: 3px; min-height: 24px; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QSlider::groove:horizontal { background: #303d50; height: 4px; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #5d91ff; border-radius: 2px; }
QSlider::handle:horizontal { background: #c9dcff; width: 12px; margin: -5px 0; border-radius: 6px; }
QProgressBar { background: #253246; border: none; border-radius: 3px; height: 5px; color: transparent; }
QProgressBar::chunk { background: #5d91ff; border-radius: 3px; }
QPlainTextEdit, QTextEdit, QTextBrowser { background: #121720; border: 1px solid #2b3442; border-radius: 6px; padding: 8px; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: #8e9caf; padding: 10px 16px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #afcbff; border-bottom: 2px solid #5d91ff; }
QToolTip { background: #26334a; color: #eef4ff; border: 1px solid #486186; padding: 6px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #52617a; border-radius: 4px; background: #151c27; }
QCheckBox::indicator:checked { background: #5d91ff; border-color: #82adff; }
'''


def icon(name, color=TEXT, size=20):
    """通过矢量笔画生成统一图标，在高分屏上保持清晰。"""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 12, size / 12)
    painter.setPen(QPen(QColor(color), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    paths = {
        'folder': [(3, 6, 10, 6), (3, 6, 3, 18), (3, 18, 21, 18), (21, 18, 21, 8),
                   (21, 8, 11, 8), (11, 8, 9, 6)],
        'save': [(5, 3, 19, 3), (19, 3, 21, 5), (21, 5, 21, 21), (21, 21, 3, 21),
                 (3, 21, 3, 3), (3, 3, 5, 3), (7, 3, 7, 9), (7, 9, 17, 9),
                 (17, 9, 17, 3), (7, 21, 7, 14), (7, 14, 17, 14), (17, 14, 17, 21)],
        'video': [(3, 6, 16, 6), (16, 6, 16, 18), (16, 18, 3, 18), (3, 18, 3, 6),
                  (16, 10, 22, 6), (22, 6, 22, 18), (22, 18, 16, 14)],
        'export': [(12, 3, 12, 15), (8, 7, 12, 3), (12, 3, 16, 7), (4, 14, 4, 21),
                   (4, 21, 20, 21), (20, 21, 20, 14)],
        'box': [(4, 4, 20, 4), (20, 4, 20, 20), (20, 20, 4, 20), (4, 20, 4, 4)],
        'prev': [(15, 5, 8, 12), (8, 12, 15, 19)],
        'next': [(9, 5, 16, 12), (16, 12, 9, 19)],
        'undo': [(9, 5, 4, 10), (4, 10, 9, 15), (4, 10, 15, 10), (15, 10, 20, 15), (20, 15, 20, 19)],
        'redo': [(15, 5, 20, 10), (20, 10, 15, 15), (20, 10, 9, 10), (9, 10, 4, 15), (4, 15, 4, 19)],
        'trash': [(4, 6, 20, 6), (9, 3, 15, 3), (6, 6, 7, 21), (7, 21, 17, 21),
                  (17, 21, 18, 6), (10, 10, 10, 17), (14, 10, 14, 17)],
        'rotate': [(4, 8, 7, 4), (7, 4, 15, 4), (15, 4, 20, 9), (20, 9, 20, 15),
                   (20, 15, 15, 20), (15, 20, 8, 20), (8, 20, 4, 16),
                   (4, 8, 4, 3), (4, 8, 9, 8)],
        'fit': [(3, 9, 3, 3), (3, 3, 9, 3), (15, 3, 21, 3), (21, 3, 21, 9),
                (21, 15, 21, 21), (21, 21, 15, 21), (9, 21, 3, 21), (3, 21, 3, 15)],
        'label': [(3, 4, 13, 4), (13, 4, 22, 13), (22, 13, 13, 22), (13, 22, 3, 12), (3, 12, 3, 4)],
        'spark': [(12, 2, 14, 9), (14, 9, 21, 12), (21, 12, 14, 15), (14, 15, 12, 22),
                  (12, 22, 9, 15), (9, 15, 2, 12), (2, 12, 9, 9), (9, 9, 12, 2)],
    }
    for x1, y1, x2, y2 in paths.get(name, paths['box']):
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)

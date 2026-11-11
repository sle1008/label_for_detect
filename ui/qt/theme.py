"""统一的工作台色彩、尺寸和原生 Qt 控件样式。"""

import re
from pathlib import Path
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QIcon, QPixmap, QPalette
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
SELECTION_BACKGROUND = '#4b78b4'
SELECTION_TEXT = '#ffffff'
CLASS_LIST_FONT_SIZE = 14
ASSET_DIRECTORY = Path(__file__).resolve().parent / 'assets'
THEME_COLORS = {
    'dark': {'background': BACKGROUND, 'surface': SURFACE, 'text': TEXT, 'muted': MUTED,
             'canvas': '#0d1117', 'placeholder': '#8e9caf', 'crosshair': '#6482a6',
             'success': '#78c8a0', 'uncertain': '#e9bd77'},
    'light': {'background': '#f2f5fa', 'surface': '#ffffff', 'text': '#233249', 'muted': '#596b83',
              'canvas': '#e7edf5', 'placeholder': '#596b83', 'crosshair': '#58779f',
              'success': '#238457', 'uncertain': '#94600f'},
}
# 成对映射控件色彩，替换时一次完成，避免浅色值再次命中深色替换项。
LIGHT_COLORS = {
    '#101318':'#f2f5fa', '#e7edf6':'#233249', '#191e26':'#ffffff', '#2b3442':'#d5deeb',
    '#8e9caf':'#596b83', '#f2f6ff':'#233249', '#223451':'#e3edff', '#98baff':'#315eaa',
    '#222a35':'#f4f7fc', '#344052':'#ccd7e6', '#d6e0ef':'#33465f', '#2b3850':'#e5edfa',
    '#668acc':'#7e9ed5', '#344b71':'#d2e1f8', '#2c456c':'#dce9ff', '#78a4ff':'#477df0',
    '#ff939b':'#ae2535', '#3c242d':'#fff0f2', '#63343f':'#efc0c8', '#586578':'#8a98ac',
    '#191f28':'#edf1f7', '#26303d':'#e0e6ef', '#121720':'#ffffff', '#355a96':'#c6dcff',
    '#334f7b':'#dce9ff', '#edf4ff':'#233249', '#1c232d':'#f6f8fc', '#252f3d':'#eaf0fa',
    '#242d39':'#e4eaf3', '#1e2631':'#eaf0f8', '#2c3c54':'#dce9ff', '#202733':'#ffffff',
    '#3a4659':'#cbd6e7', '#34527f':'#477df0', '#596779':'#8a98ac', '#354154':'#d5deeb',
    '#39485e':'#b3c2d7', '#303d50':'#d5dfed', '#c9dcff':'#477df0', '#253246':'#e2eaf6',
    '#afcbff':'#315eaa', '#26334a':'#ffffff', '#eef4ff':'#233249', '#486186':'#b0c2df',
    '#52617a':'#a6b8d0', '#151c27':'#ffffff',
}

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
QFrame#commands QPushButton { padding: 6px 8px; }
QToolButton#compact { padding: 2px 6px; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #121720; border: 1px solid #344052;
    border-radius: 6px; padding: 7px 10px; selection-background-color: #355a96; }
QLineEdit:focus, QComboBox:focus { border-color: #5d91ff; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #222a35; selection-background-color: #334f7b; }
QListView, QTableView, QTreeView { background: transparent; border: none; outline: none;
    selection-background-color: #2c456c; selection-color: #edf4ff; alternate-background-color: #1c232d; }
QListView::item { padding: 3px 7px; border-radius: 4px; }
QListView::item:hover { background: #252f3d; }
QListView::item:selected { background: #2c456c; border-left: 2px solid #78a4ff; }
QTableView::item { padding: 3px 6px; border-bottom: 1px solid #242d39; }
QHeaderView { background: #1e2631; }
QHeaderView::section { background: #1e2631; color: #8e9caf; border: none; padding: 6px; font-size: 11px; }
QTableCornerButton::section { background: #1e2631; border: none; }
QMenuBar { background: #101318; color: #8e9caf; padding: 3px 8px; }
QMenuBar::item:selected { background: #2c3c54; color: #ffffff; border-radius: 4px; }
QMenu { background: #202733; border: 1px solid #3a4659; padding: 6px; }
QMenu::item { padding: 5px 28px 5px 12px; border-radius: 4px; }
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
QToolButton#filter { text-align: left; padding: 7px 22px 7px 9px; }
QToolButton#filter::menu-indicator { subcontrol-origin: padding; subcontrol-position: bottom right; width: 10px; height: 10px; }
'''


def theme_color(name, theme='dark'):
    return THEME_COLORS.get(theme, THEME_COLORS['dark'])[name]


def apply_theme(application, theme):
    """同步所有 Qt 窗口的控件、文件选择框及原生绘图基础色。"""
    theme = theme if theme in THEME_COLORS else 'dark'
    application.setProperty('annotationTheme', theme)
    stylesheet = re.sub(r'#[0-9a-fA-F]{6}', lambda match: LIGHT_COLORS.get(match[0], match[0]), STYLESHEET) if theme == 'light' else STYLESHEET
    # 选中态使用独立色值，避免主题映射造成白字与浅色背景对比不足。
    stylesheet += f'''
QListWidget#classList {{ font-size: {CLASS_LIST_FONT_SIZE}px; }}
QListView, QTableView, QTreeView, QComboBox QAbstractItemView, QLineEdit, QTextEdit, QPlainTextEdit {{
    selection-background-color: {SELECTION_BACKGROUND}; selection-color: {SELECTION_TEXT}; }}
QListView::item:selected, QTableView::item:selected, QTreeView::item:selected,
QMenu::item:selected, QMenuBar::item:selected, QPushButton:checked, QToolButton:checked {{
    background: {SELECTION_BACKGROUND}; color: {SELECTION_TEXT}; }}'''
    arrow = (ASSET_DIRECTORY / f'chevron-{theme}.svg').as_posix()
    stylesheet += f'''QComboBox::down-arrow {{ image: url("{arrow}"); width: 10px; height: 10px; }}
QToolButton#filter::menu-indicator {{ image: url("{arrow}"); }}
QLabel#toast {{ background: {theme_color('surface', theme)}; color: {theme_color('text', theme)};
    border: 1px solid #648ac2; border-radius: 8px; padding: 10px 20px; }}
QLabel#ready {{ color: {theme_color('success', theme)}; font-size: 10px; }}'''
    palette = QPalette()
    for role, name in [(QPalette.ColorRole.Window, 'background'), (QPalette.ColorRole.Base, 'surface'),
                       (QPalette.ColorRole.WindowText, 'text'), (QPalette.ColorRole.Text, 'text'),
                       (QPalette.ColorRole.ButtonText, 'text'), (QPalette.ColorRole.Button, 'surface')]:
        palette.setColor(role, QColor(theme_color(name, theme)))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(SELECTION_BACKGROUND))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(SELECTION_TEXT))
    application.setPalette(palette)
    application.setStyleSheet(stylesheet)
    for widget in application.allWidgets():
        name = widget.property('annotationIcon')
        if name:
            widget.setIcon(icon(name, '#ffffff' if widget.objectName() == 'primary' else None))
        widget.update()


def set_button_icon(button, name):
    button.setProperty('annotationIcon', name)
    button.setIcon(icon(name))


def icon(name, color=None, size=20):
    """通过矢量笔画生成统一图标，在高分屏上保持清晰。"""
    from PySide6.QtWidgets import QApplication
    theme = QApplication.instance().property('annotationTheme') if QApplication.instance() else 'dark'
    color = color or theme_color('text', theme)
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
        'refresh': [(4, 9, 7, 4), (7, 4, 16, 4), (16, 4, 20, 9), (20, 9, 20, 4),
                    (20, 9, 15, 9), (20, 15, 17, 20), (17, 20, 8, 20), (8, 20, 4, 15)],
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

"""控制器与 Qt 的轻量边界：状态变量、菜单、文件选择和控件可见性。"""

from pathlib import Path
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFileDialog, QMenu, QWidget

# ==================== 界面适配参数设置区 ====================
FILE_DIALOG_OPTIONS = QFileDialog.Option.DontUseNativeDialog


class Value:
    """不依赖 GUI 工具包的可观察值，用于保存界面选择状态。"""
    def __init__(self, value=None, **kwargs):
        self.value = value
        self.callbacks = []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        for callback in list(self.callbacks):
            callback(value)

    def trace_add(self, mode, callback):
        self.callbacks.append(lambda value: callback())


class Menu:
    """用 Qt 菜单呈现控制器命令，命令声明与具体窗口布局解耦。"""
    def __init__(self, parent=None, **kwargs):
        owner = parent.widget if isinstance(parent, Menu) else parent
        self.widget = QMenu(owner if isinstance(owner, QWidget) else None)
        self._subscriptions = {}
        self.postcommand = None
        self.widget.aboutToShow.connect(self._prepare)

    def _prepare(self):
        if self.postcommand:
            self.postcommand()

    def add_command(self, label='', command=None, state='normal', accelerator='', **kwargs):
        action = self.widget.addAction(label + (f'\t{accelerator}' if accelerator else ''))
        action.setEnabled(state != 'disabled')
        if command:
            action.triggered.connect(lambda checked=False: command())
        return action

    def add_separator(self):
        self.widget.addSeparator()

    def add_cascade(self, label='', menu=None, **kwargs):
        menu.widget.setTitle(label)
        self.widget.addMenu(menu.widget)

    def add_radiobutton(self, label='', variable=None, value=None, command=None, **kwargs):
        action = self.add_command(label, state=kwargs.get('state', 'normal'))
        action.setCheckable(True)
        action.setChecked(variable.get() == value)
        callback = lambda current: action.setChecked(current == value)
        variable.callbacks.append(callback)
        self._subscriptions[action] = (variable, callback)
        action.destroyed.connect(lambda: self._unsubscribe(action))
        action.triggered.connect(lambda: (variable.set(value), command() if command else None))

    def _unsubscribe(self, action):
        subscription = self._subscriptions.pop(action, None)
        if subscription:
            variable, callback = subscription
            if callback in variable.callbacks:
                variable.callbacks.remove(callback)

    add_checkbutton = add_radiobutton

    def delete(self, start, end=None):
        actions = self.widget.actions()
        if not actions:
            return
        start = len(actions) - 1 if start == 'end' else int(start)
        end = len(actions) - 1 if end in ('end', None) else int(end)
        for action in actions[start:end + 1]:
            self._unsubscribe(action)
            self.widget.removeAction(action)
            action.deleteLater()

    def index(self, index):
        count = len(self.widget.actions())
        return count - 1 if count else None

    def configure(self, **kwargs):
        self.postcommand = kwargs.get('postcommand', self.postcommand)

    def tk_popup(self, x, y):
        self.widget.exec(QPoint(int(x), int(y)))

    post = tk_popup

    def grab_release(self):
        pass


class FileDialogs:
    @staticmethod
    def _filter(types):
        return ';;'.join(f'{name} ({patterns})' for name, patterns in types or [])

    @classmethod
    def askopenfilename(cls, parent=None, title='打开文件', initialdir='', filetypes=None, **kwargs):
        return QFileDialog.getOpenFileName(parent, title, initialdir, cls._filter(filetypes),
                                          options=FILE_DIALOG_OPTIONS)[0]

    @classmethod
    def asksaveasfilename(cls, parent=None, title='保存文件', initialdir='', initialfile='',
                          filetypes=None, defaultextension='', **kwargs):
        path = QFileDialog.getSaveFileName(parent, title, str(Path(initialdir) / initialfile),
                                          cls._filter(filetypes), options=FILE_DIALOG_OPTIONS)[0]
        if path and defaultextension and not Path(path).suffix:
            path += defaultextension
        return path

    @staticmethod
    def askdirectory(parent=None, title='选择目录', initialdir='', **kwargs):
        return QFileDialog.getExistingDirectory(parent, title, initialdir, options=FILE_DIALOG_OPTIONS)


filedialog = FileDialogs


class VisibilityMixin:
    """统一控制器所需的显示接口，不承担业务状态。"""
    def pack(self, **kwargs):
        self.show()

    def pack_forget(self):
        self.hide()

    def winfo_width(self):
        return self.width()

    def winfo_height(self):
        return self.height()

    def focus_set(self):
        self.setFocus()


def event_position(position):
    from types import SimpleNamespace
    return SimpleNamespace(x_root=position.x(), y_root=position.y())

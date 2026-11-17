"""为 Qt 标准控件提供应用级中文翻译。"""

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator

# ==================== 界面翻译参数设置区 ====================
QT_TRANSLATION_LOCALE = 'zh_CN'
QT_TRANSLATION_CATALOGS = ('qtbase', 'qt')
TRANSLATOR_STATE_ATTRIBUTE = '_annotation_chinese_translators'

# 标准词条仅用于语言包缺失或 Qt 新版尚未补译的情况，不匹配业务界面文字。
_EDIT_TRANSLATIONS = {
    '&Undo': '撤销(&U)',
    '&Redo': '重做(&R)',
    'Cu&t': '剪切(&T)',
    '&Copy': '复制(&C)',
    '&Paste': '粘贴(&P)',
    'Delete': '删除',
    'Select All': '全选',
    '&Select All': '全选(&A)',
}
_BUTTON_TRANSLATIONS = {
    'OK': '确定',
    '&OK': '确定(&O)',
    'Cancel': '取消',
    '&Cancel': '取消(&C)',
    'Open': '打开',
    '&Open': '打开(&O)',
    'Save': '保存',
    '&Save': '保存(&S)',
    'Save All': '全部保存',
    'Yes': '是',
    '&Yes': '是(&Y)',
    'No': '否',
    '&No': '否(&N)',
    'Yes to All': '全部是',
    'Yes to &All': '全部是(&A)',
    'No to All': '全部否',
    'N&o to All': '全部否(&O)',
    'Abort': '终止',
    'Retry': '重试',
    'Ignore': '忽略',
    'Close': '关闭',
    '&Close': '关闭(&C)',
    'Discard': '不保存',
    'Apply': '应用',
    'Reset': '重置',
    'Restore Defaults': '恢复默认值',
    'Help': '帮助',
}
_STANDARD_TRANSLATIONS = {
    'QFileDialog': {
        'Look in:': '查找范围：',
        '&Look in:': '查找范围(&L)：',
        'File &name:': '文件名称(&N)：',
        'Files of type:': '文件类型：',
        'Files of &type:': '文件类型(&T)：',
        'Directory:': '目录：',
        '&Open': '打开(&O)',
        'Open': '打开',
        '&Save': '保存(&S)',
        '&Choose': '选择(&C)',
        'All Files (*)': '所有文件 (*)',
        'All files (*)': '所有文件 (*)',
        'Back': '后退',
        'Forward': '前进',
        'Parent Directory': '上级目录',
        'Go back': '后退',
        'Go forward': '前进',
        'Go to the parent directory': '转到上级目录',
        'Create New Folder': '新建文件夹',
        'Create a New Folder': '新建文件夹',
        'List View': '列表视图',
        'Detail View': '详细视图',
        'Change to list view mode': '切换为列表视图',
        'Change to detail view mode': '切换为详细视图',
        'Files': '文件',
        'Sidebar': '侧边栏',
        'List of places and bookmarks': '位置和书签列表',
        'Recent Places': '最近位置',
        '&Rename': '重命名(&R)',
        '&Delete': '删除(&D)',
        'Delete': '删除',
        'Show &hidden files': '显示隐藏文件(&H)',
        '&New Folder': '新建文件夹(&N)',
        'New Folder': '新建文件夹',
        'Directories': '目录',
        'Find Directory': '选择目录',
        'Save As': '另存为',
        'Show ': '显示 ',
        "Are you sure you want to delete '%1'?": '确定要删除“%1”吗？',
        "'%1' is write protected.\nDo you want to delete it anyway?":
            '“%1”已被写保护。\n仍然要删除吗？',
        'Could not delete directory.': '无法删除目录。',
        '%1 already exists.\nDo you want to replace it?': '%1 已存在。\n要替换它吗？',
        '%1\nDirectory not found.\n': '%1\n未找到目录。\n',
        '%1\nDirectory not found.\nPlease verify the correct directory name was given.':
            '%1\n未找到目录。\n请检查目录名称是否正确。',
        '%1\nFile not found.\nPlease verify the correct file name was given.':
            '%1\n未找到文件。\n请检查文件名称是否正确。',
    },
    'QFileSystemModel': {
        'My Computer': '我的电脑',
        'Computer': '计算机',
        'Name': '名称',
        'Size': '大小',
        'Type': '类型',
        'Kind': '类型',
        'Date Modified': '修改日期',
        'Invalid filename': '无效的文件名',
        '<b>The name "%1" can not be used.</b><p>Try using another name, '
        'with fewer characters or no punctuations.':
            '<b>无法使用名称“%1”。</b><p>请使用更短或不含标点的名称。',
    },
    'QLineEdit': _EDIT_TRANSLATIONS,
    'QWidgetTextControl': _EDIT_TRANSLATIONS,
    'QPlatformTheme': _BUTTON_TRANSLATIONS,
    'QMessageBox': {
        **_BUTTON_TRANSLATIONS,
        'Show Details...': '显示详细信息…',
        'Hide Details...': '隐藏详细信息…',
    },
}


class _ChineseFallbackTranslator(QTranslator):
    """只补齐已知标准词条，让官方语言包及其他翻译器继续处理其余内容。"""

    def isEmpty(self):
        return False

    def translate(self, context, source_text, disambiguation=None, n=-1):
        # 未命中时返回空 QString，避免截断 Qt 的翻译器查找链。
        return _STANDARD_TRANSLATIONS.get(context, {}).get(source_text)


def install_chinese_translations(application: QCoreApplication):
    """为当前应用安装一次中文翻译，并随应用对象保存翻译器的生命周期。"""
    installed = getattr(application, TRANSLATOR_STATE_ATTRIBUTE, None)
    if installed is not None:
        return installed

    # Qt 从最后安装的翻译器开始查找，优先使用随当前版本提供的官方译文。
    fallback = _ChineseFallbackTranslator(application)
    application.installTranslator(fallback)
    translators = [fallback]
    translations_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    for catalog in QT_TRANSLATION_CATALOGS:
        translator = QTranslator()
        if translator.load(f'{catalog}_{QT_TRANSLATION_LOCALE}', translations_path):
            translator.setParent(application)
            application.installTranslator(translator)
            translators.append(translator)
            break

    installed = tuple(translators)
    setattr(application, TRANSLATOR_STATE_ATTRIBUTE, installed)
    return installed

"""Persistent display-only notes for label names."""

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional

from utils.paths import get_app_root


LABEL_NOTES_FILENAME = 'label_notes.json'

DEFAULT_LABEL_NOTES = {
    'bear': '熊',
    'cat': '猫',
    'chicken': '鸡',
    'cow': '牛',
    'crane': '鹤',
    'deer': '鹿',
    'dog': '狗',
    'fox': '狐狸',
    'horse': '马',
    'rabbit': '兔',
    'raccoon': '浣熊',
    'sheep': '绵羊',
    'zebra': '斑马',
    'wild boar': '野猪',
    'coyote': '郊狼',
    'person': '人',
    'badger': '獾',
    'giraffe': '长颈鹿',
    'porcupine': '豪猪',
    'elephant': '大象',
    'bird': '鸟',
    'kudu': '捻角羚',
    'blue wildebeest': '蓝角马',
    'duck': '鸭',
    'turkey': '火鸡',
    'goose': '鹅',
    'kangaroo': '袋鼠',
    'donkey': '驴',
    'gemsbok': '南非剑羚',
    'barbados blackbelly sheep': '巴巴多斯黑腹羊',
    'roan antelope': '马羚',
    'springbok': '跳羚',
    'warthog': '疣猪',
    'wombat': '袋熊',
    'goat': '山羊',
    'american mink': '美洲水貂',
    'monkey': '猴',
    'lop rabbit': '垂耳兔',
    'ostrich': '鸵鸟',
    'african buffalo': '非洲水牛',
    'mouflon': '欧洲盘羊',
    'marten': '貂',
    'skunk': '臭鼬',
    'genet': '斑林狸',
    'opossum': '负鼠',
    'guineafowl': '珍珠鸡',
    'eland': '大羚羊',
    'mouse': '老鼠',
    'bobcat': '短尾猫',
    'hedgehog': '刺猬',
    'honey badger': '蜜獾',
    'armadillo': '犰狳',
    'pig': '猪',
    'animal': '动物',
    'snake': '蛇',
    'squirrel': '松鼠',
    'pheasant': '雉鸡',
    'stork': '鹳',
}


class LabelNoteStore:
    """Read and write global notes keyed by normalized original label names."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else get_app_root() / LABEL_NOTES_FILENAME
        self._notes: Dict[str, str] = {}
        self._load_or_initialize()

    @staticmethod
    def normalize_name(name: str) -> str:
        return ' '.join(str(name).strip().casefold().split())

    def _load_or_initialize(self):
        if not self.path.is_file():
            self._notes = dict(DEFAULT_LABEL_NOTES)
            try:
                self.save()
            except OSError as error:
                print(f'Failed to initialize label notes at {self.path}: {error}')
            return

        try:
            with open(self.path, 'r', encoding='utf-8') as file:
                data = json.load(file)
            if not isinstance(data, dict):
                raise ValueError('label notes must be a JSON object')
            self._notes = {
                self.normalize_name(name): str(note).strip()
                for name, note in data.items()
                if self.normalize_name(name) and str(note).strip()
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            print(f'Failed to load label notes from {self.path}: {error}')
            self._notes = {}

    def get(self, label_name: str) -> str:
        return self._notes.get(self.normalize_name(label_name), '')

    def set(self, label_name: str, note: str):
        key = self.normalize_name(label_name)
        if not key:
            return
        value = str(note).strip()
        if value:
            self._notes[key] = value
        else:
            self._notes.pop(key, None)
        self.save()

    def save(self):
        if not self.path.parent.is_dir():
            raise FileNotFoundError(f'Label note directory does not exist: {self.path.parent}')

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                newline='\n',
                dir=self.path.parent,
                prefix=f'.{self.path.name}.',
                suffix='.tmp',
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                json.dump(self._notes, temp_file, ensure_ascii=False, indent=2, sort_keys=True)
                temp_file.write('\n')
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, self.path)
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

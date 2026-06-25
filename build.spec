# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — single-file Windows GUI build."""

from pathlib import Path

block_cipher = None
root = Path(SPECPATH)
icon_file = root / 'app.ico'

a = Analysis(
    ['main.py'],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        'PIL._tkinter_finder',
        'yaml',
        'onnxruntime',
        'ultralytics',
        'ultralytics.nn',
        'ultralytics.models',
        'ultralytics.utils',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'scipy',
        'pandas',
        'notebook',
        'IPython',
        'jupyter',
        'jupyter_client',
        'pytest',
        'setuptools',
        'distutils',
        'tkinter.test',
        'test',
        'tests',
        'tensorboard',
        'tensorflow',
        'jax',
        'torch.distributed',
        'torch.testing',
        'torchvision.datasets',
        'cv2.gapi',
        'sklearn',
        'sympy',
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AnnotationTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file) if icon_file.is_file() else None,
)

# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = []
hiddenimports = ['sqlite3', 'pandas', 'numpy', 'openpyxl', 'xlrd', 'tqdm', 'tkinter', 'tkinter.ttk', 'tkinter.scrolledtext', 'tkinter.messagebox', 'tkinter.filedialog', 'task_runner', 'custom_plugins']
datas += collect_data_files('openpyxl')
hiddenimports += collect_submodules('requests')
hiddenimports += collect_submodules('pandas')
hiddenimports += collect_submodules('numpy')
# 这些外置脚本会被导入控制台.exe 在运行时动态加载，必须随打包产物一并放到 dist 根目录。
datas += [
    ('增量导入.py', '.'),
    ('内部备注导入.py', '.'),
    ('定时拷贝任务.py', '.'),
    ('上传用户结构.py', '.'),
    ('同步意向学员到飞书.py', '.'),
    ('意向学员关系导入到飞书多维表格.py', '.'),
]


a = Analysis(
    ['导入控制台.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='导入控制台',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='导入控制台',
)

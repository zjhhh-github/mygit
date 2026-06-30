# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['PIL', 'PIL._tkinter_finder', 'PIL.Image', 'PIL.ImageTk', 'sqlite3']
hiddenimports += collect_submodules('PIL')


a = Analysis(
    ['D:\\桌面文件\\新建文件夹\\小工具\\联系人搜索\\搜索联系人_GUI.py'],
    pathex=[],
    binaries=[],
    datas=[('D:\\桌面文件\\新建文件夹\\小工具\\联系人搜索\\icon.ico', '.')],
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
    a.binaries,
    a.datas,
    [],
    name='联系人搜索',
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
    icon=['D:\\桌面文件\\新建文件夹\\小工具\\联系人搜索\\icon.ico'],
)

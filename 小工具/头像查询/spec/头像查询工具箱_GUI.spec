# -*- mode: python ; coding: utf-8 -*-
# 打包为独立 exe：GUI 程序，不保留控制台窗口（console=False），
# 运行日志显示在窗口内置的文本框里，不需要额外的黑框。
#
# 打包前请先安装依赖：
#   pip install pillow numpy pyinstaller

hiddenimports = ['sqlite3', 'PIL', 'PIL.Image', 'numpy']

a = Analysis(
    ['D:\\git\\mygit\\小工具\\头像查询\\头像查询工具箱_GUI.py'],
    pathex=['D:\\git\\mygit\\小工具\\头像查询'],
    binaries=[],
    datas=[],
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
    name='头像查询工具箱',
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
)

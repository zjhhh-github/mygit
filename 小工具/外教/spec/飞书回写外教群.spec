# -*- mode: python ; coding: utf-8 -*-
# 打包为独立 exe：保留控制台窗口（console=True），运行时能看到脚本打印的全部日志。

hiddenimports = ['sqlite3']

a = Analysis(
    ['D:\\git\\mygit\\小工具\\外教\\飞书回写外教群.py'],
    pathex=['D:\\git\\mygit\\小工具\\外教'],
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
    name='飞书回写外教群',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['D:\\git\\mygit\\小工具\\外教\\spec\\外教建群_icon.ico'],
)

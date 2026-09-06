# -*- mode: python ; coding: utf-8 -*-
# 打包为独立 exe：GUI 程序，不保留控制台窗口（console=False），
# 运行日志显示在窗口内置的日志文本框里，不需要额外的黑框。
#
# icon 只是给 exe 文件本身设置资源管理器里看到的图标；
# 窗口标题栏/任务栏运行时显示的图标是代码里 root.iconbitmap() 动态设置的，
# 需要把图标文件也放进 datas，运行时才能在 sys._MEIPASS 临时目录里找到它
# （具体读取逻辑见 外教群工具箱_GUI.py 里的 _图标文件路径() 函数）。

hiddenimports = ['sqlite3']

a = Analysis(
    ['D:\\git\\mygit\\小工具\\外教\\外教群工具箱_GUI.py'],
    pathex=['D:\\git\\mygit\\小工具\\外教'],
    binaries=[],
    datas=[('D:\\git\\mygit\\小工具\\外教\\spec\\外教建群_icon.ico', '.')],
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
    name='外教群工具箱',
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
    icon=['D:\\git\\mygit\\小工具\\外教\\spec\\外教建群_icon.ico'],
)

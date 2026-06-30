# -*- mode: python ; coding: utf-8 -*-

"""
PyInstaller Spec File for MySQL Database Manager
This spec file defines the configuration for packaging the MySQL database manager application.
"""

# Application metadata
APP_NAME = 'MySQL_DB_Manager'
VERSION = '1.0.0'

# Dependencies
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
hiddenimports = []
datas = []

# Collect necessary modules
hiddenimports += collect_submodules('pymysql')
hiddenimports += collect_submodules('pandas')
hiddenimports += collect_submodules('pyperclip')

# Collect data files
datas += collect_data_files('pandas')

block_cipher = None

a = Analysis(
    ['refactored_gui_db_manager.py'],  # 主入口文件
    pathex=['.'],  # 搜索路径
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=APP_NAME,  # 可执行文件名
    debug=False,  # 调试模式
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 设置为False以隐藏控制台窗口（GUI应用）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None  # 可以指定图标文件路径
)

# 为命令行版本创建另一个EXE
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME
)
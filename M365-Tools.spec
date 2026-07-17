# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['app.ui.main_window', 'app.ui.bulk', 'app.ui.dialogs', 'app.ui.form', 'app.ui.sidebar', 'app.ui.summary', 'app.ui.syncro_test', 'app.exchange', 'app.config_loader', 'app.account', 'app.auth', 'app.syncro', 'student_checker', 'settings_page', 'shared_sessions']
hiddenimports += collect_submodules('msal')
hiddenimports += collect_submodules('customtkinter')
hiddenimports += collect_submodules('dotenv')


a = Analysis(
    ['main.py'],
    pathex=['account_app', 'student_app'],
    binaries=[],
    datas=[('account_app\\config.json', 'account_app'), ('student_app\\check_students.ps1', 'student_app'), ('portable_paths.py', '.'), ('setup_wizard.py', '.'), ('settings_page.py', '.'), ('shared_sessions.py', '.')],
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
    name='M365-Tools',
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

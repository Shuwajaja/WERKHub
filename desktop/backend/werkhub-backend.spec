# -*- mode: python ; coding: utf-8 -*-
import sys
sys.setrecursionlimit(5000)  # werktools import graph is deep; default 1000 overflows
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('werktools')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['werkhub_backend.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # The dashboard backend is pure-stdlib. fastmcp/mcp are only for `hub serve`
        # (not the dashboard) and drag in a heavy data-science tree via the shared env.
        'fastmcp', 'mcp', 'pydantic', 'pydantic_core',
        'pandas', 'numpy', 'scipy', 'sklearn', 'scikit_learn', 'matplotlib',
        'dask', 'pyarrow', 'cloudpickle', 'IPython', 'jupyter', 'notebook',
        'PIL', 'tkinter', 'pytest',
    ],
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
    name='werkhub-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
src = Path(os.environ.get("YIJIANG_SRC", ".")).resolve()

datas = [
    (str(src / "static"), "static"),
    (str(src / "resources"), "resources"),
    (str(src / "config.example.json"), "."),
]
runtime_dir = src / "pyarmor_runtime_000000"
if runtime_dir.exists():
    datas.append((str(runtime_dir), "pyarmor_runtime_000000"))

hiddenimports = [
    "v2_selector",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
] + collect_submodules("uvicorn")
if (src / "pyarmor_runtime_000000").exists():
    hiddenimports.append("pyarmor_runtime_000000")

a = Analysis(
    [str(src / "desktop.py")],
    pathex=[str(src)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pandas", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Yijiang",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(src / "resources" / "yijiang.ico"),
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
    name="Yijiang",
)

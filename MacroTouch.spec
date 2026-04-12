# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import platform
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_ROOT = ROOT / "desktop-app"
IS_WINDOWS = platform.system() == "Windows"


def _tree(source: Path, prefix: str, excludes: list[str] | None = None):
    if not source.exists():
        return []
    return Tree(str(source), prefix=prefix, excludes=excludes or [], typecode="DATA")


datas = []
datas += _tree(APP_ROOT / "ui", "ui", excludes=["__pycache__", "*.pyc", "*.pyo"])
datas += _tree(APP_ROOT / "assets", "icons", excludes=["__pycache__", "*.pyc", "*.pyo", "AUTOR.docx"])
datas += _tree(APP_ROOT / "style", "style", excludes=["__pycache__", "*.pyc", "*.pyo"])
datas += _tree(APP_ROOT / "fonts", "fonts", excludes=["__pycache__", "*.pyc", "*.pyo"])
datas += _tree(APP_ROOT / "modules" / "fonts", "modules/fonts", excludes=["__pycache__", "*.pyc", "*.pyo"])
if (APP_ROOT / "modules" / "predecls.h").exists():
    datas.append((str(APP_ROOT / "modules" / "predecls.h"), "modules"))

binaries = []
arduino_candidates: list[Path] = []
if IS_WINDOWS:
    arduino_candidates.append(ROOT / "tools" / "arduino-cli.exe")
else:
    arduino_candidates.append(ROOT / "tools" / "arduino-cli")
    host_cli = shutil.which("arduino-cli")
    if host_cli:
        arduino_candidates.append(Path(host_cli))

for candidate in arduino_candidates:
    if candidate.is_file():
        binaries.append((str(candidate), "tools"))
        break


icon_path = APP_ROOT / "assets" / "MacroTouch.ico"


a = Analysis(
    ["desktop-app/main.py"],
    pathex=[str(APP_ROOT), str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
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
    name="MacroTouch",
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
    icon=[str(icon_path)] if icon_path.exists() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MacroTouch",
)

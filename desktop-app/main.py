# main.py - MacroTouch Desktop Application
#
# This is the main entry point for the MacroTouch desktop configuration application.
# The application provides a PyQt6-based GUI for configuring ESP32-S3 MacroTouch devices.
#
# Project Structure:
# - firmware/          : ESP32-S3 Arduino firmware
# - desktop-app/       : Python desktop application
#   - main.py         : Application entry point (this file)
#   - modules/        : Core application logic and utilities
#   - ui/            : Qt Designer UI files
#   - assets/        : Icons and images
# - images/           : Screenshots and device photos
# - example-config/   : Sample configuration files
#
# main.py
from __future__ import annotations

import sys
import os
import json
import hashlib
import threading
import time
import argparse
import math
from pathlib import Path
from typing import Any, Dict
import shlex
from urllib.parse import urlparse
import re

if os.name == "posix" and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
    # Suppress noisy non-fatal Qt Wayland textinput warnings.
    _rule = "qt.qpa.wayland.textinput.warning=false"
    _existing_rules = os.environ.get("QT_LOGGING_RULES", "").strip()
    if _existing_rules:
        _parts = [p.strip() for p in _existing_rules.split(";") if p.strip()]
        if _rule not in _parts:
            os.environ["QT_LOGGING_RULES"] = ";".join([*_parts, _rule])
    else:
        os.environ["QT_LOGGING_RULES"] = _rule

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QPushButton, QLineEdit, QComboBox,
    QInputDialog, QMessageBox, QSpinBox, QDoubleSpinBox, QGridLayout, QFileDialog, QTextEdit,
    QListWidget, QAbstractItemView, QDialog, QDialogButtonBox, QTabWidget, QGraphicsDropShadowEffect, QFrame,
    QGroupBox,
    QWidget, QHBoxLayout, QVBoxLayout, QSystemTrayIcon, QMenu, QSizePolicy, QScrollArea,
    QSwipeGesture, QCheckBox, QColorDialog
)

from PyQt6.QtGui import (
    QIcon,
    QPixmap,
    QGuiApplication,
    QTextCursor,
    QColor,
    QImage,
    QPainter,
    QPen,
    QLinearGradient,
    QPainterPath,
    QMovie,
    QRegion,
    QAction,
)
try:
    from PyQt6.QtSvg import QSvgRenderer
except Exception:
    QSvgRenderer = None
from PyQt6 import uic
from PyQt6.QtCore import (
    QThread, pyqtSignal, QTimer, Qt, QSize,
    QPropertyAnimation, QEasingCurve, QEvent, QPoint, QPointF, QRect, QRectF,
    QParallelAnimationGroup, QAbstractAnimation
)
from PIL import Image, ImageOps
import subprocess
import serial 
import shutil
try:
    import psutil  # type: ignore
except Exception:
    psutil = None

from modules.platform_env import IS_WINDOWS, IS_LINUX
from modules.arduino_utils import (
    arduino_cli_run,
    arduino_paths,
    build_arduino_env,
    copy_bundled_library,
    find_arduino_cli,
    lovyangfx_ready,
)
from modules.profile_schema import apply_profile_mode_defaults
from modules.smarthome import (
    DEFAULT_SMART_HOME_BASE_URL as SMART_HOME_BASE_URL,
    generate_smarthome_sketch,
    load_smart_home_state,
    normalize_smart_home_base_url,
    save_smart_home_state,
)

# Windows-only importy – na Linuxe sa ani nepokúsime
if IS_WINDOWS:
    try:
        import win32api
        import win32con
    except Exception as e:
        print(f"[Windows] pywin32 not available: {e}")
        win32api = None
        win32con = None
    try:
        from modules.media_controls import (
            WindowsMediaController,
            VK,
            minimize_active_window,
            toggle_maximize_active_window,
        )
    except Exception as e:
        print(f"[Windows] media_controls not available: {e}")
        WindowsMediaController = None
        VK = {}
        def minimize_active_window():
            print("[Windows] MinimizeWindow unavailable")

        def toggle_maximize_active_window():
            print("[Windows] MaximizeWindow unavailable")
else:
    win32api = None
    win32con = None
    WindowsMediaController = None
    VK = {}
    def minimize_active_window():
        print("[Linux] MinimizeWindow – neimplementované")

    def toggle_maximize_active_window():
        print("[Linux] MaximizeWindow – neimplementované")

from modules.paths import _appdata_dir, _state_file
from modules.helpers import open_path_smart, _debounced, c_ident_from_filename
from modules.media_backend import get_media_backend
from modules.encoder import ImprovedEncoderHandler
from modules.serial_service import SerialService
from modules.runtime_manager import RuntimeManager
from modules.profiles import ProfileManager
from modules.state_manager import StateManager
from modules.logging import get_logger
from modules.codegen import generate_main_ino
from modules.new_profile_dialog import NewProfileDialog
from modules.system_monitor_widget import SystemMonitorWidget
from modules.system_stats import SystemStatsProvider
from modules.action_constants import BTN_ACTIONS, BUTTON_ACTIONS, ACTION_ALIASES, KNOB_MODES
from modules.workers import UploaderWorker, TaskWorker
from modules.button_style_dialog import ButtonStyleDialog
from modules.mixer_widgets import MixerWidget, MixerSlider

# MediaStatusProvider – na Windows cez winsdk, na Linuxe cez playerctl
from modules.media_status import MediaStatusProvider

from modules.music_manager import MusicManager
from modules.music_state import MusicState
from modules.media_widget import MediaWidget 

ESP32_BOARD_URL = "https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json"
LOVYANGFX_GIT_URL = "https://github.com/lovyan03/LovyanGFX.git"

METRIC_WIDGET_KEY_ALIASES: dict[str, str] = {
    "CPU": "CPU",
    "CPU_PERCENT": "CPU",
    "RAM": "RAM",
    "RAM_PERCENT": "RAM",
    "GPU": "GPU",
    "GPU_PERCENT": "GPU",
    "GPU_TEMP": "GPU_TEMP",
    "GPU_TEMPERATURE": "GPU_TEMP",
    "GPUTEMP": "GPU_TEMP",
    "TEMP": "GPU_TEMP",
    "FPS": "FPS",
    "NET": "NET",
    "NETWORK": "NET",
    "NET_MB_S": "NET",
    "DISK": "DISK",
    "DISK_MB_S": "DISK",
    "IO": "DISK",
    "CPU_GHZ": "CPU_GHZ",
    "CPUGHZ": "CPU_GHZ",
}

METRIC_WIDGET_DEFAULT_LABELS: dict[str, str] = {
    "CPU": "CPU load",
    "RAM": "RAM usage",
    "GPU": "GPU load",
    "GPU_TEMP": "GPU temp",
    "FPS": "FPS",
    "NET": "Network",
    "DISK": "Disk I/O",
    "CPU_GHZ": "CPU freq",
}

HARDWARE_INPUT_ACTION_LABELS: dict[str, str] = {
    "None": "Not assigned",
    "PlayMusic": "Play / Pause",
    "Mute": "Mute output",
    "Mute Mic": "Mute mic",
    "Unmute Mic": "Unmute mic",
    "Next": "Next track",
    "Previous": "Previous track",
    "NextProfile": "Next profile",
    "PreviousProfile": "Previous profile",
    "Open URL": "Open URL",
    "Spotify Playlist": "Spotify playlist",
}

HARDWARE_INPUT_KNOB_LABELS: dict[str, str] = {
    "None": "Disabled",
    "Volume": "Volume",
    "Brightness": "Brightness",
}

PROFILE_MODE_INFO: dict[str, dict[str, str]] = {
    "monitor": {
        "badge": "MONITOR",
        "title": "Live system stats",
        "description": "This profile uses the preview for CPU, RAM and GPU telemetry instead of editable grid buttons.",
        "hint": "Hardware inputs below still work, so you can keep profile switching or quick actions available.",
    },
    "media": {
        "badge": "MEDIA",
        "title": "Now playing screen",
        "description": "This profile is driven by live playback state, transport controls and album artwork instead of per-button settings.",
        "hint": "Use Button A/B or the encoder below for playback shortcuts that should stay available everywhere.",
    },
    "mixer": {
        "badge": "MIXER",
        "title": "Live audio mixer",
        "description": "This profile mirrors master, mic and app volumes, so the right panel intentionally focuses on shared hardware controls.",
        "hint": "The encoder can still control volume or brightness, and Button A/B can stay mapped to quick actions.",
    },
}

# vyčistenie QT env premenných
for var in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_API"):
    os.environ.pop(var, None)


class GifPerfTestDialog(QDialog):
    """Simple GIF stress test with live FPS/CPU/drop metrics."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._app = parent if isinstance(parent, QWidget) else None
        self.setObjectName("GifPerfTestDialog")
        self.setWindowTitle("GIF Performance Test")
        self.resize(920, 680)
        self.setModal(False)
        try:
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        except Exception:
            pass
        self.setStyleSheet(
            """
QDialog#GifPerfTestDialog {
    background-color: #0B1220;
    color: #E5E7EB;
}
QDialog#GifPerfTestDialog QLabel {
    color: #E5E7EB;
}
QDialog#GifPerfTestDialog QLabel#lblPerfStats {
    color: #D1D5DB;
    background-color: rgba(15, 23, 42, 0.9);
    border: 1px solid rgba(148, 163, 184, 0.24);
    border-radius: 10px;
    padding: 8px 10px;
}
QDialog#GifPerfTestDialog QLabel#lblEspStats {
    color: #BFDBFE;
    background-color: rgba(15, 23, 42, 0.9);
    border: 1px solid rgba(59, 130, 246, 0.38);
    border-radius: 10px;
    padding: 8px 10px;
}
QDialog#GifPerfTestDialog QLineEdit,
QDialog#GifPerfTestDialog QSpinBox {
    background-color: rgba(17, 24, 39, 0.96);
    border: 1px solid rgba(148, 163, 184, 0.28);
    border-radius: 8px;
    color: #F3F4F6;
    padding: 4px 10px;
}
QDialog#GifPerfTestDialog QPushButton {
    background-color: rgba(17, 24, 39, 0.96);
    border: 1px solid rgba(148, 163, 184, 0.24);
    border-radius: 12px;
    color: #F3F4F6;
    padding: 7px 14px;
}
QDialog#GifPerfTestDialog QPushButton:hover {
    background-color: rgba(30, 41, 59, 0.98);
}
QDialog#GifPerfTestDialog QScrollArea {
    background-color: rgba(9, 14, 25, 0.92);
    border: 1px solid rgba(148, 163, 184, 0.24);
    border-radius: 12px;
}
QDialog#GifPerfTestDialog QListWidget {
    background-color: rgba(9, 14, 25, 0.92);
    border: 1px solid rgba(148, 163, 184, 0.24);
    border-radius: 10px;
    color: #E5E7EB;
    padding: 4px;
}
QDialog#GifPerfTestDialog QListWidget::item:selected {
    background-color: rgba(30, 58, 138, 0.7);
    color: #EFF6FF;
}
"""
        )

        self._movies: list[QMovie] = []
        self._frame_prev: dict[int, int] = {}
        self._frame_count_hint_map: dict[int, int] = {}
        self._expected_fps_per_movie: list[float] = []
        self._source_fps_min = 20.0
        self._source_fps_avg = 20.0
        self._active_gif_paths: list[str] = []
        self._frame_events = 0
        self._dropped_jump = 0
        self._running = False
        self._start_ts = 0.0
        self._target_fps = 20.0
        self._effective_fps = 20.0
        self._source_fps = 20.0
        self._duration_s = 20
        self._gif_library_dir = _appdata_dir() / "gif_library"
        self._gif_library_dir.mkdir(parents=True, exist_ok=True)
        self._proc = psutil.Process(os.getpid()) if psutil is not None else None
        if self._proc is not None:
            try:
                self._proc.cpu_percent(None)
            except Exception:
                self._proc = None

        self._stats_timer = QTimer(self)
        self._stats_timer.setSingleShot(False)
        self._stats_timer.setInterval(500)
        self._stats_timer.timeout.connect(self._update_stats)

        self._auto_stop_timer = QTimer(self)
        self._auto_stop_timer.setSingleShot(True)
        self._auto_stop_timer.timeout.connect(self._stop_test)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(QLabel("GIF file (fallback)", self))
        self.lineGifPath = QLineEdit(self)
        self.lineGifPath.setPlaceholderText("Choose a .gif file")
        path_row.addWidget(self.lineGifPath, 1)
        self.btnBrowseGif = QPushButton("Browse", self)
        self.btnBrowseGif.clicked.connect(self._browse_gif)
        path_row.addWidget(self.btnBrowseGif)
        root.addLayout(path_row)

        lib_btn_row = QHBoxLayout()
        lib_btn_row.setSpacing(8)
        self.btnImportGifs = QPushButton("Import GIFs", self)
        self.btnImportGifs.setToolTip("Copy selected GIF files into app library.")
        self.btnImportGifs.clicked.connect(self._import_gifs_to_library)
        self.btnRemoveGif = QPushButton("Remove selected", self)
        self.btnRemoveGif.clicked.connect(self._remove_selected_library_gifs)
        self.btnRefreshGifList = QPushButton("Refresh", self)
        self.btnRefreshGifList.clicked.connect(self._refresh_gif_library)
        self.btnOpenGifFolder = QPushButton("Open folder", self)
        self.btnOpenGifFolder.clicked.connect(self._open_gif_library_folder)
        lib_btn_row.addWidget(self.btnImportGifs)
        lib_btn_row.addWidget(self.btnRemoveGif)
        lib_btn_row.addWidget(self.btnRefreshGifList)
        lib_btn_row.addWidget(self.btnOpenGifFolder)
        lib_btn_row.addStretch(1)
        root.addLayout(lib_btn_row)

        root.addWidget(QLabel("GIF library (multi-select used by test)", self))
        self.listGifLibrary = QListWidget(self)
        self.listGifLibrary.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.listGifLibrary.setAlternatingRowColors(False)
        self.listGifLibrary.itemSelectionChanged.connect(self._on_library_selection_changed)
        self.listGifLibrary.setMinimumHeight(110)
        root.addWidget(self.listGifLibrary)

        opts_row = QHBoxLayout()
        opts_row.setSpacing(8)
        self.spinGifCount = QSpinBox(self)
        self.spinGifCount.setRange(1, 16)
        self.spinGifCount.setValue(6)
        self.spinGifSize = QSpinBox(self)
        self.spinGifSize.setRange(32, 320)
        self.spinGifSize.setValue(128)
        self.spinGifFps = QSpinBox(self)
        self.spinGifFps.setRange(1, 60)
        self.spinGifFps.setValue(20)
        self.spinGifDuration = QSpinBox(self)
        self.spinGifDuration.setRange(3, 300)
        self.spinGifDuration.setValue(20)
        self.spinGifDuration.setSuffix(" s")

        opts_row.addWidget(QLabel("Count", self))
        opts_row.addWidget(self.spinGifCount)
        opts_row.addWidget(QLabel("Size", self))
        opts_row.addWidget(self.spinGifSize)
        opts_row.addWidget(QLabel("Target FPS", self))
        opts_row.addWidget(self.spinGifFps)
        opts_row.addWidget(QLabel("Duration", self))
        opts_row.addWidget(self.spinGifDuration)
        opts_row.addStretch(1)
        root.addLayout(opts_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btnStartTest = QPushButton("Start test", self)
        self.btnStartTest.clicked.connect(self._start_test)
        self.btnStopTest = QPushButton("Stop", self)
        self.btnStopTest.clicked.connect(self._stop_test)
        self.btnStopTest.setEnabled(False)
        self.btnClose = QPushButton("Close", self)
        self.btnClose.clicked.connect(self.close)
        btn_row.addWidget(self.btnStartTest)
        btn_row.addWidget(self.btnStopTest)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btnClose)
        root.addLayout(btn_row)

        esp_row = QHBoxLayout()
        esp_row.setSpacing(8)
        self.btnStartEspTest = QPushButton("Start on ESP", self)
        self.btnStartEspTest.clicked.connect(self._start_test_on_esp)
        self.btnStopEspTest = QPushButton("Stop ESP", self)
        self.btnStopEspTest.clicked.connect(self._stop_test_on_esp)
        esp_row.addWidget(self.btnStartEspTest)
        esp_row.addWidget(self.btnStopEspTest)
        esp_row.addStretch(1)
        root.addLayout(esp_row)

        self.lblPerfStats = QLabel(
            "Idle. Choose GIF and start test.",
            self,
        )
        self.lblPerfStats.setObjectName("lblPerfStats")
        self.lblPerfStats.setWordWrap(True)
        self.lblPerfStats.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.lblPerfStats)

        self.lblEspStats = QLabel(
            "ESP benchmark idle. This runs synthetic GIF-like render load on device "
            "(not real GIF decode). Upload firmware with BENCH support, then click 'Start on ESP'.",
            self,
        )
        self.lblEspStats.setObjectName("lblEspStats")
        self.lblEspStats.setWordWrap(True)
        self.lblEspStats.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.lblEspStats)

        self.scrollArea = QScrollArea(self)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setFrameShape(QFrame.Shape.NoFrame)
        self.canvas = QWidget(self.scrollArea)
        self.canvas.setStyleSheet(
            "background-color: rgba(8, 12, 22, 0.9);"
            "border-radius: 10px;"
        )
        self.canvasGrid = QGridLayout(self.canvas)
        self.canvasGrid.setContentsMargins(8, 8, 8, 8)
        self.canvasGrid.setHorizontalSpacing(10)
        self.canvasGrid.setVerticalSpacing(10)
        self.scrollArea.setWidget(self.canvas)
        try:
            self.scrollArea.viewport().setStyleSheet("background-color: rgba(8, 12, 22, 0.9);")
        except Exception:
            pass
        root.addWidget(self.scrollArea, 1)
        self._refresh_gif_library()

    def _browse_gif(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose GIF",
            self.lineGifPath.text().strip(),
            "GIF files (*.gif);;All files (*.*)",
        )
        if file_path:
            self.lineGifPath.setText(file_path)
            self.listGifLibrary.clearSelection()

    def _scan_library_gifs(self) -> list[Path]:
        try:
            files = [p for p in self._gif_library_dir.iterdir() if p.is_file() and p.suffix.lower() == ".gif"]
        except Exception:
            return []
        files.sort(key=lambda p: p.name.lower())
        return files

    def _refresh_gif_library(self) -> None:
        selected_before = set(self._selected_library_paths())
        self.listGifLibrary.clear()
        items = self._scan_library_gifs()
        for p in items:
            self.listGifLibrary.addItem(p.name)
        if not items:
            return
        any_selected = False
        for i in range(self.listGifLibrary.count()):
            it = self.listGifLibrary.item(i)
            if it is None:
                continue
            full = str((self._gif_library_dir / it.text()).resolve())
            if full in selected_before:
                it.setSelected(True)
                any_selected = True
        if not any_selected:
            try:
                self.listGifLibrary.item(0).setSelected(True)
            except Exception:
                pass

    def _selected_library_paths(self) -> list[str]:
        out: list[str] = []
        for it in self.listGifLibrary.selectedItems():
            p = (self._gif_library_dir / it.text()).resolve()
            if p.is_file():
                out.append(str(p))
        return out

    def _open_gif_library_folder(self) -> None:
        try:
            open_path_smart(self._gif_library_dir)
        except Exception:
            pass

    def _import_gifs_to_library(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import GIFs",
            str(self._gif_library_dir),
            "GIF files (*.gif);;All files (*.*)",
        )
        if not paths:
            return
        imported = 0
        skipped = 0
        self._gif_library_dir.mkdir(parents=True, exist_ok=True)
        for raw in paths:
            src = Path(raw)
            if not src.is_file() or src.suffix.lower() != ".gif":
                skipped += 1
                continue
            name = src.name
            stem = src.stem
            suffix = src.suffix
            dst = self._gif_library_dir / name
            idx = 2
            while dst.exists():
                dst = self._gif_library_dir / f"{stem}_{idx}{suffix}"
                idx += 1
            try:
                shutil.copy2(src, dst)
                imported += 1
            except Exception:
                skipped += 1
        self._refresh_gif_library()
        QMessageBox.information(
            self,
            "GIF import",
            f"Imported: {imported}\nSkipped: {skipped}\nLibrary: {self._gif_library_dir}",
        )

    def _remove_selected_library_gifs(self) -> None:
        selected = self._selected_library_paths()
        if not selected:
            QMessageBox.information(self, "GIF library", "Select GIFs to remove first.")
            return
        reply = QMessageBox.question(
            self,
            "Remove GIFs",
            f"Remove {len(selected)} selected GIF file(s) from library?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        removed = 0
        for fp in selected:
            try:
                Path(fp).unlink(missing_ok=True)
                removed += 1
            except Exception:
                pass
        self._refresh_gif_library()
        if removed > 0:
            self.lineGifPath.clear()

    def _on_library_selection_changed(self) -> None:
        selected = self._selected_library_paths()
        if selected:
            self.lineGifPath.setText(selected[0])

    def _serial_ready_for_esp(self) -> bool:
        app = self._app
        if app is None:
            return False
        serial_monitor = getattr(app, "serial_monitor", None)
        if serial_monitor is None:
            return False
        ser = getattr(serial_monitor, "ser", None)
        return bool(ser is not None and getattr(ser, "is_open", False))

    def _send_esp_bench_line(self, line: str) -> bool:
        app = self._app
        if app is None:
            return False
        serial_monitor = getattr(app, "serial_monitor", None)
        if serial_monitor is None:
            return False
        try:
            serial_monitor.send_line(line)
            return True
        except Exception:
            return False

    def _start_test_on_esp(self) -> None:
        if not self._serial_ready_for_esp():
            QMessageBox.warning(self, "ESP benchmark", "ESP is not connected over serial.")
            return
        count = int(self.spinGifCount.value())
        side = int(self.spinGifSize.value())
        fps = int(self.spinGifFps.value())
        duration_s = int(self.spinGifDuration.value())
        cmd = f"BENCH:START;N={count};SIZE={side};FPS={fps};DUR={duration_s};FAST=1"
        if self._send_esp_bench_line(cmd):
            self.lblEspStats.setText(f"ESP command sent: {cmd}\nWaiting for BENCH:STARTED/STAT...")
        else:
            QMessageBox.warning(self, "ESP benchmark", "Failed to send command to ESP.")

    def _stop_test_on_esp(self) -> None:
        if not self._serial_ready_for_esp():
            QMessageBox.warning(self, "ESP benchmark", "ESP is not connected over serial.")
            return
        if self._send_esp_bench_line("BENCH:STOP"):
            self.lblEspStats.setText("ESP command sent: BENCH:STOP")
        else:
            QMessageBox.warning(self, "ESP benchmark", "Failed to send command to ESP.")

    def handle_esp_bench_line(self, line: str) -> None:
        if not line:
            return
        text = line.strip()
        if not text.startswith("BENCH:"):
            return
        payload = text[6:]
        parts = [p for p in payload.split(";") if p]
        if not parts:
            self.lblEspStats.setText(text)
            return
        kind = parts[0].strip().upper()
        kv: dict[str, str] = {}
        for part in parts[1:]:
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            kv[k.strip().upper()] = v.strip()

        if kind == "STARTED":
            self.lblEspStats.setText(
                "ESP BENCH STARTED | "
                f"N={kv.get('N', '?')} SIZE={kv.get('SIZE', '?')} "
                f"FPS={kv.get('FPS', '?')} DUR={kv.get('DUR', '?')}s "
                f"FAST={kv.get('FAST', '?')}"
            )
            return
        if kind == "STAT":
            self.lblEspStats.setText(
                "ESP BENCH STAT | "
                f"elapsed={kv.get('EL', '?')}ms "
                f"fps={kv.get('FPS', '?')} "
                f"frames={kv.get('FR', '?')}/{kv.get('EXP', '?')} "
                f"drop={kv.get('DROP', '?')} "
                f"last_us={kv.get('LUS', '?')} avg_us={kv.get('AUS', '?')} "
                f"fast={kv.get('FAST', '?')}"
            )
            return
        if kind == "RESULT":
            self.lblEspStats.setText(
                "ESP BENCH RESULT | "
                f"state={kv.get('STATE', '?')} "
                f"elapsed={kv.get('EL', '?')}ms "
                f"fps={kv.get('FPS', '?')} "
                f"frames={kv.get('FR', '?')}/{kv.get('EXP', '?')} "
                f"drop={kv.get('DROP', '?')} "
                f"avg_us={kv.get('AUS', '?')} "
                f"N={kv.get('N', '?')} SIZE={kv.get('SIZE', '?')} target={kv.get('TFPS', '?')} "
                f"fast={kv.get('FAST', '?')}"
            )
            return
        self.lblEspStats.setText(text)

    def _gif_source_fps(self, gif_path: str) -> tuple[float, int]:
        durations_ms: list[float] = []
        frame_count = 0
        try:
            with Image.open(gif_path) as img:
                frame_count = int(getattr(img, "n_frames", 0) or 0)
                sample_frames = frame_count if frame_count > 0 else 1
                sample_frames = max(1, min(180, sample_frames))
                for idx in range(sample_frames):
                    try:
                        img.seek(idx)
                    except Exception:
                        break
                    dur = img.info.get("duration", 0)
                    if not isinstance(dur, (int, float)) or dur <= 1:
                        dur = 100
                    durations_ms.append(float(dur))
        except Exception:
            return 10.0, 0

        if not durations_ms:
            durations_ms = [100.0]
        avg_ms = max(8.0, sum(durations_ms) / len(durations_ms))
        src_fps = 1000.0 / avg_ms
        if frame_count <= 0:
            frame_count = len(durations_ms)
        return src_fps, frame_count

    def _clear_canvas_movies(self) -> None:
        for movie in self._movies:
            try:
                movie.stop()
            except Exception:
                pass
            movie.deleteLater()
        self._movies.clear()
        self._frame_prev.clear()
        self._frame_count_hint_map.clear()
        self._expected_fps_per_movie.clear()
        self._active_gif_paths.clear()
        while self.canvasGrid.count():
            item = self.canvasGrid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _start_test(self) -> None:
        selected_gifs = self._selected_library_paths()
        fallback_path = self.lineGifPath.text().strip()
        if selected_gifs:
            gif_sources = selected_gifs
        elif fallback_path and os.path.isfile(fallback_path):
            gif_sources = [fallback_path]
        else:
            QMessageBox.warning(self, "GIF test", "Import/select GIFs from library or choose a valid fallback GIF file.")
            return

        self._stop_test()
        self._clear_canvas_movies()

        count = int(self.spinGifCount.value())
        side = int(self.spinGifSize.value())
        target_fps = float(self.spinGifFps.value())
        duration_s = int(self.spinGifDuration.value())

        gif_info: dict[str, tuple[float, int]] = {}
        normalized_sources: list[str] = []
        for src in gif_sources:
            src_path = str(Path(src).expanduser().resolve())
            if not os.path.isfile(src_path):
                continue
            normalized_sources.append(src_path)
            if src_path not in gif_info:
                gif_info[src_path] = self._gif_source_fps(src_path)
        if not normalized_sources:
            QMessageBox.warning(self, "GIF test", "No valid GIF files found for test.")
            return

        cols = max(1, int(math.ceil(math.sqrt(count))))
        for idx in range(count):
            gif_path = normalized_sources[idx % len(normalized_sources)]
            src_fps, frame_count_hint = gif_info.get(gif_path, (10.0, 0))
            effective_fps = min(target_fps, src_fps) if src_fps > 0 else target_fps
            speed_pct = int(round((effective_fps / max(1e-3, src_fps)) * 100.0))
            speed_pct = max(5, min(400, speed_pct))

            lbl = QLabel(self.canvas)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedSize(side, side)
            lbl.setStyleSheet(
                "QLabel {"
                "background-color: #0F141D;"
                "border: 1px solid #2B3442;"
                "border-radius: 10px;"
                "}"
            )
            movie = QMovie(gif_path)
            if not movie.isValid():
                lbl.deleteLater()
                self._clear_canvas_movies()
                QMessageBox.warning(self, "GIF test", f"Selected GIF cannot be played by Qt:\n{gif_path}")
                return
            movie.setParent(lbl)
            movie.setCacheMode(QMovie.CacheMode.CacheAll)
            movie.setScaledSize(QSize(side, side))
            movie.setSpeed(speed_pct)
            movie.frameChanged.connect(lambda frame, i=idx: self._on_frame_changed(i, frame))
            lbl.setMovie(movie)
            self.canvasGrid.addWidget(lbl, idx // cols, idx % cols)
            self._movies.append(movie)
            self._frame_count_hint_map[idx] = frame_count_hint
            self._expected_fps_per_movie.append(effective_fps)
            self._active_gif_paths.append(gif_path)

        self._frame_events = 0
        self._dropped_jump = 0
        self._frame_prev = {idx: -1 for idx in range(count)}
        src_values = [max(0.1, gif_info[p][0]) for p in normalized_sources if p in gif_info]
        self._source_fps_min = min(src_values) if src_values else target_fps
        self._source_fps_avg = (sum(src_values) / len(src_values)) if src_values else target_fps
        self._target_fps = target_fps
        if self._expected_fps_per_movie:
            self._effective_fps = sum(self._expected_fps_per_movie) / len(self._expected_fps_per_movie)
        else:
            self._effective_fps = target_fps
        self._source_fps = self._source_fps_avg
        self._duration_s = duration_s
        self._start_ts = time.monotonic()
        self._running = True

        if self._proc is not None:
            try:
                self._proc.cpu_percent(None)
            except Exception:
                self._proc = None

        for movie in self._movies:
            movie.start()

        self.btnStartTest.setEnabled(False)
        self.btnStopTest.setEnabled(True)
        self._stats_timer.start()
        self._auto_stop_timer.start(max(3000, duration_s * 1000))
        self._update_stats()

    def _stop_test(self) -> None:
        if not self._running and not self._movies:
            return
        self._running = False
        self._stats_timer.stop()
        self._auto_stop_timer.stop()
        for movie in self._movies:
            try:
                movie.stop()
            except Exception:
                pass
        self.btnStartTest.setEnabled(True)
        self.btnStopTest.setEnabled(False)
        self._update_stats()

    def _on_frame_changed(self, idx: int, frame_number: int) -> None:
        self._frame_events += 1
        prev = int(self._frame_prev.get(idx, -1))
        if prev >= 0:
            delta = frame_number - prev
            if delta < 0:
                frame_hint = int(self._frame_count_hint_map.get(idx, 0))
                if frame_hint > 0:
                    delta = (frame_hint - prev) + frame_number
                else:
                    delta = 1
            if delta > 1:
                self._dropped_jump += (delta - 1)
        self._frame_prev[idx] = int(frame_number)

    def _update_stats(self) -> None:
        if self._start_ts <= 0:
            return
        elapsed = max(1e-3, time.monotonic() - self._start_ts)
        gif_count = max(1, len(self._movies))
        fps_total = self._frame_events / elapsed
        fps_per_gif = fps_total / gif_count

        expected_total = elapsed * max(0.1, sum(self._expected_fps_per_movie) if self._expected_fps_per_movie else (self._effective_fps * gif_count))
        dropped_est = max(0, int(round(expected_total - self._frame_events)))
        dropped = max(int(self._dropped_jump), dropped_est)
        drop_pct = (100.0 * dropped / expected_total) if expected_total > 0.0 else 0.0

        cpu_text = "n/a"
        if self._proc is not None:
            try:
                cpu_text = f"{self._proc.cpu_percent(None):.1f}%"
            except Exception:
                cpu_text = "n/a"

        state = "RUNNING" if self._running else "STOPPED"
        unique_sources = len(set(self._active_gif_paths)) if self._active_gif_paths else max(1, len(self._selected_library_paths()))
        self.lblPerfStats.setText(
            f"{state} | elapsed: {elapsed:.1f}s / {self._duration_s}s | gifs: {gif_count} | "
            f"target: {self._target_fps:.1f} fps | effective: {self._effective_fps:.1f} fps "
            f"(source avg {self._source_fps_avg:.1f}, min {self._source_fps_min:.1f}) | "
            f"sources: {unique_sources} | actual: {fps_per_gif:.1f} fps/gif "
            f"({fps_total:.1f} total) | CPU(app): {cpu_text} | dropped: {dropped} ({drop_pct:.1f}%)"
        )

    def closeEvent(self, event) -> None:
        self._stop_test()
        self._clear_canvas_movies()
        super().closeEvent(event)


class MacroTouchApp(QMainWindow):
    serialLine = pyqtSignal(str)
    serialConnected = pyqtSignal(str)
    serialDisconnected = pyqtSignal(str)
    weatherPreviewRefresh = pyqtSignal()
    GRAY_LABELS = (
        "chip1",
        "lblRows",
        "lblCols",
        "chipGrid",
        "lblName",
        "lblIcon",
        "lblAction",
        "lblPath",
        "lblBtnA",
        "lblBtnB",
        "lblPot",
        "lblSmartSSID",
        "lblSmartPass",
        "lblSmartBaseUrl",
        "lblSmartRelay1",
        "lblSmartRelay2",
        "lblSmartRelay3",
        "lblSmartRelay4",
        "lblScrEnable",
        "lblScrIdle",
        "lblScrTimeSize",
        "lblScrTimeFont",
        "lblScrTimeColor",
        "lblScrBgColor",
        "lblScrBgImage",
        "lblScrLabelText",
        "lblScrLabelSize",
        "lblScrLabelColor",
        "lblGridBgColor",
        "lblGridBgImage",
        "lblBtnBgColor",
        "lblBtnFgColor",
        "lblIconTransparency",
        "lblDisplayHint",
        "lblTheme",
        "lblAccent",
        "lblWallpaperEnable",
        "lblWallpaperPath",
        "lblWallpaperDim",
    )

    # ---------- pomocné metódy pre grid/ikony ----------

    def _file_sig(self, path: str) -> tuple[int, int]:
        """Cheap file signature for cache invalidation (mtime_ns, size)."""
        try:
            st = os.stat(path)
            return int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))), int(st.st_size)
        except Exception:
            return (0, 0)

    def _write_text_if_changed(self, path: Path, content: str, encoding: str = "utf-8") -> bool:
        try:
            if path.is_file():
                try:
                    if path.read_text(encoding=encoding) == content:
                        return False
                except Exception:
                    pass
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding=encoding)
            return True
        except Exception:
            raise

    def _build_meta_path(self, build_path: str) -> Path:
        return Path(build_path) / ".compile-meta.json"

    def _load_build_meta(self, build_path: str) -> dict[str, Any] | None:
        meta_path = self._build_meta_path(build_path)
        if not meta_path.is_file():
            return None
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _save_build_meta(self, build_path: str, meta: dict[str, Any]) -> None:
        meta_path = self._build_meta_path(build_path)
        try:
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[Upload] Failed to persist compile cache metadata: {e}")

    def _build_artifacts_ready(self, build_path: str) -> bool:
        root = Path(build_path)
        if not root.is_dir():
            return False
        return any(root.glob("*.bin"))

    def _compute_compile_signature(
        self,
        sketch_folder: str,
        fqbn: str,
        compile_props: list[str],
    ) -> dict[str, Any]:
        root = Path(sketch_folder).resolve()
        digest = hashlib.sha1()
        digest.update(f"fqbn={fqbn}\n".encode("utf-8", errors="ignore"))
        for prop in compile_props:
            digest.update(f"prop={prop}\n".encode("utf-8", errors="ignore"))

        entries: list[tuple[str, int, int]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in {"_build", "_build_cache"}]
            base = Path(dirpath)
            for filename in filenames:
                file_path = base / filename
                rel = file_path.relative_to(root).as_posix()
                mtime_ns, size = self._file_sig(str(file_path))
                entries.append((rel, mtime_ns, size))

        entries.sort(key=lambda x: x[0])
        file_count = 0
        for rel, mtime_ns, size in entries:
            digest.update(rel.encode("utf-8", errors="ignore"))
            digest.update(b"\0")
            digest.update(str(mtime_ns).encode("ascii", errors="ignore"))
            digest.update(b":")
            digest.update(str(size).encode("ascii", errors="ignore"))
            digest.update(b"\n")
            file_count += 1

        return {
            "v": 1,
            "digest": digest.hexdigest(),
            "file_count": file_count,
            "fqbn": fqbn,
            "compile_props": list(compile_props),
        }

    def _cache_put(self, cache: dict, key: Any, value: Any, limit: int) -> None:
        if key in cache:
            cache[key] = value
            return
        if len(cache) >= max(1, int(limit)):
            cache.clear()
        cache[key] = value

    def _icon_image_is_opaque(self, img: QImage) -> bool:
        """Rýchly odhad či je obrázok úplne nepriehľadný (bez alfa)."""
        if img.isNull():
            return False
        if not img.hasAlphaChannel():
            return True
        w = img.width()
        h = img.height()
        if w <= 0 or h <= 0:
            return False
        step = max(1, int(min(w, h) / 32))
        for y in range(0, h, step):
            for x in range(0, w, step):
                if QColor(img.pixel(x, y)).alpha() < 250:
                    return False
        return True

    def _fit_icon_size(self, img_path: str, cell_w: int, cell_h: int, margin: int = 8) -> QSize:
        sig = self._file_sig(img_path)
        cache_key = (img_path, sig[0], sig[1], int(cell_w), int(cell_h), int(margin))
        cached = self._icon_fit_cache.get(cache_key) if hasattr(self, "_icon_fit_cache") else None
        if isinstance(cached, QSize):
            return QSize(cached.width(), cached.height())
        try:
            pm = QPixmap(img_path)
            if pm.isNull():
                size = QSize(max(16, cell_w - margin), max(16, cell_h - margin))
                self._cache_put(self._icon_fit_cache, cache_key, size, self._icon_fit_cache_limit)
                return size
            avail_w = max(16, cell_w - margin)
            avail_h = max(16, cell_h - margin)
            scaled = pm.scaled(
                avail_w,
                avail_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            size = QSize(scaled.width(), scaled.height())
            self._cache_put(self._icon_fit_cache, cache_key, size, self._icon_fit_cache_limit)
            return size
        except Exception:
            size = QSize(max(16, cell_w - margin), max(16, cell_h - margin))
            self._cache_put(self._icon_fit_cache, cache_key, size, self._icon_fit_cache_limit)
            return size

    def _make_icon(
        self,
        img_path: str,
        target_size: QSize,
        widget: QWidget | None = None,
        cover: bool = False,
    ) -> QIcon:
        """Vytvorí HiDPI-friendly ikonu bez rozmazania pri frakčnom DPI."""
        if not img_path:
            return QIcon()
        pm = QPixmap(img_path)
        if pm.isNull():
            return QIcon(img_path)
        try:
            dpr = float(widget.devicePixelRatioF()) if widget is not None else float(self.devicePixelRatioF())
        except Exception:
            dpr = 1.0
        target_w = max(1, int(round(target_size.width() * dpr)))
        target_h = max(1, int(round(target_size.height() * dpr)))
        sig = self._file_sig(img_path)
        cache_key = (
            img_path,
            sig[0],
            sig[1],
            target_w,
            target_h,
            bool(cover),
            int(round(dpr * 100)),
        )
        cached = self._icon_render_cache.get(cache_key) if hasattr(self, "_icon_render_cache") else None
        if isinstance(cached, QIcon):
            return cached
        img = pm.toImage()
        opaque = self._icon_image_is_opaque(img)
        mode = Qt.AspectRatioMode.KeepAspectRatioByExpanding if (cover or opaque) else Qt.AspectRatioMode.KeepAspectRatio
        scaled = pm.scaled(
            target_w,
            target_h,
            mode,
            Qt.TransformationMode.SmoothTransformation,
        )
        canvas = QPixmap(target_w, target_h)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        radius = max(6, int(round(min(target_w, target_h) * 0.20)))
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, target_w, target_h), radius, radius)
        painter.setClipPath(path)
        x = (canvas.width() - scaled.width()) // 2
        y = (canvas.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
        canvas.setDevicePixelRatio(dpr)
        icon = QIcon(canvas)
        self._cache_put(self._icon_render_cache, cache_key, icon, self._icon_render_cache_limit)
        return icon

    def _make_icon_plate(
        self,
        img_path: str,
        target_size: QSize,
        plate_color: str,
        widget: QWidget | None = None,
    ) -> QIcon:
        """Ikona na pevnom štvorcovom podklade (pre ikonové tlačidlá v gride)."""
        if not img_path:
            return QIcon()
        pm = QPixmap(img_path)
        if pm.isNull():
            return QIcon(img_path)
        try:
            dpr = float(widget.devicePixelRatioF()) if widget is not None else float(self.devicePixelRatioF())
        except Exception:
            dpr = 1.0

        s = max(1, int(max(target_size.width(), target_size.height())))
        pad = max(2, int(round(s * 0.06)))
        inner = max(2, s - 2 * pad)
        radius = max(6, int(round(s * 0.16)))
        color = self._normalize_hex_color(str(plate_color or "#000000"), "#000000")
        sig = self._file_sig(img_path)
        cache_key = (
            "plate",
            img_path,
            sig[0],
            sig[1],
            s,
            color,
            int(round(dpr * 100)),
        )
        cached = self._icon_render_cache.get(cache_key) if hasattr(self, "_icon_render_cache") else None
        if isinstance(cached, QIcon):
            return cached

        plate_px = max(1, int(round(s * dpr)))
        inner_px = max(1, int(round(inner * dpr)))

        plate = QPixmap(plate_px, plate_px)
        plate.fill(Qt.GlobalColor.transparent)

        scaled = pm.scaled(
            inner_px,
            inner_px,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )

        painter = QPainter(plate)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, plate.width(), plate.height()), radius * dpr, radius * dpr)
        painter.setClipPath(path)
        painter.fillPath(path, QColor(color))
        x = (plate.width() - scaled.width()) // 2
        y = (plate.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()

        plate.setDevicePixelRatio(dpr)
        icon = QIcon(plate)
        self._cache_put(self._icon_render_cache, cache_key, icon, self._icon_render_cache_limit)
        return icon

    def _icon_bg_color_for_btn(self, btn_data: dict[str, Any] | None = None) -> str:
        style = btn_data.get("style", {}) if isinstance(btn_data, dict) else {}
        candidate = str(style.get("bg_color", "") or "")
        if candidate:
            return self._normalize_hex_color(candidate, "#000000")
        defaults = self._display_settings.get("buttons", {}) if hasattr(self, "_display_settings") else {}
        return self._normalize_hex_color(str(defaults.get("bg_color", "#000000")), "#000000")

    def _render_svg_to_png(self, svg_path: str, dest_path: str, target_px: int = 512) -> bool:
        if QSvgRenderer is None:
            return False
        try:
            renderer = QSvgRenderer(svg_path)
            if not renderer.isValid():
                return False
            base = renderer.defaultSize()
            if not base.isValid() or base.width() <= 0 or base.height() <= 0:
                base = QSize(target_px, target_px)
            scale = min(target_px / base.width(), target_px / base.height())
            w = max(1, int(round(base.width() * scale)))
            h = max(1, int(round(base.height() * scale)))
            image = QImage(target_px, target_px, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            x = (target_px - w) // 2
            y = (target_px - h) // 2
            renderer.render(painter, QRectF(x, y, w, h))
            painter.end()
            return image.save(dest_path, "PNG")
        except Exception:
            return False

    def _convert_svg_icon(self, svg_path: str, target_px: int = 512) -> str | None:
        if not svg_path or QSvgRenderer is None:
            return None
        icons_dir = os.path.join(os.path.dirname(__file__), "icons")
        os.makedirs(icons_dir, exist_ok=True)
        icon_name = f"{Path(svg_path).stem}.png"
        dest_path = os.path.join(icons_dir, icon_name)
        ok = self._render_svg_to_png(svg_path, dest_path, target_px)
        return dest_path if ok else None

    def _set_button_icon(
        self,
        btn: QPushButton,
        img_path: str,
        cell_w: int,
        cell_h: int,
        margin: int = 8,
        bg_color: str | None = None,
    ) -> None:
        if not img_path or not os.path.exists(img_path):
            btn._icon_sig = None  # type: ignore[attr-defined]
            btn.setIcon(QIcon())
            return
        use_transparency = bool(
            self._display_settings.get("buttons", {}).get("icon_transparent", True)
            if hasattr(self, "_display_settings")
            else True
        )
        try:
            has_label = bool(btn.text().strip())
        except Exception:
            has_label = False
        effective_margin = margin
        if not has_label:
            effective_margin = 2 if use_transparency else 4
        file_sig = self._file_sig(img_path)
        icon_sig = (
            img_path,
            file_sig[0],
            file_sig[1],
            int(cell_w),
            int(cell_h),
            int(effective_margin),
            bool(use_transparency),
            bool(has_label),
            str(bg_color or ""),
        )
        if getattr(btn, "_icon_sig", None) == icon_sig:
            return
        if use_transparency:
            size = self._fit_icon_size(img_path, cell_w, cell_h, effective_margin)
            btn.setIcon(self._make_icon(img_path, size, btn, cover=not has_label))
        else:
            side = max(16, min(cell_w, cell_h) - effective_margin)
            size = QSize(side, side)
            plate = self._normalize_hex_color(str(bg_color or self._icon_bg_color_for_btn()), "#000000")
            btn.setIcon(self._make_icon_plate(img_path, size, plate, btn))
        btn.setIconSize(size)
        btn._icon_sig = icon_sig  # type: ignore[attr-defined]

    def _request_save(self, delay_ms: int = 250):
        self._save_timer.start(delay_ms)

    def _on_add_profile_clicked(self):
        dlg = NewProfileDialog(self.app_root, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        name, ptype = dlg.get_values()
        name = name.strip()
        if not name:
            return

        try:
            # použijeme existujúci ProfileManager, aby založil nový profil
            profile_name = self.profile_manager.add_profile(name)
        except ValueError as e:
            QMessageBox.warning(self, "Varovanie", str(e))
            return

        # centrálne nastavíme defaulty podľa typu
        self._ensure_profile_defaults(profile_name, ptype)

        # pridáme profil do listu vľavo
        self.listProfiles.addItem(profile_name)
        self._set_selected_profile(profile_name)
        # _on_profile_list_changed sa postará o načítanie a render podľa mode

        # uložíme stav
        self._request_save()

        self.statusBar().showMessage(f"Profil '{profile_name}' pridaný (typ: {ptype})")

    def _current_profile_mode(self, profile_name: str | None = None) -> str:
        """
        Vráti mode ('grid' / 'monitor' / 'media' / 'mixer' ...) pre daný profil.
        Ak profile_name nie je daný, použije aktuálny profil z ProfileManagera.
        """
        if profile_name is None:
            profile_name = self.profile_manager.current_profile

        prof = self.profile_manager.profiles.get(profile_name, {})
        if not isinstance(prof, dict):
            return "grid"
        return prof.get("mode", "grid")

    def _send_profile_change_to_esp(self, profile_name: str) -> None:
        """
        Pošle na ESP informáciu o zmene profilu.
        Očakávaný formát: SwitchProfile:<NÁZOV PROFILU>
        Uprav si podľa toho, čo spracúva firmware.
        """
        if not getattr(self, "serial_service", None) or not self.serial_service.is_connected:
            return

        line = f"SwitchProfile:{profile_name}"
        try:
            self.serial_service.send_line(line)
            self.logger.debug(f"[PROFILE→ESP] {line}")
        except Exception:
            self.logger.exception(f"[PROFILE→ESP] Error sending '{line}'")

    def _send_time_to_esp(self) -> None:
        if not getattr(self, "serial_service", None) or not self.serial_service.is_connected:
            return
        try:
            now = time.localtime()
            line = f"TIME:{now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d}"
            self.serial_service.send_line(line)
            date_line = f"DATE:{now.tm_year:04d}-{now.tm_mon:02d}-{now.tm_mday:02d}"
            self.serial_service.send_line(date_line)
            self.logger.debug(f"[PROFILE→ESP] {line} / {date_line}")
            temp = None
            stats = getattr(self, "_last_stats", None)
            if isinstance(stats, dict):
                t = stats.get("gpu_temp")
                if isinstance(t, (int, float)):
                    temp = float(t)
            if temp is not None and getattr(self, "serial_service", None):
                self.serial_service.send_line(f"TEMP:{temp:.1f}")
        except Exception as e:
            self.logger.exception("[TIME→ESP] Error sending time")



    def _clear_grid_widgets(self):
        """Vyprázdni gridLayout a korektne naloží s widgetmi."""
        if not hasattr(self, "gridLayout") or self.gridLayout is None:
            return

        while self.gridLayout.count():
            item = self.gridLayout.takeAt(0)
            w = item.widget()
            if not w:
                continue

            if isinstance(w, (SystemMonitorWidget, MediaWidget, MixerWidget)):
                # vyber z layoutu a skry
                self.gridLayout.removeWidget(w)
                w.hide()
            else:
                # bežné grid tlačidlá znič
                w.deleteLater()

        self.grid_buttons = {}
        self._grid_cell_to_anchor = {}
        self.selected_button_name = None
        self._grid_resize_drag_btn = None
        self._grid_resize_drag_anchor = None
        self._grid_resize_drag_start_global = None
        self._grid_resize_drag_start_span = (1, 1)
        self._grid_resize_drag_candidate = (1, 1)
        self._hide_grid_resize_preview()



    def _render_monitor_preview(self, profile_name: str):
        print("[DEBUG] _render_monitor_preview called")

        self._clear_grid_widgets()

        if not hasattr(self, "monitor_widget"):
            self.monitor_widget = SystemMonitorWidget(self)
            print("[DEBUG] monitor_widget created", self.monitor_widget, "parent=", self.monitor_widget.parent())
        else:
            print("[DEBUG] monitor_widget reuse", self.monitor_widget, "parent=", self.monitor_widget.parent())

        self.monitor_widget.show()

        self.gridLayout.addWidget(self.monitor_widget, 0, 0, 1, 1)
        self.gridLayout.setRowStretch(0, 1)
        self.gridLayout.setColumnStretch(0, 1)

    def _on_music_state_changed(self, state: MusicState) -> None:
        """
        Centrálna reakcia na zmeny hudobného stavu.
        UI aktualizujeme len vtedy, keď sme v 'media' profile
        a máme media_widget v gride. Zároveň udržiavame posledný známy stav
        pre okamžitý render pri prepnutí na media profil.
        """
        self._last_music_state = state
        self._last_media_progress_ts = time.monotonic()
        is_media_profile = self._current_profile_mode() == "media"
        has_widget = hasattr(self, "media_widget") and self.media_widget is not None

        if is_media_profile and has_widget:
            self.media_widget.apply_state(state)

        if is_media_profile:
            info = {
                "source": state.source,
                "source_app": state.source,
                "title": state.title,
                "artist": state.artist,
                "album": "",
                "position": state.pos_s,
                "duration": state.dur_s,
                "is_playing": state.is_playing,
                "volume_pct": state.volume_pct,
            }
            self._send_media_packet_to_esp(info)

    def _tick_media_progress(self) -> None:
        """
        Jednoduchý „tiker“ – keď je stav PLAYING, posúvame pozíciu aj bez novej
        udalosti z MediaStatusProvideru, aby UI a ESP neostali „zamrznuté“.
        """
        if not getattr(self, "_media_status_allowed", False):
            return

        st = getattr(self, "_last_music_state", None)
        if not st:
            return
        if not st.is_playing:
            self._last_media_progress_ts = time.monotonic()
            return

        now = time.monotonic()
        last = getattr(self, "_last_media_progress_ts", now)
        self._last_media_progress_ts = now
        if now <= last:
            return

        delta = int(round(now - last))
        if delta <= 0:
            return

        new_pos = min(st.dur_s, st.pos_s + delta)
        if new_pos != st.pos_s:
            self.music_manager.update_position_only(new_pos)

    def _update_media_timers(self, enable: bool) -> None:
        """Zapína/vypína media progres timer podľa aktívneho profilu."""
        if not hasattr(self, "_media_progress_timer"):
            return
        if enable and not self._media_timer_running:
            self._media_progress_timer.start()
            self._media_timer_running = True
        elif (not enable) and self._media_timer_running:
            self._media_progress_timer.stop()
            self._media_timer_running = False


    # ---------- ovládanie médií z tlačidiel v widgete ----------

    def _render_media_preview(self, profile_name: str):
        self._clear_grid_widgets()

        if not hasattr(self, "media_widget"):
            self.media_widget = MediaWidget(self)
            self.media_widget.prevClicked.connect(self._media_prev)
            self.media_widget.playPauseClicked.connect(self._media_playpause)
            self.media_widget.nextClicked.connect(self._media_next)
            print("[DEBUG] media_widget created", self.media_widget, "parent=", self.media_widget.parent())
        else:
            print("[DEBUG] media_widget reuse", self.media_widget, "parent=", self.media_widget.parent())

        self.media_widget.show()
        if getattr(self, "_last_music_state", None):
            self.media_widget.apply_state(self._last_music_state)

        self.gridLayout.addWidget(self.media_widget, 0, 0, 1, 1)
        self.gridLayout.setRowStretch(0, 1)
        self.gridLayout.setColumnStretch(0, 1)

    # ---------- Mixer preview ----------

    def _mixer_accent_color(self, accent: str | None) -> QColor:
        mapping = {
            "red": QColor(239, 68, 68),
            "green": QColor(34, 197, 94),
            "emerald": QColor(22, 163, 74),
            "slate": QColor(100, 116, 139),
        }
        return mapping.get(accent or "", QColor(71, 214, 127))

    def _make_mixer_column(
        self,
        key: str,
        title: str,
        on_change,
        with_mute: bool = False,
        on_mute_toggle=None,
        accent: str | None = None,
    ) -> dict[str, Any]:
        col = QWidget(self.mixer_widget)
        col.setProperty("role", "mixer-column")
        if accent:
            col.setProperty("accent", accent)
        col.setObjectName(f"MixerColumn_{key}")
        col.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        col.setMinimumWidth(110)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        header = QWidget(col)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(2, 2, 2, 0)
        header_lay.setSpacing(6)
        dot = QLabel("")
        dot.setFixedSize(10, 10)
        dot.setProperty("role", "dot")
        if accent:
            dot.setProperty("accent", accent)
        label = QLabel(title)
        label.setProperty("role", "title")
        if accent:
            label.setProperty("accent", accent)
        header_lay.addWidget(dot)
        header_lay.addWidget(label)
        header_lay.addStretch(1)

        slider = MixerSlider(self._mixer_accent_color(accent), col)
        slider.setObjectName(f"MixerSlider_{key}")
        slider.setFixedWidth(40)
        slider.setValue(0)
        slider.setProperty("mixerSlider", True)
        slider.installEventFilter(self)
        slider.sliderReleased.connect(lambda k=key: self._flush_mixer_volume_now(k))
        value = QLabel("0%")
        value.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        value.setProperty("role", "value")
        if accent:
            value.setProperty("accent", accent)
        mute_btn = None
        empty_label = QLabel("Empty")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        empty_label.setProperty("role", "empty")
        if with_mute:
            mute_btn = QPushButton("Mute")
            mute_btn.setCheckable(True)
            mute_btn.setProperty("role", "mute")
            if accent:
                mute_btn.setProperty("accent", accent)
            if on_mute_toggle is not None:
                mute_btn.toggled.connect(on_mute_toggle)

        lay.addWidget(header)
        lay.addWidget(slider, 1, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(value)
        if mute_btn is not None:
            lay.addWidget(mute_btn)
        lay.addWidget(empty_label)

        if on_change is not None:
            slider.valueChanged.connect(on_change)

        return {
            "container": col,
            "label": label,
            "dot": dot,
            "slider": slider,
            "value": value,
            "mute_btn": mute_btn,
            "empty_label": empty_label,
            "active": True,
        }

    def _refresh_widget_style(self, widget: QWidget | None) -> None:
        if widget is None:
            return
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _queue_mixer_volume(self, key: str, value: int, apply_fn) -> None:
        if self._mixer_updating:
            return
        value = max(0, min(100, int(value)))
        self._mixer_pending_volume[key] = (value, apply_fn)
        timer = self._mixer_volume_timers.get(key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda k=key: self._flush_mixer_volume(k))
            self._mixer_volume_timers[key] = timer
        timer.start(self._mixer_volume_throttle_ms)

    def _flush_mixer_volume(self, key: str) -> None:
        pending = self._mixer_pending_volume.get(key)
        if not pending:
            return
        if self._mixer_updating:
            timer = self._mixer_volume_timers.get(key)
            if timer is not None:
                timer.start(self._mixer_volume_throttle_ms)
            return
        value, apply_fn = pending
        self._mixer_pending_volume.pop(key, None)
        try:
            apply_fn(value)
        except Exception:
            pass

    def _flush_mixer_volume_now(self, key: str) -> None:
        timer = self._mixer_volume_timers.get(key)
        if timer is not None:
            timer.stop()
        self._flush_mixer_volume(key)

    def _apply_mixer_master_volume(self, value: int) -> None:
        if IS_WINDOWS:
            ok = self._win_set_endpoint_volume(self._win_get_master_endpoint(), value)
            if not ok:
                try:
                    self.media_backend.set_volume(int(value))
                except Exception as e:
                    print(f"[Mixer] master volume fallback failed: {e}")
            return
        if not self._pactl_available():
            return
        self._pactl_set(["set-sink-volume", "@DEFAULT_SINK@", f"{value}%"])

    def _apply_mixer_mic_volume(self, value: int) -> None:
        if IS_WINDOWS:
            self._win_set_endpoint_volume(self._win_get_mic_endpoint(), value)
            return
        if not self._pactl_available():
            return
        self._pactl_set(["set-source-volume", "@DEFAULT_SOURCE@", f"{value}%"])

    def _set_mixer_column_active(self, slot: dict[str, Any], active: bool) -> None:
        slot["active"] = bool(active)
        slider = slot.get("slider")
        if isinstance(slider, MixerSlider):
            slider.setActive(active)
        container = slot.get("container")
        label = slot.get("label")
        dot = slot.get("dot")
        value = slot.get("value")
        mute_btn = slot.get("mute_btn")
        for w in (container, label, dot, value, mute_btn):
            if isinstance(w, QWidget):
                w.setProperty("active", active)
                self._refresh_widget_style(w)
        if isinstance(mute_btn, QWidget):
            mute_btn.setVisible(active)
        empty_label = slot.get("empty_label")
        if isinstance(empty_label, QWidget):
            empty_label.setVisible(not active)

    def _build_mixer_widget(self) -> None:
        self.mixer_widget = MixerWidget(self)
        self.mixer_widget.setObjectName("MixerRoot")
        self.mixer_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.mixer_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.mixer_widget.setMinimumSize(420, 260)
        root = QVBoxLayout(self.mixer_widget)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(18)
        root.addLayout(row, 1)

        self._mixer_channels: dict[str, dict[str, Any]] = {}
        self._mixer_channels["mic"] = self._make_mixer_column(
            "mic",
            "Mic",
            self._on_mixer_mic_changed,
            with_mute=True,
            on_mute_toggle=self._on_mixer_mic_mute_toggled,
            accent="red",
        )
        self._mixer_channels["master"] = self._make_mixer_column(
            "master",
            "Master",
            self._on_mixer_master_changed,
            with_mute=True,
            on_mute_toggle=self._on_mixer_master_mute_toggled,
            accent="green",
        )

        row.addWidget(self._mixer_channels["mic"]["container"], 1)
        row.addWidget(self._mixer_channels["master"]["container"], 1)
        self._set_mixer_column_active(self._mixer_channels["mic"], True)
        self._set_mixer_column_active(self._mixer_channels["master"], True)

        self._mixer_app_slots = []
        for idx in range(2):
            slot = self._make_mixer_column(
                f"app{idx + 1}",
                f"App {idx + 1}",
                lambda value, i=idx: self._on_mixer_app_changed(i, value),
                with_mute=True,
                on_mute_toggle=lambda checked, i=idx: self._on_mixer_app_mute_toggled(i, checked),
                accent="emerald" if idx == 0 else "slate",
            )
            slot["id"] = None
            self._mixer_app_slots.append(slot)
            row.addWidget(slot["container"], 1)
            self._set_mixer_column_active(slot, False)

        self._mixer_status_label = QLabel("")
        self._mixer_status_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._mixer_status_label.setProperty("role", "status")
        root.addWidget(self._mixer_status_label, 0)
        self._apply_mixer_style()

    def _apply_mixer_style(self) -> None:
        if not self.mixer_widget:
            return
        self.mixer_widget.setStyleSheet(
            """
#MixerRoot {
    background: qradialgradient(
        cx:0.5, cy:0.0, radius:1.2, fx:0.5, fy:0.0,
        stop:0 rgba(76, 93, 128, 0.18),
        stop:1 #0b0f1a
    );
    border: 2px solid rgba(255, 255, 255, 0.26);
    border-radius: 20px;
}

#MixerRoot QWidget[role="mixer-column"] {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #0f1422,
        stop:1 #0b0f1a
    );
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 16px;
}

#MixerRoot QWidget[role="mixer-column"][active="false"] {
    background-color: #0c1018;
    border-color: rgba(255, 255, 255, 0.04);
}

#MixerRoot QWidget[role="mixer-column"][accent="red"] {
    border-top: 2px solid #ef4444;
}

#MixerRoot QWidget[role="mixer-column"][accent="green"] {
    border-top: 2px solid #22c55e;
}

#MixerRoot QWidget[role="mixer-column"][accent="emerald"] {
    border-top: 2px solid #16a34a;
}

#MixerRoot QWidget[role="mixer-column"][accent="slate"] {
    border-top: 2px solid #64748b;
}

#MixerRoot QLabel[role="title"] {
    color: #e2e8f0;
    font-size: 11px;
    font-weight: 600;
}

#MixerRoot QLabel[role="title"][active="false"] {
    color: #94a3b8;
}

#MixerRoot QLabel[role="title"][active="true"][accent="red"] {
    color: #ef4444;
}

#MixerRoot QLabel[role="title"][active="true"][accent="green"] {
    color: #22c55e;
}

#MixerRoot QLabel[role="title"][active="true"][accent="emerald"] {
    color: #16a34a;
}

#MixerRoot QLabel[role="title"][active="true"][accent="slate"] {
    color: #64748b;
}

#MixerRoot QLabel[role="dot"] {
    background-color: #475569;
    border: 1px solid #1f2937;
    border-radius: 5px;
}

#MixerRoot QLabel[role="dot"][active="false"] {
    background-color: #334155;
}

#MixerRoot QLabel[role="dot"][active="true"][accent="red"] {
    background-color: #ef4444;
}

#MixerRoot QLabel[role="dot"][active="true"][accent="green"] {
    background-color: #22c55e;
}

#MixerRoot QLabel[role="dot"][active="true"][accent="emerald"] {
    background-color: #16a34a;
}

#MixerRoot QLabel[role="dot"][active="true"][accent="slate"] {
    background-color: #64748b;
}

#MixerRoot QLabel[role="value"] {
    color: #f8fafc;
    font-size: 12px;
    font-weight: 600;
}

#MixerRoot QLabel[role="value"][active="false"] {
    color: #94a3b8;
}

#MixerRoot QLabel[role="status"] {
    color: #9aa4b2;
    font-size: 11px;
}

#MixerRoot QLabel[role="empty"] {
    background-color: #111827;
    border: 1px dashed #2b3442;
    border-radius: 10px;
    padding: 4px 10px;
    color: #94a3b8;
    font-size: 10px;
}

#MixerRoot QPushButton[role="mute"] {
    background-color: #111827;
    border: 1px solid #273244;
    border-radius: 10px;
    padding: 6px 12px;
    min-height: 22px;
    color: #cbd5e1;
}

#MixerRoot QPushButton[role="mute"]:hover {
    border-color: rgba(148, 163, 184, 0.6);
}

#MixerRoot QPushButton[role="mute"]:checked {
    background-color: rgba(248, 113, 113, 0.16);
    border-color: #f87171;
    color: #fee2e2;
}

#MixerRoot QPushButton[role="mute"][accent="green"]:checked {
    background-color: rgba(74, 222, 128, 0.16);
    border-color: #4ade80;
    color: #dcfce7;
}

#MixerRoot QPushButton[role="mute"]:disabled {
    color: #6b7280;
    background-color: rgba(12, 15, 22, 0.7);
    border-color: rgba(255, 255, 255, 0.04);
}
"""
        )

    def _render_mixer_preview(self, profile_name: str):
        self._clear_grid_widgets()

        if not self.mixer_widget:
            self._build_mixer_widget()

        self.mixer_widget.show()
        self.gridLayout.addWidget(self.mixer_widget, 0, 0, 1, 1)
        self.gridLayout.setRowStretch(0, 1)
        self.gridLayout.setColumnStretch(0, 1)
        self._refresh_mixer_state()

    def _update_mixer_timers(self, enable: bool) -> None:
        if not hasattr(self, "_mixer_timer"):
            return
        if enable and not self._mixer_timer_running:
            self._mixer_timer.start()
            self._mixer_timer_running = True
        elif (not enable) and self._mixer_timer_running:
            self._mixer_timer.stop()
            self._mixer_timer_running = False

    def _set_mixer_status(self, text: str) -> None:
        if self._mixer_status_label:
            self._mixer_status_label.setText(text or "")

    def _set_mixer_enabled(self, enabled: bool) -> None:
        if not hasattr(self, "_mixer_channels"):
            return
        for slot in self._mixer_channels.values():
            slot["slider"].setEnabled(enabled)
            mute_btn = slot.get("mute_btn")
            if mute_btn:
                mute_btn.setEnabled(enabled)
        for slot in self._mixer_app_slots:
            slider = slot.get("slider")
            active = bool(slot.get("active", False))
            if slider:
                slider.setEnabled(enabled and active)
            mute_btn = slot.get("mute_btn")
            if mute_btn:
                mute_btn.setEnabled(enabled and active)

    def _update_mute_button(self, btn: QPushButton | None, muted: bool) -> None:
        if btn is None:
            return
        btn.blockSignals(True)
        btn.setChecked(bool(muted))
        btn.blockSignals(False)
        btn.setText("Muted" if muted else "Mute")

    def _prettify_app_name(self, raw: str) -> str:
        name = (raw or "").strip().strip('"')
        if not name:
            return "App"
        key = name.lower()
        mapping = {
            "spotify": "Spotify",
            "com.spotify.client": "Spotify",
            "discord": "Discord",
            "com.discordapp.discord": "Discord",
            "webcord": "Discord",
            "firefox": "Firefox",
            "org.mozilla.firefox": "Firefox",
            "chromium": "Chromium",
            "org.chromium.chromium": "Chromium",
            "google-chrome": "Chrome",
            "chrome": "Chrome",
            "com.google.chrome": "Chrome",
            "vlc": "VLC",
            "org.videolan.vlc": "VLC",
            "obs": "OBS",
            "obs-studio": "OBS",
            "com.obsproject.studio": "OBS",
            "steam": "Steam",
            "com.valvesoftware.steam": "Steam",
            "teams": "Teams",
            "com.microsoft.teams": "Teams",
            "telegram": "Telegram",
            "org.telegram.desktop": "Telegram",
            "thunderbird": "Thunderbird",
            "org.mozilla.thunderbird": "Thunderbird",
            "signal": "Signal",
            "org.signal.signal": "Signal",
        }
        if key in mapping:
            return mapping[key]
        if key.startswith(("com.", "org.", "net.", "io.")):
            parts = key.split(".")
            if parts:
                name = parts[-1]
        name = re.sub(r"[-_.]+", " ", name).strip()
        return name.title() if name else "App"

    def _sanitize_mixer_label(self, raw: str) -> str:
        name = (raw or "").strip().strip('"')
        if not name:
            return "App"
        name = re.sub(r"[;,]", " ", name)
        name = name.encode("ascii", "ignore").decode("ascii")
        name = re.sub(r"\s+", " ", name).strip()
        if not name:
            return "App"
        return name[:14]

    def _parse_volume_pct(self, text: str) -> int | None:
        if not text:
            return None
        m = re.search(r"/\s*(\d+)%", text)
        if m:
            return int(m.group(1))
        m = re.search(r"(\d+)%", text)
        if m:
            return int(m.group(1))
        return None

    def _win_audio_ready(self) -> bool:
        if not IS_WINDOWS or self._win_audio_failed:
            return False
        try:
            import pythoncom  # type: ignore
            pythoncom.CoInitialize()
            from pycaw.pycaw import AudioUtilities  # noqa: F401
            return True
        except Exception as e:
            self._win_audio_failed = True
            self._win_audio_error = str(e)
            return False

    def _win_get_master_endpoint(self):
        if not self._win_audio_ready():
            return None
        if self._win_master_endpoint is not None:
            return self._win_master_endpoint
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL  # type: ignore
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore
            dev = AudioUtilities.GetSpeakers()
            if dev is None:
                return None
            interface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            self._win_master_endpoint = cast(interface, POINTER(IAudioEndpointVolume))
        except Exception as e:
            self._win_audio_error = f"master endpoint: {e}"
            self._win_master_endpoint = None
        return self._win_master_endpoint

    def _win_get_mic_endpoint(self):
        if not self._win_audio_ready():
            return None
        if self._win_mic_endpoint is not None:
            return self._win_mic_endpoint
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL  # type: ignore
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore
            dev = AudioUtilities.GetMicrophone()
            if dev is None:
                return None
            interface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            self._win_mic_endpoint = cast(interface, POINTER(IAudioEndpointVolume))
        except Exception as e:
            self._win_audio_error = f"mic endpoint: {e}"
            self._win_mic_endpoint = None
        return self._win_mic_endpoint

    def _win_get_endpoint_volume(self, endpoint) -> int | None:
        if endpoint is None:
            return None
        try:
            return int(round(float(endpoint.GetMasterVolumeLevelScalar()) * 100))
        except Exception:
            return None

    def _win_get_endpoint_mute(self, endpoint) -> bool:
        if endpoint is None:
            return False
        try:
            return bool(endpoint.GetMute())
        except Exception:
            return False

    def _win_set_endpoint_volume(self, endpoint, value: int) -> bool:
        if endpoint is None:
            return False
        try:
            endpoint.SetMasterVolumeLevelScalar(max(0, min(100, value)) / 100.0, None)
            return True
        except Exception:
            return False

    def _win_set_endpoint_mute(self, endpoint, muted: bool) -> None:
        if endpoint is None:
            return
        try:
            endpoint.SetMute(1 if muted else 0, None)
        except Exception:
            pass

    def _win_list_sessions(self) -> list[dict[str, Any]]:
        if not self._win_audio_ready():
            return []
        try:
            from pycaw.pycaw import AudioUtilities  # type: ignore
        except Exception:
            return []
        sessions = []
        for sess in AudioUtilities.GetAllSessions():
            try:
                vol_iface = sess.SimpleAudioVolume
                vol = int(round(float(vol_iface.GetMasterVolume()) * 100))
                mute = bool(vol_iface.GetMute())
            except Exception:
                continue
            display = (sess.DisplayName or "").strip()
            proc_name = ""
            try:
                if sess.Process is not None:
                    proc_name = sess.Process.name()
            except Exception:
                proc_name = ""
            raw = display or proc_name or f"PID {sess.ProcessId}"
            if raw.lower().endswith(".exe"):
                raw = raw[:-4]
            name = self._prettify_app_name(raw)
            sid = sess.InstanceIdentifier or sess.Identifier or str(sess.ProcessId)
            sessions.append(
                {
                    "id": sid,
                    "name": name or "App",
                    "raw_name": raw or "",
                    "volume_pct": vol,
                    "mute": mute,
                }
            )
        sessions.sort(key=lambda s: str(s.get("name", "")))
        return sessions

    def _win_find_session(self, session_id: str):
        if not session_id or not self._win_audio_ready():
            return None
        try:
            from pycaw.pycaw import AudioUtilities  # type: ignore
        except Exception:
            return None
        for sess in AudioUtilities.GetAllSessions():
            sid = sess.InstanceIdentifier or sess.Identifier or str(sess.ProcessId)
            if sid == session_id:
                return sess
        return None

    def _win_set_session_volume(self, session_id: str, value: int) -> None:
        sess = self._win_find_session(session_id)
        if sess is None:
            return
        try:
            sess.SimpleAudioVolume.SetMasterVolume(max(0, min(100, value)) / 100.0, None)
        except Exception:
            pass

    def _win_set_session_mute(self, session_id: str, muted: bool) -> None:
        sess = self._win_find_session(session_id)
        if sess is None:
            return
        try:
            sess.SimpleAudioVolume.SetMute(bool(muted), None)
        except Exception:
            pass

    def _pactl_available(self) -> bool:
        return shutil.which("pactl") is not None

    def _pactl_json(self, args: list[str]) -> Any | None:
        try:
            res = subprocess.run(
                ["pactl", "-f", "json"] + args,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return None
        out = (res.stdout or "").strip()
        if not out:
            return None
        start = None
        for ch in ("{", "["):
            idx = out.find(ch)
            if idx != -1:
                start = idx if start is None else min(start, idx)
        if start is None:
            return None
        try:
            return json.loads(out[start:])
        except Exception:
            return None

    def _volume_from_json(self, volume: Any) -> int | None:
        if not isinstance(volume, dict):
            return None
        pcts: list[int] = []
        for ch in volume.values():
            if not isinstance(ch, dict):
                continue
            pct = self._parse_volume_pct(str(ch.get("value_percent") or ""))
            if pct is not None:
                pcts.append(pct)
        if not pcts:
            return None
        return int(round(sum(pcts) / len(pcts)))

    def _pactl_output(self, args: list[str]) -> str:
        try:
            res = subprocess.run(
                ["pactl"] + args,
                capture_output=True,
                text=True,
                check=False,
            )
            return (res.stdout or "").strip()
        except Exception:
            return ""

    def _pactl_set(self, args: list[str]) -> None:
        try:
            subprocess.run(["pactl"] + args, check=False)
        except Exception:
            pass

    def _list_sink_inputs(self) -> list[dict[str, Any]]:
        data = self._pactl_json(["list", "sink-inputs"])
        if not isinstance(data, list):
            return []
        inputs: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            sid = item.get("index")
            if sid is None:
                continue
            props = item.get("properties") or {}
            name = ""
            if isinstance(props, dict):
                name = (
                    props.get("application.name")
                    or props.get("application.process.binary")
                    or props.get("media.name")
                    or ""
                )
            vol = self._volume_from_json(item.get("volume"))
            mute = bool(item.get("mute", False))
            display = self._prettify_app_name(str(name or ""))
            inputs.append(
                {
                    "id": sid,
                    "name": display or f"App {sid}",
                    "raw_name": name or "",
                    "volume_pct": vol,
                    "mute": mute,
                }
            )
        return inputs

    def _update_app_slot(self, slot: dict[str, Any], app: dict[str, Any]) -> None:
        slot["id"] = app.get("id")
        slot["name"] = app.get("name", "App")
        slot["raw_name"] = app.get("raw_name", "")
        slot["mute"] = bool(app.get("mute", False))
        slot["label"].setText(slot["name"])
        slot["label"].setToolTip(slot["raw_name"])
        slot["container"].setVisible(True)
        self._set_mixer_column_active(slot, True)
        slider = slot.get("slider")
        if isinstance(slider, MixerSlider):
            slider.setEnabled(True)
        self._update_mute_button(slot.get("mute_btn"), slot["mute"])
        vol = app.get("volume_pct")
        if vol is not None and not slot["slider"].isSliderDown():
            self._update_slider(slot, vol)

    def _clear_app_slot(self, slot: dict[str, Any]) -> None:
        slot["id"] = None
        slot["label"].setText("Empty")
        slot["label"].setToolTip("")
        slot["name"] = "Empty"
        slot["raw_name"] = ""
        slot["mute"] = False
        slot["container"].setVisible(True)
        self._set_mixer_column_active(slot, False)
        slider = slot.get("slider")
        if isinstance(slider, MixerSlider):
            slider.setEnabled(False)
        self._update_mute_button(slot.get("mute_btn"), False)
        self._update_slider(slot, 0)

    def _update_slider(self, slot: dict[str, Any], value: int) -> None:
        value = max(0, min(100, int(value)))
        slider = slot["slider"]
        slider.blockSignals(True)
        slider.setValue(value)
        slider.blockSignals(False)
        slot["value"].setText(f"{value}%")

    def _refresh_mixer_state(self) -> None:
        if not self.mixer_widget:
            return
        if IS_WINDOWS:
            if not self._win_audio_ready():
                msg = "Mixer on Windows requires pycaw."
                if self._win_audio_error:
                    msg += f" ({self._win_audio_error})"
                self._set_mixer_status(msg)
                self._set_mixer_enabled(False)
                return
            self._set_mixer_status("")
            self._set_mixer_enabled(True)
            self._mixer_updating = True
            try:
                master_ep = self._win_get_master_endpoint()
                master = self._win_get_endpoint_volume(master_ep)
                if master is not None and not self._mixer_channels["master"]["slider"].isSliderDown():
                    self._update_slider(self._mixer_channels["master"], master)
                master_muted = self._win_get_endpoint_mute(master_ep)
                self._update_mute_button(self._mixer_channels["master"].get("mute_btn"), master_muted)
                self._mixer_master_muted = master_muted

                mic_ep = self._win_get_mic_endpoint()
                if mic_ep is None:
                    self._set_mixer_column_active(self._mixer_channels["mic"], False)
                    mic_slider = self._mixer_channels["mic"].get("slider")
                    if isinstance(mic_slider, MixerSlider):
                        mic_slider.setEnabled(False)
                    self._update_mute_button(self._mixer_channels["mic"].get("mute_btn"), False)
                    self._mixer_mic_muted = False
                else:
                    self._set_mixer_column_active(self._mixer_channels["mic"], True)
                    mic_slider = self._mixer_channels["mic"].get("slider")
                    if isinstance(mic_slider, MixerSlider):
                        mic_slider.setEnabled(True)
                    mic = self._win_get_endpoint_volume(mic_ep)
                    if mic is not None and not mic_slider.isSliderDown():
                        self._update_slider(self._mixer_channels["mic"], mic)
                    mic_muted = self._win_get_endpoint_mute(mic_ep)
                    self._update_mute_button(self._mixer_channels["mic"].get("mute_btn"), mic_muted)
                    self._mixer_mic_muted = mic_muted

                apps = self._win_list_sessions()
                active = {app.get("id"): app for app in apps if app.get("id") is not None}

                for slot in self._mixer_app_slots:
                    slot_id = slot.get("id")
                    if slot_id in active:
                        app = active.pop(slot_id)
                        self._update_app_slot(slot, app)
                    else:
                        self._clear_app_slot(slot)

                remaining = list(active.values())
                remaining.sort(key=lambda a: a.get("name", ""))
                for slot in self._mixer_app_slots:
                    if slot.get("id") is None and remaining:
                        app = remaining.pop(0)
                        self._update_app_slot(slot, app)

                has_visible_app = any(slot.get("id") is not None for slot in self._mixer_app_slots)
                if not has_visible_app:
                    self._set_mixer_status("Spusť audio, aby sa appky zobrazili.")
                else:
                    self._set_mixer_status("")

                self._send_mixer_packet_to_esp()
            finally:
                self._mixer_updating = False
            return
        if not self._pactl_available():
            self._set_mixer_status("Mixer vyzaduje 'pactl' (PulseAudio/PipeWire).")
            self._set_mixer_enabled(False)
            return

        self._set_mixer_status("")
        self._set_mixer_enabled(True)
        self._mixer_updating = True
        try:
            info = self._pactl_json(["info"]) or {}
            default_sink = info.get("default_sink_name")
            default_source = info.get("default_source_name")
            self._mixer_default_sink = default_sink
            self._mixer_default_source = default_source

            sinks = self._pactl_json(["list", "sinks"]) or []
            sink = None
            if isinstance(sinks, list):
                if default_sink:
                    sink = next((s for s in sinks if s.get("name") == default_sink), None)
                if sink is None and sinks:
                    sink = sinks[0]
            master = self._volume_from_json(sink.get("volume") if isinstance(sink, dict) else None)
            if master is not None and not self._mixer_channels["master"]["slider"].isSliderDown():
                self._update_slider(self._mixer_channels["master"], master)
            master_muted = bool(sink.get("mute")) if isinstance(sink, dict) else False
            self._update_mute_button(self._mixer_channels["master"].get("mute_btn"), master_muted)
            self._mixer_master_muted = master_muted

            sources = self._pactl_json(["list", "sources"]) or []
            source = None
            if isinstance(sources, list):
                if default_source:
                    source = next((s for s in sources if s.get("name") == default_source), None)
                if source is None:
                    source = next(
                        (s for s in sources if isinstance(s, dict) and not str(s.get("name", "")).endswith(".monitor")),
                        None,
                    )
                if source is None and sources:
                    source = sources[0]
            mic = self._volume_from_json(source.get("volume") if isinstance(source, dict) else None)
            if mic is not None and not self._mixer_channels["mic"]["slider"].isSliderDown():
                self._update_slider(self._mixer_channels["mic"], mic)
            mic_muted = bool(source.get("mute")) if isinstance(source, dict) else False
            self._update_mute_button(self._mixer_channels["mic"].get("mute_btn"), mic_muted)
            self._mixer_mic_muted = mic_muted

            apps = self._list_sink_inputs()
            active = {app.get("id"): app for app in apps if app.get("id") is not None}

            for slot in self._mixer_app_slots:
                slot_id = slot.get("id")
                if slot_id in active:
                    app = active.pop(slot_id)
                    self._update_app_slot(slot, app)
                else:
                    self._clear_app_slot(slot)

            remaining = list(active.values())
            remaining.sort(key=lambda a: a.get("id", 0))
            for slot in self._mixer_app_slots:
                if slot.get("id") is None and remaining:
                    app = remaining.pop(0)
                    self._update_app_slot(slot, app)

            has_visible_app = any(slot.get("id") is not None for slot in self._mixer_app_slots)
            if not has_visible_app:
                self._set_mixer_status("Spusť audio, aby sa appky zobrazili.")
            else:
                self._set_mixer_status("")

            self._send_mixer_packet_to_esp()
        finally:
            self._mixer_updating = False

    def _on_mixer_master_changed(self, value: int) -> None:
        if self._mixer_updating:
            return
        self._mixer_channels["master"]["value"].setText(f"{value}%")
        self._queue_mixer_volume("master", value, self._apply_mixer_master_volume)

    def _on_mixer_mic_changed(self, value: int) -> None:
        if self._mixer_updating:
            return
        self._mixer_channels["mic"]["value"].setText(f"{value}%")
        self._queue_mixer_volume("mic", value, self._apply_mixer_mic_volume)

    def _on_mixer_master_mute_toggled(self, checked: bool) -> None:
        if self._mixer_updating:
            return
        if IS_WINDOWS:
            self._win_set_endpoint_mute(self._win_get_master_endpoint(), checked)
            self._update_mute_button(self._mixer_channels["master"].get("mute_btn"), checked)
            self._mixer_master_muted = bool(checked)
            return
        if not self._pactl_available():
            return
        self._pactl_set(["set-sink-mute", "@DEFAULT_SINK@", "1" if checked else "0"])
        self._update_mute_button(self._mixer_channels["master"].get("mute_btn"), checked)
        self._mixer_master_muted = bool(checked)

    def _on_mixer_mic_mute_toggled(self, checked: bool) -> None:
        if self._mixer_updating:
            return
        if IS_WINDOWS:
            self._win_set_endpoint_mute(self._win_get_mic_endpoint(), checked)
            self._update_mute_button(self._mixer_channels["mic"].get("mute_btn"), checked)
            self._mixer_mic_muted = bool(checked)
            return
        if not self._pactl_available():
            return
        self._pactl_set(["set-source-mute", "@DEFAULT_SOURCE@", "1" if checked else "0"])
        self._update_mute_button(self._mixer_channels["mic"].get("mute_btn"), checked)
        self._mixer_mic_muted = bool(checked)

    def _set_mic_mute_state(self, muted: bool) -> None:
        ok = True
        if IS_WINDOWS:
            endpoint = self._win_get_mic_endpoint()
            if endpoint is None:
                ok = False
            else:
                self._win_set_endpoint_mute(endpoint, muted)
        else:
            if not self._pactl_available():
                ok = False
            else:
                self._pactl_set(["set-source-mute", "@DEFAULT_SOURCE@", "1" if muted else "0"])
        if not ok:
            return
        self._mixer_mic_muted = bool(muted)
        if hasattr(self, "_mixer_channels"):
            slot = self._mixer_channels.get("mic") if isinstance(self._mixer_channels, dict) else None
            if slot:
                self._update_mute_button(slot.get("mute_btn"), muted)
        self._send_mixer_packet_to_esp(force=True)

    def _on_mixer_app_mute_toggled(self, index: int, checked: bool) -> None:
        if self._mixer_updating:
            return
        if IS_WINDOWS:
            if index < 0 or index >= len(self._mixer_app_slots):
                return
            slot = self._mixer_app_slots[index]
            slot_id = slot.get("id")
            if slot_id is None:
                self._update_mute_button(slot.get("mute_btn"), False)
                return
            self._win_set_session_mute(str(slot_id), checked)
            slot["mute"] = bool(checked)
            self._update_mute_button(slot.get("mute_btn"), checked)
            return
        if not self._pactl_available():
            return
        if index < 0 or index >= len(self._mixer_app_slots):
            return
        slot = self._mixer_app_slots[index]
        slot_id = slot.get("id")
        if slot_id is None:
            self._update_mute_button(slot.get("mute_btn"), False)
            return
        self._pactl_set(["set-sink-input-mute", str(slot_id), "1" if checked else "0"])
        slot["mute"] = bool(checked)
        self._update_mute_button(slot.get("mute_btn"), checked)

    def _set_mixer_app_volume_slot(self, index: int, value: int) -> None:
        if not IS_WINDOWS and not self._pactl_available():
            return
        if index < 0 or index >= len(self._mixer_app_slots):
            return
        slot = self._mixer_app_slots[index]
        slot_id = slot.get("id")
        if slot_id is None:
            return
        if IS_WINDOWS:
            self._win_set_session_volume(str(slot_id), value)
        else:
            self._pactl_set(["set-sink-input-volume", str(slot_id), f"{value}%"])
        slider = slot.get("slider")
        if isinstance(slider, MixerSlider) and slider.isSliderDown():
            slot["value"].setText(f"{int(value)}%")
            return
        self._update_slider(slot, value)

    def _set_mixer_app_mute_slot(self, index: int, muted: bool) -> None:
        if not IS_WINDOWS and not self._pactl_available():
            return
        if index < 0 or index >= len(self._mixer_app_slots):
            return
        slot = self._mixer_app_slots[index]
        slot_id = slot.get("id")
        if slot_id is None:
            return
        if IS_WINDOWS:
            self._win_set_session_mute(str(slot_id), muted)
        else:
            self._pactl_set(["set-sink-input-mute", str(slot_id), "1" if muted else "0"])
        slot["mute"] = bool(muted)
        self._update_mute_button(slot.get("mute_btn"), muted)

    def _handle_mixer_command(self, payload: str) -> None:
        if not IS_WINDOWS and not self._pactl_available():
            return
        self._mixer_updating = True
        has_ui = hasattr(self, "_mixer_channels")
        try:
            for token in (payload or "").split(";"):
                t = token.strip()
                if not t:
                    continue
                if t.startswith("MASTER="):
                    val = int(t.split("=", 1)[1] or "0")
                    if IS_WINDOWS:
                        ok = self._win_set_endpoint_volume(self._win_get_master_endpoint(), val)
                        if not ok:
                            try:
                                self.media_backend.set_volume(int(val))
                            except Exception as e:
                                print(f"[Mixer] master volume fallback failed: {e}")
                    else:
                        self._pactl_set(["set-sink-volume", "@DEFAULT_SINK@", f"{val}%"])
                    if has_ui:
                        self._update_slider(self._mixer_channels["master"], val)
                elif t.startswith("MIC="):
                    val = int(t.split("=", 1)[1] or "0")
                    if IS_WINDOWS:
                        self._win_set_endpoint_volume(self._win_get_mic_endpoint(), val)
                    else:
                        self._pactl_set(["set-source-volume", "@DEFAULT_SOURCE@", f"{val}%"])
                    if has_ui:
                        self._update_slider(self._mixer_channels["mic"], val)
                elif t.startswith("MM="):
                    val = int(t.split("=", 1)[1] or "0")
                    if IS_WINDOWS:
                        self._win_set_endpoint_mute(self._win_get_master_endpoint(), bool(val))
                    else:
                        self._pactl_set(["set-sink-mute", "@DEFAULT_SINK@", "1" if val else "0"])
                    if has_ui:
                        self._update_mute_button(self._mixer_channels["master"].get("mute_btn"), bool(val))
                    self._mixer_master_muted = bool(val)
                elif t.startswith("MICM="):
                    val = int(t.split("=", 1)[1] or "0")
                    if IS_WINDOWS:
                        self._win_set_endpoint_mute(self._win_get_mic_endpoint(), bool(val))
                    else:
                        self._pactl_set(["set-source-mute", "@DEFAULT_SOURCE@", "1" if val else "0"])
                    if has_ui:
                        self._update_mute_button(self._mixer_channels["mic"].get("mute_btn"), bool(val))
                    self._mixer_mic_muted = bool(val)
                elif t.startswith("APP1="):
                    val = int(t.split("=", 1)[1] or "0")
                    self._set_mixer_app_volume_slot(0, val)
                elif t.startswith("APP2="):
                    val = int(t.split("=", 1)[1] or "0")
                    self._set_mixer_app_volume_slot(1, val)
                elif t.startswith("APP1M="):
                    val = int(t.split("=", 1)[1] or "0")
                    self._set_mixer_app_mute_slot(0, bool(val))
                elif t.startswith("APP2M="):
                    val = int(t.split("=", 1)[1] or "0")
                    self._set_mixer_app_mute_slot(1, bool(val))
        except Exception as e:
            print(f"[Mixer] Command error: {e}")
        finally:
            self._mixer_updating = False

    def _on_mixer_app_changed(self, index: int, value: int) -> None:
        if self._mixer_updating:
            return
        if index < 0 or index >= len(self._mixer_app_slots):
            return
        slot = self._mixer_app_slots[index]
        slot_id = slot.get("id")
        if slot_id is None:
            return
        slot["value"].setText(f"{value}%")
        self._queue_mixer_volume(
            f"app{index}",
            value,
            lambda v, i=index: self._set_mixer_app_volume_slot(i, v),
        )


    def _save_state_now(self):
        try:
            data = {
                "current_profile": self.profile_manager.current_profile,
                "profiles": self.profile_manager.profiles,
                "app_flags": self._app_flags,
                "display_settings": self._display_settings,
            }
            self.state_manager.save_state(data)
            self.statusBar().showMessage("Stav uložený", 1200)
        except Exception as e:
            self.logger.exception("Save state error")
            self.statusBar().showMessage("Chyba pri ukladaní stavu", 4000)

    def _setup_data(self):
        self.logger = get_logger(__name__)
        self.state_manager = StateManager()
        self.profile_manager = ProfileManager()
        self.profile_manager.on_profile_loaded(self._apply_loaded_profile)
        self.selected_button_name: str | None = None
        self.grid_buttons: Dict[str, QPushButton] = {}
        self._grid_cell_to_anchor: Dict[str, str] = {}

        self._cmd_last_ts: Dict[str, float] = {}
        self._startup_t0 = time.monotonic()
        self._display_settings = self._default_display_settings()
        self._display_loading = False
        self._current_button_style = self._default_button_style()
        self._button_ui_loading = False
        self._button_autosave_timer: QTimer | None = None
        self._button_autosave_target: str | None = None
        self._confirm_risky_actions = os.environ.get(
            "MACROTOUCH_CONFIRM_RISKY_ACTIONS",
            "1",
        ).strip().lower() not in {"0", "false", "no", "off"}
        self._risky_action_confirm_until: Dict[str, float] = {}
        self._risky_action_grace_s = 1.5
        self._accent_color = "#60A5FA"
        self._icon_render_cache: dict[tuple[Any, ...], QIcon] = {}
        self._icon_render_cache_limit = 384
        self._icon_fit_cache: dict[tuple[Any, ...], QSize] = {}
        self._icon_fit_cache_limit = 512
        self._grid_bg_pixmap_cache: dict[tuple[Any, ...], QPixmap] = {}
        self._grid_bg_cache_limit = 64
        self._grid_frame_cache: dict[tuple[Any, ...], QPixmap] = {}
        self._grid_frame_cache_limit = 64
        self._serial_log_last_ts = 0.0
        self._serial_log_min_interval = 0.20
        self._last_non_grid_size: tuple[int, int] | None = None
        self.gridResizePreview: QFrame | None = None
        self._grid_resize_drag_btn: QPushButton | None = None
        self._grid_resize_drag_anchor: str | None = None
        self._grid_resize_drag_start_global: QPointF | None = None
        self._grid_resize_drag_start_span: tuple[int, int] = (1, 1)
        self._grid_resize_drag_candidate: tuple[int, int] = (1, 1)
        self._weather_lock = threading.Lock()
        self._weather_fetch_inflight = False
        self._weather_force_pending = False
        self._weather_cached_line = ""
        self._weather_cached_payload: dict[str, Any] = {}
        self._weather_last_sent_line = ""
        self._weather_geocode_cache: dict[str, tuple[float, float, str]] = {}
        self._weather_sync_interval_ms = 30 * 60 * 1000
        self._weather_marquee_state: dict[str, dict[str, int]] = {}
        self._weather_marquee_step = 0
        self._metric_widget_last_sent_line = ""
        self._gif_perf_dialog: GifPerfTestDialog | None = None

    def _default_display_settings(self) -> dict[str, Any]:
        return {
            "screensaver": {
                "enabled": True,
                "idle_ms": 60000,
                "bg_color": "#0A0F1C",
                "bg_image": "",
                "time_color": "#EAF2FF",
                "label_color": "#9FB3D9",
                "time_size": 4,
                "time_font": "Title",
                "label_size": 2,
                "label": "MacroTouch",
                "show_label": True,
            },
            "grid": {
                "bg_color": "#000000",
                "bg_image": "",
            },
            "buttons": {
                "bg_color": "#000000",
                "fg_color": "#FFFFFF",
                "bg_highlight": "#14181E",
                "fg_highlight": "#F0F0F0",
                "font": "",
                "text_size": 1.1,
                "icon_transparent": True,
            },
            "appearance": {
                "theme": "Dark",
                "accent": "#2563EB",
                "wallpaper_enabled": False,
                "wallpaper": "",
                "wallpaper_dim": 30,
            },
        }

    def _normalize_hex_color(self, value: str, fallback: str) -> str:
        text = (value or "").strip()
        if not text:
            return fallback
        if not text.startswith("#"):
            text = "#" + text
        if len(text) == 4:
            text = "#" + "".join(ch * 2 for ch in text[1:])
        if not re.match(r"^#[0-9A-Fa-f]{6}$", text):
            return fallback
        return text.upper()

    def _adjust_hex_color(self, value: str, lighter_pct: int) -> str:
        color = QColor(value)
        if not color.isValid():
            return value
        return color.lighter(lighter_pct).name().upper()

    def _darken_hex_color(self, value: str, darker_pct: int) -> str:
        color = QColor(value)
        if not color.isValid():
            return value
        return color.darker(darker_pct).name().upper()

    def _hex_to_rgba(self, value: str, alpha: float) -> str:
        color = QColor(value)
        if not color.isValid():
            return f"rgba(0, 0, 0, {alpha})"
        return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"

    def _merge_display_settings(self, raw: Any) -> dict[str, Any]:
        data = self._default_display_settings()
        if not isinstance(raw, dict):
            return data

        scr = raw.get("screensaver")
        if isinstance(scr, dict):
            data["screensaver"]["enabled"] = bool(scr.get("enabled", data["screensaver"]["enabled"]))
            try:
                idle_ms = int(scr.get("idle_ms", data["screensaver"]["idle_ms"]))
            except Exception:
                idle_ms = data["screensaver"]["idle_ms"]
            data["screensaver"]["idle_ms"] = max(5000, min(3600_000, idle_ms))

            try:
                time_size = int(scr.get("time_size", data["screensaver"]["time_size"]))
            except Exception:
                time_size = data["screensaver"]["time_size"]
            data["screensaver"]["time_size"] = max(1, min(8, time_size))

            time_font = str(scr.get("time_font", data["screensaver"]["time_font"]))
            if time_font in ("Mono", "Digital", "Default"):
                time_font = "Title"
            if time_font not in ("Default", "Title", "Body", "Meta"):
                time_font = data["screensaver"]["time_font"]
            data["screensaver"]["time_font"] = time_font

            try:
                label_size = int(scr.get("label_size", data["screensaver"]["label_size"]))
            except Exception:
                label_size = data["screensaver"]["label_size"]
            data["screensaver"]["label_size"] = max(1, min(6, label_size))

            label = scr.get("label", data["screensaver"]["label"])
            data["screensaver"]["label"] = str(label) if label is not None else ""
            data["screensaver"]["show_label"] = bool(scr.get("show_label", bool(data["screensaver"]["label"])))

            data["screensaver"]["bg_color"] = self._normalize_hex_color(
                str(scr.get("bg_color", data["screensaver"]["bg_color"])),
                data["screensaver"]["bg_color"],
            )
            bg_image = str(scr.get("bg_image", data["screensaver"].get("bg_image", "")) or "").strip()
            if bg_image and not os.path.isfile(bg_image):
                bg_image = ""
            data["screensaver"]["bg_image"] = bg_image
            data["screensaver"]["time_color"] = self._normalize_hex_color(
                str(scr.get("time_color", data["screensaver"]["time_color"])),
                data["screensaver"]["time_color"],
            )
            data["screensaver"]["label_color"] = self._normalize_hex_color(
                str(scr.get("label_color", data["screensaver"]["label_color"])),
                data["screensaver"]["label_color"],
            )

        grid = raw.get("grid")
        if isinstance(grid, dict):
            data["grid"]["bg_color"] = self._normalize_hex_color(
                str(grid.get("bg_color", data["grid"]["bg_color"])),
                data["grid"]["bg_color"],
            )
            grid_bg_image = str(grid.get("bg_image", data["grid"].get("bg_image", "")) or "").strip()
            if grid_bg_image and not os.path.isfile(grid_bg_image):
                grid_bg_image = ""
            data["grid"]["bg_image"] = grid_bg_image

        btn = raw.get("buttons")
        if isinstance(btn, dict):
            data["buttons"]["bg_color"] = self._normalize_hex_color(
                str(btn.get("bg_color", data["buttons"]["bg_color"])),
                data["buttons"]["bg_color"],
            )
            data["buttons"]["fg_color"] = self._normalize_hex_color(
                str(btn.get("fg_color", data["buttons"]["fg_color"])),
                data["buttons"]["fg_color"],
            )
            data["buttons"]["bg_highlight"] = self._normalize_hex_color(
                str(btn.get("bg_highlight", data["buttons"]["bg_highlight"])),
                data["buttons"]["bg_highlight"],
            )
            data["buttons"]["fg_highlight"] = self._normalize_hex_color(
                str(btn.get("fg_highlight", data["buttons"]["fg_highlight"])),
                data["buttons"]["fg_highlight"],
            )
            data["buttons"]["font"] = str(btn.get("font", data["buttons"]["font"]) or "")
            try:
                text_size = float(btn.get("text_size", data["buttons"]["text_size"]))
            except Exception:
                text_size = data["buttons"]["text_size"]
            data["buttons"]["text_size"] = max(0.6, min(2.5, text_size))
            data["buttons"]["icon_transparent"] = bool(
                btn.get("icon_transparent", data["buttons"].get("icon_transparent", True))
            )

        app = raw.get("appearance")
        if isinstance(app, dict):
            theme = str(app.get("theme", data["appearance"]["theme"]))
            if theme not in ("Dark", "OLED", "Light"):
                theme = data["appearance"]["theme"]
            data["appearance"]["theme"] = theme

            data["appearance"]["accent"] = self._normalize_hex_color(
                str(app.get("accent", data["appearance"]["accent"])),
                data["appearance"]["accent"],
            )
            data["appearance"]["wallpaper_enabled"] = bool(
                app.get("wallpaper_enabled", data["appearance"]["wallpaper_enabled"])
            )
            data["appearance"]["wallpaper"] = str(app.get("wallpaper", data["appearance"]["wallpaper"])) if app.get("wallpaper") is not None else ""
            try:
                dim = int(app.get("wallpaper_dim", data["appearance"]["wallpaper_dim"]))
            except Exception:
                dim = data["appearance"]["wallpaper_dim"]
            data["appearance"]["wallpaper_dim"] = max(0, min(80, dim))

        return data

    def _pick_color_for(self, line_edit: QLineEdit) -> None:
        if line_edit is None:
            return
        current = self._normalize_hex_color(line_edit.text(), "#000000")
        color = QColor(current)
        picked = QColorDialog.getColor(color, self, "Pick color")
        if not picked.isValid():
            return
        line_edit.setText(picked.name().upper())
        self._on_display_setting_changed()

    def _update_wallpaper_controls(self) -> None:
        enabled = bool(self.chkWallpaperEnable.isChecked()) if hasattr(self, "chkWallpaperEnable") and self.chkWallpaperEnable else False
        if self.lineWallpaperPath:
            self.lineWallpaperPath.setEnabled(enabled)
        if self.btnWallpaperBrowse:
            self.btnWallpaperBrowse.setEnabled(enabled)
        if self.spinWallpaperDim:
            self.spinWallpaperDim.setEnabled(enabled)

    def choose_wallpaper(self) -> None:
        if not self.chkWallpaperEnable or not self.chkWallpaperEnable.isChecked():
            return
        filters = "Images (*.png *.jpg *.jpeg *.bmp *.webp);;All files (*.*)"
        start_dir = str(Path.home())
        if self.lineWallpaperPath and self.lineWallpaperPath.text().strip():
            start_dir = self._path_dialog_start_dir(self.lineWallpaperPath.text().strip())
        path, _ = QFileDialog.getOpenFileName(self, "Vyber tapetu", start_dir, filters)
        if not path:
            return
        path = os.path.expanduser(os.path.expandvars(path))
        self.lineWallpaperPath.setText(path)
        self._on_display_setting_changed()

    def _choose_image_for(self, line_edit: QLineEdit | None) -> None:
        if not line_edit:
            return
        filters = "Images (*.png *.jpg *.jpeg *.bmp *.webp);;All files (*.*)"
        start_dir = str(Path.home())
        if line_edit.text().strip():
            start_dir = self._path_dialog_start_dir(line_edit.text().strip())
        path, _ = QFileDialog.getOpenFileName(self, "Vyber obrázok", start_dir, filters)
        if not path:
            return
        path = os.path.expanduser(os.path.expandvars(path))
        line_edit.setText(path)
        self._on_display_setting_changed()

    def _apply_display_settings_to_ui(self) -> None:
        if not hasattr(self, "chkScrEnable"):
            return
        self._display_loading = True
        try:
            scr = self._display_settings.get("screensaver", {})
            grid = self._display_settings.get("grid", {})
            btn = self._display_settings.get("buttons", {})
            app = self._display_settings.get("appearance", {})

            self.chkScrEnable.setChecked(bool(scr.get("enabled", True)))
            idle_s = int(scr.get("idle_ms", 60000)) // 1000
            self.spinScrIdle.setValue(max(5, min(3600, idle_s)))
            self.spinScrTimeSize.setValue(int(scr.get("time_size", 3)))
            if self.comboScrTimeFont:
                time_font = str(scr.get("time_font", "Title"))
                if time_font in ("Mono", "Digital", "Default"):
                    time_font = "Title"
                self._set_combo_safe(self.comboScrTimeFont, time_font, "Title")
            self.lineScrTimeColor.setText(str(scr.get("time_color", "#F0F0F0")))
            self.lineScrBgColor.setText(str(scr.get("bg_color", "#080C12")))
            if hasattr(self, "lineScrBgImage") and self.lineScrBgImage:
                self.lineScrBgImage.setText(str(scr.get("bg_image", "")))
            self.lineScrLabelText.setText(str(scr.get("label", "MacroTouch")))
            self.spinScrLabelSize.setValue(int(scr.get("label_size", 1)))
            self.lineScrLabelColor.setText(str(scr.get("label_color", "#788296")))
            if hasattr(self, "lineGridBgColor") and self.lineGridBgColor:
                self.lineGridBgColor.setText(str(grid.get("bg_color", "#000000")))
            if hasattr(self, "lineGridBgImage") and self.lineGridBgImage:
                self.lineGridBgImage.setText(str(grid.get("bg_image", "")))
            if hasattr(self, "lineBtnBgColor") and self.lineBtnBgColor:
                self.lineBtnBgColor.setText(str(btn.get("bg_color", "#000000")))
            if hasattr(self, "lineBtnFgColor") and self.lineBtnFgColor:
                self.lineBtnFgColor.setText(str(btn.get("fg_color", "#FFFFFF")))
            if hasattr(self, "chkIconTransparency") and self.chkIconTransparency:
                self.chkIconTransparency.setChecked(bool(btn.get("icon_transparent", True)))

            if self.comboTheme:
                theme = str(app.get("theme", "Dark"))
                idx = self.comboTheme.findText(theme)
                self.comboTheme.setCurrentIndex(idx if idx >= 0 else 0)
            if self.lineAccentColor:
                self.lineAccentColor.setText(str(app.get("accent", "#2563EB")))
            if self.chkWallpaperEnable:
                self.chkWallpaperEnable.setChecked(bool(app.get("wallpaper_enabled", False)))
            if self.lineWallpaperPath:
                self.lineWallpaperPath.setText(str(app.get("wallpaper", "")))
            if self.spinWallpaperDim:
                self.spinWallpaperDim.setValue(int(app.get("wallpaper_dim", 30)))

        finally:
            self._display_loading = False
        self._apply_grid_background_style()
        self._update_wallpaper_controls()

    def _set_line_text_if_changed(self, line: QLineEdit, value: str) -> None:
        if line is None:
            return
        if line.text().strip() == value:
            return
        line.blockSignals(True)
        line.setText(value)
        line.blockSignals(False)

    def _read_display_settings_from_ui(self) -> dict[str, Any]:
        data = self._default_display_settings()
        # zachovaj existujúce button defaulty (už nie sú v UI)
        current_buttons = self._display_settings.get("buttons")
        if isinstance(current_buttons, dict):
            data["buttons"] = dict(current_buttons)
            data["buttons"].pop("force_default", None)
        if not hasattr(self, "chkScrEnable"):
            return data

        scr = data["screensaver"]
        grid = data["grid"]
        btn = data["buttons"]
        app = data["appearance"]

        scr["enabled"] = bool(self.chkScrEnable.isChecked())
        scr["idle_ms"] = int(self.spinScrIdle.value()) * 1000
        scr["time_size"] = int(self.spinScrTimeSize.value())
        if self.comboScrTimeFont:
            font_choice = (self.comboScrTimeFont.currentText() or "").strip()
            if font_choice not in ("Default", "Title", "Body", "Meta"):
                font_choice = scr.get("time_font", "Default")
            scr["time_font"] = font_choice
        scr["label_size"] = int(self.spinScrLabelSize.value())
        scr["label"] = self.lineScrLabelText.text().strip()
        scr["show_label"] = bool(scr["label"])

        scr["time_color"] = self._normalize_hex_color(self.lineScrTimeColor.text(), scr["time_color"])
        scr["bg_color"] = self._normalize_hex_color(self.lineScrBgColor.text(), scr["bg_color"])
        if hasattr(self, "lineScrBgImage") and self.lineScrBgImage:
            raw = self.lineScrBgImage.text().strip()
            scr["bg_image"] = os.path.expanduser(os.path.expandvars(raw)) if raw else ""
        scr["label_color"] = self._normalize_hex_color(self.lineScrLabelColor.text(), scr["label_color"])

        if hasattr(self, "lineGridBgColor") and self.lineGridBgColor:
            grid["bg_color"] = self._normalize_hex_color(self.lineGridBgColor.text(), grid["bg_color"])
        if hasattr(self, "lineGridBgImage") and self.lineGridBgImage:
            raw = self.lineGridBgImage.text().strip()
            grid["bg_image"] = os.path.expanduser(os.path.expandvars(raw)) if raw else ""

        if hasattr(self, "lineBtnBgColor") and self.lineBtnBgColor:
            btn["bg_color"] = self._normalize_hex_color(self.lineBtnBgColor.text(), btn["bg_color"])
        if hasattr(self, "lineBtnFgColor") and self.lineBtnFgColor:
            btn["fg_color"] = self._normalize_hex_color(self.lineBtnFgColor.text(), btn["fg_color"])
        if hasattr(self, "chkIconTransparency") and self.chkIconTransparency:
            btn["icon_transparent"] = bool(self.chkIconTransparency.isChecked())
        # auto highlight from default colors
        btn["bg_highlight"] = self._adjust_hex_color(btn["bg_color"], 115)
        btn["fg_highlight"] = btn["fg_color"]

        if self.comboTheme:
            theme = (self.comboTheme.currentText() or "").strip()
            if theme not in ("Dark", "OLED", "Light"):
                theme = app["theme"]
            app["theme"] = theme
        if self.lineAccentColor:
            app["accent"] = self._normalize_hex_color(self.lineAccentColor.text(), app["accent"])
        if self.chkWallpaperEnable:
            app["wallpaper_enabled"] = bool(self.chkWallpaperEnable.isChecked())
        if self.lineWallpaperPath:
            app["wallpaper"] = (self.lineWallpaperPath.text() or "").strip()
        if self.spinWallpaperDim:
            app["wallpaper_dim"] = int(self.spinWallpaperDim.value())

        self._set_line_text_if_changed(self.lineScrTimeColor, scr["time_color"])
        self._set_line_text_if_changed(self.lineScrBgColor, scr["bg_color"])
        self._set_line_text_if_changed(self.lineScrLabelColor, scr["label_color"])
        if hasattr(self, "lineGridBgColor") and self.lineGridBgColor:
            self._set_line_text_if_changed(self.lineGridBgColor, grid["bg_color"])
        if hasattr(self, "lineBtnBgColor") and self.lineBtnBgColor:
            self._set_line_text_if_changed(self.lineBtnBgColor, btn["bg_color"])
        if hasattr(self, "lineBtnFgColor") and self.lineBtnFgColor:
            self._set_line_text_if_changed(self.lineBtnFgColor, btn["fg_color"])
        if self.lineAccentColor:
            self._set_line_text_if_changed(self.lineAccentColor, app["accent"])
        if self.lineWallpaperPath:
            self._set_line_text_if_changed(self.lineWallpaperPath, app["wallpaper"])

        return data

    def _on_display_setting_changed(self) -> None:
        if self._booting or self._display_loading:
            return
        prev_theme = str(self._display_settings.get("appearance", {}).get("theme", "Dark"))
        self._display_settings = self._read_display_settings_from_ui()
        new_theme = str(self._display_settings.get("appearance", {}).get("theme", "Dark"))
        if new_theme == "Light" and prev_theme != "Light":
            self._ensure_light_grid_bg(update_ui=True)
        self._request_save()
        self._apply_grid_background_style()
        self._refresh_grid_icons()
        self._refresh_grid_button_styles()
        self._update_wallpaper_controls()
        self._apply_app_theme_from_settings()

    def _ensure_light_grid_bg(self, update_ui: bool = False) -> bool:
        app = self._display_settings.get("appearance", {})
        if str(app.get("theme", "Dark")) != "Light":
            return False
        grid = self._display_settings.get("grid", {})
        bg = str(grid.get("bg_color", "#000000")).upper()
        if bg != "#000000":
            return False
        grid["bg_color"] = "#FFFFFF"
        if update_ui and hasattr(self, "lineGridBgColor") and self.lineGridBgColor:
            self._set_line_text_if_changed(self.lineGridBgColor, "#FFFFFF")
        return True

    def _default_button_style(self) -> dict[str, Any]:
        btn = self._display_settings.get("buttons", {})
        return {
            "bg_color": btn.get("bg_color", "#000000"),
            "fg_color": btn.get("fg_color", "#FFFFFF"),
            "font": str(btn.get("font", "") or ""),
            "text_size": float(btn.get("text_size", 1.1)),
        }

    def _load_button_style_for_selected(self, btn_data: dict[str, Any]) -> None:
        defaults = self._default_button_style()
        style = btn_data.get("style", {}) if isinstance(btn_data, dict) else {}

        bg = self._normalize_hex_color(str(style.get("bg_color", defaults["bg_color"])), defaults["bg_color"])
        fg = self._normalize_hex_color(str(style.get("fg_color", defaults["fg_color"])), defaults["fg_color"])
        font = str(style.get("font", defaults.get("font", "")) or "")
        try:
            size = float(style.get("text_size", defaults["text_size"]))
        except Exception:
            size = float(defaults["text_size"])
        size = max(0.6, min(2.5, size))

        self._current_button_style = {"bg_color": bg, "fg_color": fg, "text_size": size, "font": font}

    def _button_style_for_data(self, btn_data: dict[str, Any]) -> dict[str, Any]:
        defaults = self._default_button_style()
        style = btn_data.get("style", {}) if isinstance(btn_data, dict) else {}
        bg = self._normalize_hex_color(str(style.get("bg_color", defaults["bg_color"])), defaults["bg_color"])
        fg = self._normalize_hex_color(str(style.get("fg_color", defaults["fg_color"])), defaults["fg_color"])
        font = str(style.get("font", defaults.get("font", "")) or "")
        try:
            size = float(style.get("text_size", defaults["text_size"]))
        except Exception:
            size = float(defaults["text_size"])
        size = max(0.6, min(2.5, size))
        return {"bg_color": bg, "fg_color": fg, "text_size": size, "font": font}

    def _weather_estimated_widget_size(self, btn_data: dict[str, Any]) -> tuple[int, int]:
        cur = self.profile_manager.current_profile if hasattr(self, "profile_manager") else None
        prof = self.profile_manager.profiles.get(cur, {}) if cur else {}
        rows = max(1, min(4, int(prof.get("rows", 3)))) if isinstance(prof, dict) else 3
        cols = max(1, min(4, int(prof.get("cols", 4)))) if isinstance(prof, dict) else 4
        cell_w, cell_h = self._compute_cell_size(rows, cols)
        gap = int(self._grid_settings().get("gap", 8))
        try:
            span_rows = max(1, int(btn_data.get("span_rows", 1) or 1))
        except Exception:
            span_rows = 1
        try:
            span_cols = max(1, int(btn_data.get("span_cols", 1) or 1))
        except Exception:
            span_cols = 1
        w = cell_w * span_cols + gap * (span_cols - 1)
        h = cell_h * span_rows + gap * (span_rows - 1)
        return max(32, int(w)), max(32, int(h))

    def _weather_layout_metrics(self, btn: QPushButton | None, btn_data: dict[str, Any]) -> dict[str, Any]:
        if isinstance(btn, QPushButton):
            w = max(32, int(btn.width()))
            h = max(32, int(btn.height()))
        else:
            w, h = self._weather_estimated_widget_size(btn_data)

        area = w * h
        vertical = h >= int(w * 1.22)
        short_h = h < 96
        tiny_square = (w <= 152 and h <= 152 and area <= 22_000)

        if w <= 118 or h <= 62 or area <= 15_000 or tiny_square:
            mode = "micro"
        elif vertical and w < 156:
            mode = "narrow"
        elif short_h:
            mode = "row"
        elif w < 190 or area < 19000:
            mode = "compact"
        else:
            mode = "full"

        show_icon = mode in {"row", "compact", "full"} and w >= 148
        if mode == "row":
            icon_side = max(18, min(30, int(h * 0.42)))
            pad = (6, 10, 6, 10)  # top,right,bottom,left
            font_pt = 8.6
            bold = True
        elif mode == "compact":
            icon_side = max(20, min(34, int(min(w, h) * 0.25)))
            pad = (8, 11, 8, 11)
            font_pt = 9.2
            bold = True
        elif mode == "full":
            icon_side = max(24, min(42, int(min(w, h) * 0.28)))
            pad = (9, 12, 10, 12)
            font_pt = 10.2
            bold = True
        elif mode == "narrow":
            show_icon = w >= 110
            icon_side = max(16, min(24, int(min(w, h) * 0.20))) if show_icon else 0
            pad = (8, 8, 8, 8)
            font_pt = 8.6
            bold = True
        else:  # micro
            show_icon = w >= 98 and h >= 98
            icon_side = max(15, min(22, int(min(w, h) * 0.22))) if show_icon else 0
            pad = (6, 6, 6, 6)
            font_pt = 9.4
            bold = True

        text_w = w - pad[1] - pad[3] - ((icon_side + 10) if show_icon else 0)
        if mode == "micro":
            line_chars = max(6, int(text_w / (8.8 if show_icon else 8.0)))
        else:
            line_chars = max(9, int(text_w / 7.2))

        return {
            "mode": mode,
            "show_icon": show_icon,
            "icon_side": icon_side,
            "pad_top": pad[0],
            "pad_right": pad[1],
            "pad_bottom": pad[2],
            "pad_left": pad[3],
            "line_chars": line_chars,
            "font_pt": font_pt,
            "bold": bold,
            "text_align": "left" if show_icon else ("center" if mode == "micro" else "left"),
        }

    def _metric_layout_metrics(self, btn: QPushButton | None, btn_data: dict[str, Any]) -> dict[str, Any]:
        if isinstance(btn, QPushButton):
            w = max(32, int(btn.width()))
            h = max(32, int(btn.height()))
        else:
            w, h = self._weather_estimated_widget_size(btn_data)

        area = w * h
        vertical = h >= int(w * 1.20)
        short_h = h < 94
        tiny_square = (w <= 152 and h <= 152 and area <= 22_000)

        if w <= 118 or h <= 62 or area <= 15_000 or tiny_square:
            mode = "micro"
        elif vertical and w < 156:
            mode = "narrow"
        elif short_h:
            mode = "row"
        elif w < 186 or area < 19_000:
            mode = "compact"
        else:
            mode = "full"

        show_icon = mode in {"row", "compact", "full"} and w >= 136
        if mode == "row":
            icon_side = max(16, min(26, int(h * 0.34)))
            pad = (6, 10, 6, 10)
            font_pt = 9.0
            bold = True
        elif mode == "compact":
            icon_side = max(18, min(30, int(min(w, h) * 0.23)))
            pad = (8, 11, 8, 11)
            font_pt = 9.5
            bold = True
        elif mode == "full":
            icon_side = max(20, min(34, int(min(w, h) * 0.25)))
            pad = (9, 12, 10, 12)
            font_pt = 10.4
            bold = True
        elif mode == "narrow":
            show_icon = w >= 112
            icon_side = max(15, min(22, int(min(w, h) * 0.18))) if show_icon else 0
            pad = (8, 8, 8, 8)
            font_pt = 8.8
            bold = True
        else:  # micro
            show_icon = w >= 104 and h >= 88
            icon_side = max(14, min(20, int(min(w, h) * 0.20))) if show_icon else 0
            pad = (6, 6, 6, 6)
            font_pt = 9.6
            bold = True

        text_w = w - pad[1] - pad[3] - ((icon_side + 8) if show_icon else 0)
        if mode == "micro":
            line_chars = max(6, int(text_w / (8.6 if show_icon else 8.0)))
        else:
            line_chars = max(9, int(text_w / 7.2))

        return {
            "mode": mode,
            "show_icon": show_icon,
            "icon_side": icon_side,
            "pad_top": pad[0],
            "pad_right": pad[1],
            "pad_bottom": pad[2],
            "pad_left": pad[3],
            "line_chars": line_chars,
            "font_pt": font_pt,
            "bold": bold,
            "text_align": "left" if show_icon else ("center" if mode == "micro" else "left"),
        }

    def _normalize_metric_key(self, raw: Any) -> str:
        text = str(raw or "").strip().upper()
        if not text:
            return ""
        text = re.sub(r"[^A-Z0-9]+", "_", text).strip("_")
        return METRIC_WIDGET_KEY_ALIASES.get(text, "")

    def _metric_palette_for_key(self, key: str) -> tuple[str, str, str]:
        palettes = {
            "CPU": ("#1F2937", "#334155", "#60A5FA"),
            "RAM": ("#273449", "#344A66", "#22D3EE"),
            "GPU": ("#1F3A2C", "#2B5A43", "#34D399"),
            "GPU_TEMP": ("#3A1F2A", "#5A3040", "#FB7185"),
            "FPS": ("#3A2D17", "#5B431D", "#FBBF24"),
            "NET": ("#1E3A4A", "#2E5B71", "#38BDF8"),
            "DISK": ("#2A2A3A", "#3D3D59", "#A78BFA"),
            "CPU_GHZ": ("#22302A", "#2F443A", "#4ADE80"),
        }
        return palettes.get(key, palettes["CPU"])

    def _metric_icon_label(self, key: str) -> str:
        labels = {
            "CPU": "CPU",
            "RAM": "RAM",
            "GPU": "GPU",
            "GPU_TEMP": "TMP",
            "FPS": "FPS",
            "NET": "NET",
            "DISK": "IO",
            "CPU_GHZ": "GHz",
        }
        return labels.get(key, key or "M")

    def _metric_values_from_stats(self, stats: dict[str, Any] | None = None) -> dict[str, float | None]:
        source = stats if isinstance(stats, dict) else self._last_stats
        if not isinstance(source, dict):
            source = {}

        def _num(name: str) -> float | None:
            raw = source.get(name)
            if isinstance(raw, (int, float)):
                return float(raw)
            return None

        vals: dict[str, float | None] = {
            "CPU": _num("cpu_percent"),
            "RAM": _num("ram_percent"),
            "GPU": _num("gpu_percent"),
            "GPU_TEMP": _num("gpu_temp"),
            "FPS": _num("fps"),
            "NET": _num("net_mb_s"),
            "DISK": _num("disk_mb_s"),
            "CPU_GHZ": _num("cpu_ghz"),
        }

        if vals["CPU"] is not None:
            vals["CPU"] = max(0.0, min(100.0, vals["CPU"]))
        if vals["RAM"] is not None:
            vals["RAM"] = max(0.0, min(100.0, vals["RAM"]))
        if vals["GPU"] is not None:
            vals["GPU"] = max(0.0, min(100.0, vals["GPU"]))
        if vals["NET"] is not None:
            vals["NET"] = max(0.0, vals["NET"])
        if vals["DISK"] is not None:
            vals["DISK"] = max(0.0, vals["DISK"])
        return vals

    def _format_metric_value(self, key: str, value: float | None) -> str:
        if value is None:
            return "--"
        if key in {"CPU", "RAM", "GPU"}:
            return f"{value:.0f}%"
        if key == "GPU_TEMP":
            return f"{value:.0f} C"
        if key == "FPS":
            return f"{max(0.0, value):.0f}"
        if key in {"NET", "DISK"}:
            if value < 1.0:
                return f"{value * 1024.0:.0f} KB/s"
            return f"{value:.1f} MB/s"
        if key == "CPU_GHZ":
            return f"{value:.2f} GHz"
        return f"{value:.1f}"

    def _metric_secondary_text(self, key: str, value: float | None) -> str:
        if value is None:
            return "No data"
        if key in {"CPU", "RAM", "GPU"}:
            return "Usage"
        if key == "GPU_TEMP":
            return "Thermal"
        if key == "FPS":
            return "Frames/s"
        if key == "NET":
            return "Throughput"
        if key == "DISK":
            return "Read+Write"
        if key == "CPU_GHZ":
            return "Frequency"
        return "Metric"

    def _make_metric_icon(self, key: str, size: int, accent_hex: str) -> QIcon:
        side = max(16, min(96, int(size)))
        cache_key = ("metric-icon", key, side, accent_hex)
        cached = self._icon_render_cache.get(cache_key)
        if cached is not None:
            return cached

        pm = QPixmap(side, side)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        accent = QColor(accent_hex)
        border = QColor(accent)
        border.setAlpha(220)
        fill = QColor(accent)
        fill.setAlpha(76)
        p.setPen(QPen(border, max(1, side // 18)))
        p.setBrush(fill)
        rad = max(4, side // 4)
        p.drawRoundedRect(QRectF(1, 1, side - 2, side - 2), rad, rad)

        font = p.font()
        font.setBold(True)
        font.setPointSizeF(max(6.5, min(14.0, side * 0.28)))
        p.setFont(font)
        p.setPen(QColor("#F8FAFC"))
        p.drawText(QRectF(0, 0, side, side), Qt.AlignmentFlag.AlignCenter, self._metric_icon_label(key))
        p.end()

        icon = QIcon(pm)
        self._cache_put(self._icon_render_cache, cache_key, icon, self._icon_render_cache_limit)
        return icon

    def _metric_preview_text_for_button(
        self,
        btn_data: dict[str, Any],
        btn: QPushButton | None = None,
        btn_name: str | None = None,
    ) -> str:
        action_key = self._normalize_action(str(btn_data.get("action", "") or ""))
        base_name = str(btn_data.get("name", "") or "").strip()
        if action_key != "MetricWidget":
            return base_name

        metrics = self._metric_layout_metrics(btn, btn_data)
        line_chars = int(metrics.get("line_chars", 14))
        mode = str(metrics.get("mode", "compact"))
        try:
            spec = self._parse_metric_widget_spec(str(btn_data.get("path", "") or ""))
        except Exception:
            spec = {"key": "CPU", "label": "CPU load"}

        key = str(spec.get("key", "CPU") or "CPU")
        label = self._weather_clean_field(spec.get("label", METRIC_WIDGET_DEFAULT_LABELS.get(key, key)), max_len=20)
        if not label:
            label = METRIC_WIDGET_DEFAULT_LABELS.get(key, key)
        value = self._metric_values_from_stats().get(key)
        value_text = self._format_metric_value(key, value)
        sub_text = self._metric_secondary_text(key, value)

        def fit(text: str, width: int) -> str:
            return self._fit_weather_text_line(text, max(6, width))

        if mode == "micro":
            return f"{fit(value_text, line_chars)}\n{fit(label, line_chars)}"
        if mode == "narrow":
            return f"{fit(label, line_chars)}\n{fit(value_text, line_chars)}"
        if mode == "row":
            return f"{fit(label, line_chars)}\n{fit(value_text, line_chars)}"
        if mode == "compact":
            return f"{fit(label, line_chars)}\n{fit(value_text, line_chars)}\n{fit(sub_text, line_chars)}"
        return f"{fit(label, line_chars)}\n{fit(value_text, line_chars)}\n{fit(sub_text, line_chars)}"

    def _apply_metric_button_style(
        self,
        btn: QPushButton,
        btn_data: dict[str, Any],
        selected: bool,
        hovered: bool,
        locked: bool,
        accent: str,
    ) -> dict[str, Any]:
        metrics = self._metric_layout_metrics(btn, btn_data)
        try:
            spec = self._parse_metric_widget_spec(str(btn_data.get("path", "") or ""))
        except Exception:
            spec = {"key": "CPU", "label": "CPU load"}
        key = str(spec.get("key", "CPU") or "CPU")
        label = self._weather_clean_field(spec.get("label", METRIC_WIDGET_DEFAULT_LABELS.get(key, key)), max_len=20)
        value = self._metric_values_from_stats().get(key)
        value_text = self._format_metric_value(key, value)

        top, bottom, accent_icon = self._metric_palette_for_key(key)
        if locked:
            top = "#2E3238"
            bottom = "#3B4048"
            fg = "#9CA3AF"
            border_color = "#505762"
            border_width = 1
        else:
            fg = "#F8FAFC"
            if hovered and not selected:
                top = self._adjust_hex_color(top, 108)
                bottom = self._adjust_hex_color(bottom, 108)
            border_color = accent if selected else self._adjust_hex_color(top, 128)
            border_width = 2 if selected else 1

        pressed_top = self._darken_hex_color(top, 112) if not locked else top
        pressed_bottom = self._darken_hex_color(bottom, 112) if not locked else bottom

        btn.setStyleSheet(
            "QPushButton {"
            f" background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {top}, stop:1 {bottom});"
            f" color: {fg};"
            f" border: {border_width}px solid {border_color};"
            " border-radius: 22px;"
            f" text-align: {str(metrics.get('text_align', 'left'))};"
            f" padding: {int(metrics['pad_top'])}px {int(metrics['pad_right'])}px {int(metrics['pad_bottom'])}px {int(metrics['pad_left'])}px;"
            "}"
            "QPushButton:pressed {"
            f" background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {pressed_top}, stop:1 {pressed_bottom});"
            f" border: {border_width}px solid {border_color};"
            "}"
        )

        if bool(metrics.get("show_icon", False)):
            side = max(14, int(metrics.get("icon_side", 20)))
            btn.setIcon(self._make_metric_icon(key, side, accent_icon))
            btn.setIconSize(QSize(side, side))
        else:
            btn.setIcon(QIcon())

        btn.setText(self._metric_preview_text_for_button(btn_data, btn=btn, btn_name=btn.objectName()))
        if label:
            btn.setToolTip(f"{label}: {value_text}")
        else:
            btn.setToolTip(f"Metric: {value_text}")
        return metrics

    def _apply_weather_button_style(
        self,
        btn: QPushButton,
        btn_data: dict[str, Any],
        selected: bool,
        hovered: bool,
        locked: bool,
        accent: str,
    ) -> dict[str, Any]:
        metrics = self._weather_layout_metrics(btn, btn_data)
        payload = self._weather_payload_snapshot()
        category = self._weather_category_from_code(payload.get("code", 0))
        top, bottom, accent_icon = self._weather_palette_for_category(category)

        if locked:
            top = "#2E3238"
            bottom = "#3B4048"
            fg = "#9CA3AF"
            border_color = "#505762"
            border_width = 1
        else:
            fg = "#F8FAFC"
            if hovered and not selected:
                top = self._adjust_hex_color(top, 108)
                bottom = self._adjust_hex_color(bottom, 108)
            border_color = accent if selected else self._adjust_hex_color(top, 128)
            border_width = 2 if selected else 1

        pressed_top = self._darken_hex_color(top, 112) if not locked else top
        pressed_bottom = self._darken_hex_color(bottom, 112) if not locked else bottom

        btn.setStyleSheet(
            "QPushButton {"
            f" background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {top}, stop:1 {bottom});"
            f" color: {fg};"
            f" border: {border_width}px solid {border_color};"
            " border-radius: 22px;"
            f" text-align: {str(metrics.get('text_align', 'left'))};"
            f" padding: {int(metrics['pad_top'])}px {int(metrics['pad_right'])}px {int(metrics['pad_bottom'])}px {int(metrics['pad_left'])}px;"
            "}"
            "QPushButton:pressed {"
            f" background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {pressed_top}, stop:1 {pressed_bottom});"
            f" border: {border_width}px solid {border_color};"
            "}"
        )

        if bool(metrics.get("show_icon", False)):
            side = max(16, int(metrics.get("icon_side", 24)))
            btn.setIcon(self._make_weather_icon(category, side, accent_icon))
            btn.setIconSize(QSize(side, side))
        else:
            btn.setIcon(QIcon())
        btn.setText(self._weather_preview_text_for_button(btn_data, btn=btn, btn_name=btn.objectName()))

        tooltip = self._weather_clean_field(payload.get("desc", ""), max_len=64)
        if tooltip:
            btn.setToolTip(f"Weather: {tooltip}")
        else:
            btn.setToolTip("Weather widget")
        return metrics

    def _apply_grid_button_style(self, btn: QPushButton, btn_data: dict[str, Any]) -> None:
        if btn is None:
            return
        style = self._button_style_for_data(btn_data)
        bg = style.get("bg_color", "#000000")
        fg = style.get("fg_color", "#FFFFFF")
        font_name = str(style.get("font", "") or "")
        size = float(style.get("text_size", 1.1))
        selected = bool(btn.property("selected"))
        hovered = bool(btn.property("hovered"))
        locked = not btn.isEnabledTo(self)
        accent = getattr(self, "_accent_color", "#60a5fa")
        action_key = self._normalize_action(str(btn_data.get("action", "") or ""))

        if action_key == "WeatherWidget":
            metrics = self._apply_weather_button_style(btn, btn_data, selected, hovered, locked, accent)
            try:
                font = btn.font()
                if font_name:
                    font.setFamily(font_name)
                else:
                    font.setFamily(self.font().family())
                user_scale = max(0.85, min(1.35, float(style.get("text_size", 1.1)) / 1.1))
                base_size = float(metrics.get("font_pt", 9.2))
                font.setPointSizeF(max(7.2, min(13.0, base_size * user_scale)))
                font.setBold(bool(metrics.get("bold", True)))
                btn.setFont(font)
            except Exception:
                pass
            self._ensure_button_shadow(btn, locked)
            return

        if action_key == "MetricWidget":
            metrics = self._apply_metric_button_style(btn, btn_data, selected, hovered, locked, accent)
            try:
                font = btn.font()
                if font_name:
                    font.setFamily(font_name)
                else:
                    font.setFamily(self.font().family())
                user_scale = max(0.85, min(1.35, float(style.get("text_size", 1.1)) / 1.1))
                base_size = float(metrics.get("font_pt", 9.4))
                font.setPointSizeF(max(7.0, min(13.0, base_size * user_scale)))
                font.setBold(bool(metrics.get("bold", True)))
                btn.setFont(font)
            except Exception:
                pass
            self._ensure_button_shadow(btn, locked)
            return

        if locked:
            bg_effective = "#2E3238"
            fg = "#9CA3AF"
            border_color = "#3F444B"
            border_width = 1
            shadow = False
        else:
            bg_effective = bg
            if hovered and not selected:
                bg_effective = self._adjust_hex_color(bg, 112)
            if selected:
                bg_effective = self._adjust_hex_color(accent, 120)
                fg = "#FFFFFF"
            border_color = accent if selected else "#3a3d45"
            border_width = 2 if selected else 1
            shadow = True

        pressed_bg = self._darken_hex_color(bg_effective, 114) if not locked else bg_effective

        btn.setStyleSheet(
            "QPushButton {"
            f" background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {bg_effective}, stop:1 {self._adjust_hex_color(bg_effective, 90)});"
            f" color: {fg};"
            f" border: {border_width}px solid {border_color};"
            " border-radius: 18px;"
            " padding: 6px;"
            " text-align: center;"
            "}"
            "QPushButton:hover {"
            f" background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {self._adjust_hex_color(bg_effective, 108)}, stop:1 {self._adjust_hex_color(bg_effective, 94)});"
            "}"
            "QPushButton:pressed {"
            f" background-color: {pressed_bg};"
            f" border: {border_width}px solid {self._darken_hex_color(border_color, 105)};"
            "}"
        )

        try:
            font = btn.font()
            if font_name:
                font.setFamily(font_name)
            else:
                font.setFamily(self.font().family())
            font.setPointSizeF(max(7.0, min(16.0, 12.0 * size)))
            font.setBold(True)
            btn.setFont(font)
        except Exception:
            pass

        self._ensure_button_shadow(btn, locked)

    def _ensure_button_shadow(self, btn: QPushButton, disabled: bool) -> None:
        if disabled:
            if btn.graphicsEffect() is not None:
                btn.setGraphicsEffect(None)
            return

        existing = btn.graphicsEffect()
        if isinstance(existing, QGraphicsDropShadowEffect):
            return

        glow = QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(22)
        glow.setOffset(0, 2)
        glow.setColor(QColor(0, 0, 0, 80))
        btn.setGraphicsEffect(glow)

    def _refresh_grid_button_styles(self) -> None:
        cur = self.profile_manager.current_profile if hasattr(self, "profile_manager") else None
        prof = self.profile_manager.profiles.get(cur, {}) if cur else {}
        for name, btn in self.grid_buttons.items():
            btn_data = prof.get(name, {})
            self._apply_grid_button_style(btn, btn_data)

    def _refresh_grid_icons(self) -> None:
        if not hasattr(self, "grid_buttons"):
            return
        cur = self.profile_manager.current_profile if hasattr(self, "profile_manager") else None
        if not cur:
            return
        if self._current_profile_mode(cur) != "grid":
            return
        prof = self.profile_manager.profiles.get(cur, {})
        rows = int(prof.get("rows", 3))
        cols = int(prof.get("cols", 4))
        cell_w, cell_h = self._compute_cell_size(rows, cols)
        gap = int(self._grid_settings().get("gap", 8))

        for name, btn in self.grid_buttons.items():
            if not btn:
                continue
            span_rows = max(1, int(btn.property("gridRowSpan") or 1))
            span_cols = max(1, int(btn.property("gridColSpan") or 1))
            target_w = cell_w * span_cols + gap * (span_cols - 1)
            target_h = cell_h * span_rows + gap * (span_rows - 1)
            btn_data = prof.get(name, {})
            icon_path = (btn_data.get("icon") or "").strip()
            if icon_path and os.path.exists(icon_path):
                bg_color = self._icon_bg_color_for_btn(btn_data)
                self._set_button_icon(btn, icon_path, target_w, target_h, bg_color=bg_color)
            else:
                btn.setIcon(QIcon())

    def _reset_all_button_styles(self) -> None:
        reply = QMessageBox.question(
            self,
            "Reset styles",
            "Naozaj chceš zmazať všetky vlastné štýly tlačidiel?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        changed = False
        for prof in self.profile_manager.profiles.values():
            if not isinstance(prof, dict):
                continue
            for key, btn_data in prof.items():
                if not isinstance(btn_data, dict):
                    continue
                if not key.startswith("btn"):
                    continue
                if btn_data.get("style"):
                    btn_data["style"] = {}
                    changed = True
        self._current_button_style = self._default_button_style()
        self._apply_grid_background_style()
        self._refresh_grid_button_styles()
        if changed:
            self._request_save()
            self.statusBar().showMessage("Všetky štýly tlačidiel boli resetované.", 2000)

    def _apply_grid_background_style(self) -> None:
        cont = self._grid_display_widget()
        if cont is None:
            return
        grid = self._display_settings.get("grid", {})
        color = self._normalize_hex_color(str(grid.get("bg_color", "#000000")), "#000000")
        try:
            cont.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        except Exception:
            pass

        bg_label = getattr(self, "displayBgLabel", None)
        if self._current_profile_mode() != "grid":
            if isinstance(bg_label, QLabel):
                bg_label.hide()
            return

        if isinstance(bg_label, QLabel):
            if cont.width() <= 2 or cont.height() <= 2:
                bg_label.hide()
                return
            bg_label.setGeometry(0, 0, cont.width(), cont.height())
            img_path = str(grid.get("bg_image", "") or "").strip()
            radius = 18
            rect = self._grid_bg_rect
            if not isinstance(rect, QRect) or rect.width() <= 0 or rect.height() <= 0:
                rect = QRect(0, 0, cont.width(), cont.height())
            rect_sig = (
                int(rect.x()),
                int(rect.y()),
                int(rect.width()),
                int(rect.height()),
            )
            if img_path and os.path.isfile(img_path):
                sig = self._file_sig(img_path)
                cache_key = (
                    "img",
                    img_path,
                    sig[0],
                    sig[1],
                    int(cont.width()),
                    int(cont.height()),
                    radius,
                    rect_sig,
                )
                out = self._grid_bg_pixmap_cache.get(cache_key)
                if out is None:
                    pm = QPixmap(img_path)
                    if not pm.isNull():
                        target_size = QSize(rect.width(), rect.height())
                        scaled = pm.scaled(
                            target_size,
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        out = QPixmap(cont.size())
                        out.fill(Qt.GlobalColor.transparent)
                        painter = QPainter(out)
                        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                        path = QPainterPath()
                        path.addRoundedRect(QRectF(rect), radius, radius)
                        painter.setClipPath(path)
                        x = rect.x() + (rect.width() - scaled.width()) // 2
                        y = rect.y() + (rect.height() - scaled.height()) // 2
                        painter.drawPixmap(x, y, scaled)
                        painter.end()
                        self._cache_put(self._grid_bg_pixmap_cache, cache_key, out, self._grid_bg_cache_limit)
                if isinstance(out, QPixmap):
                    bg_label.setPixmap(out)
                    bg_label.show()
                    return

            cache_key = (
                "color",
                color,
                int(cont.width()),
                int(cont.height()),
                radius,
                rect_sig,
            )
            out = self._grid_bg_pixmap_cache.get(cache_key)
            if out is None:
                out = QPixmap(cont.size())
                out.fill(Qt.GlobalColor.transparent)
                painter = QPainter(out)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color))
                painter.drawRoundedRect(QRectF(rect), radius, radius)
                painter.end()
                self._cache_put(self._grid_bg_pixmap_cache, cache_key, out, self._grid_bg_cache_limit)
            bg_label.setPixmap(out)
            bg_label.show()

    def _apply_app_theme_from_settings(self) -> None:
        appearance = self._display_settings.get("appearance", {})
        theme = str(appearance.get("theme", "Dark"))
        if theme not in ("Dark", "OLED", "Light"):
            theme = "Dark"

        accent = self._normalize_hex_color(str(appearance.get("accent", "#2563EB")), "#2563EB")
        self._accent_color = accent
        accent_light = self._adjust_hex_color(accent, 125)
        accent_dark = self._darken_hex_color(accent, 130)
        accent_darker = self._darken_hex_color(accent, 150)

        tokens = {
            "{{ACCENT}}": accent,
            "{{ACCENT_LIGHT}}": accent_light,
            "{{ACCENT_DARK}}": accent_dark,
            "{{ACCENT_DARKER}}": accent_darker,
            "{{ACCENT_RGBA_25}}": self._hex_to_rgba(accent, 0.25),
            "{{ACCENT_RGBA_20}}": self._hex_to_rgba(accent, 0.20),
        }

        theme_file = self._resource_path("style", f"theme_{theme.lower()}.qss")
        qss = ""
        if theme_file.exists():
            try:
                qss = theme_file.read_text(encoding="utf-8")
            except Exception as e:
                print(f"Theme load failed: {e}")
                qss = ""
        if not qss:
            fallback = self._resource_path("style", "style.qss")
            if fallback.exists():
                try:
                    qss = fallback.read_text(encoding="utf-8")
                except Exception:
                    qss = ""

        for key, val in tokens.items():
            qss = qss.replace(key, val)

        wallpaper_enabled = bool(appearance.get("wallpaper_enabled", False))
        wallpaper_path = str(appearance.get("wallpaper", "") or "").strip()
        if wallpaper_enabled and wallpaper_path and os.path.isfile(wallpaper_path):
            dim = int(appearance.get("wallpaper_dim", 30))
            dim = max(0, min(80, dim))
            alpha = max(0.0, min(1.0, dim / 100.0))
            wp = wallpaper_path.replace("\\", "/").replace('"', '\\"')
            qss += (
                "\nQMainWindow {"
                f' background-image: url("{wp}");'
                " background-position: center;"
                " background-repeat: no-repeat;"
                " background-attachment: fixed;"
                "}\n"
                f"#centralwidget {{ background-color: rgba(0, 0, 0, {alpha}); }}\n"
            )
        else:
            qss += "\n#centralwidget { background-color: transparent; }\n"

        if qss:
            self.setStyleSheet(qss)
            self.style().polish(self)
            self._refresh_grid_button_styles()

    def _open_button_style_dialog(self) -> None:
        if not self.selected_button_name:
            QMessageBox.information(self, "Style", "Najprv vyber tlačidlo v gride.")
            return
        defaults = self._default_button_style()
        current = getattr(self, "_current_button_style", defaults) or defaults
        label = (self.inputName.text() or "").strip() or "Button"

        dlg = ButtonStyleDialog(self, current, defaults, label, self._normalize_hex_color)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._current_button_style = dlg.get_style()
            self._schedule_button_autosave(delay_ms=0)
            self.statusBar().showMessage("Style uložený.", 2000)

    def _copy_to_clipboard(self, text: str | None) -> None:
        raw = text or ""

        # prázdny vstup – simuluj Ctrl+C (Windows / Linux cez backend)
        if not raw.strip():
            if IS_WINDOWS and win32api is not None and win32con is not None:
                try:
                    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                    win32api.keybd_event(ord('C'), 0, 0, 0)
                    win32api.keybd_event(ord('C'), 0, win32con.KEYEVENTF_KEYUP, 0)
                    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                    print("CopyToClipboard: simulované Ctrl+C (prázdny vstup)")
                except Exception as e:
                    print(f"CopyToClipboard Ctrl+C error: {e}")
            else:
                try:
                    self.media_backend.send_keys("ctrl+c")
                except Exception as e:
                    print(f"CopyToClipboard Linux Ctrl+C error: {e}")
            return

        text = raw.strip()
        app = QGuiApplication.instance()
        if app is None:
            print("CopyToClipboard: QGuiApplication.instance() je None")
            return

        cb = app.clipboard()
        cb.setText(text)

        try:
            current = cb.text()
        except Exception:
            current = "<neviem prečítať>"

        print(f"CopyToClipboard: do schránky uložené -> '{text}'")
        print(f"CopyToClipboard: kontrola schránky -> '{current}'")

    def _run_linux_command_candidates(self, action_name: str, candidates: list[list[str]]) -> bool:
        is_wayland = os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"
        allow_wayland_xtools = os.environ.get("MACROTOUCH_ALLOW_WAYLAND_XTOOLS", "").strip().lower() in {
            "1", "true", "yes", "on"
        }

        for cmd in candidates:
            if not cmd:
                continue
            exe = cmd[0]
            if is_wayland and not allow_wayland_xtools and exe in {"xdotool", "wmctrl"}:
                print(
                    f"[Linux] {action_name} – '{exe}' on Wayland is disabled "
                    "(set MACROTOUCH_ALLOW_WAYLAND_XTOOLS=1 to force)"
                )
                continue
            if not shutil.which(exe):
                continue
            try:
                subprocess.Popen(cmd)
                return True
            except Exception as e:
                print(f"[Linux] {action_name} command failed ({' '.join(cmd)}): {e}")
        print(f"[Linux] {action_name} – žiadny vhodný príkaz sa nenašiel")
        return False

    def _load_state(self):
        try:
            state = self.state_manager.load_state()

            self.profile_manager.profiles = state.get("profiles", {})
            self.profile_manager.current_profile = state.get("current_profile", "Default")

            app_flags = state.get("app_flags", {})
            if isinstance(app_flags, dict):
                for key in self._app_flags:
                    if key in app_flags:
                        self._app_flags[key] = bool(app_flags.get(key))

            display_settings = state.get("display_settings", {})
            self._display_settings = self._merge_display_settings(display_settings)
            changed = self._ensure_light_grid_bg(update_ui=False)
            self._apply_display_settings_to_ui()
            self._apply_app_theme_from_settings()
            if changed:
                self._request_save()

            for name, prof in self.profile_manager.profiles.items():
                if not isinstance(prof, dict):
                    continue
                mode = prof.get("mode", "grid")
                self._ensure_profile_defaults(name, mode)

            self.listProfiles.blockSignals(True)
            self.listProfiles.clear()
            self.listProfiles.addItems(list(self.profile_manager.profiles.keys()))
            self.listProfiles.blockSignals(False)

            if self.profile_manager.current_profile not in self.profile_manager.profiles:
                self.profile_manager.current_profile = next(iter(self.profile_manager.profiles.keys()))

            self._set_selected_profile(self.profile_manager.current_profile)

            # ensure full profile application (UI and ESP) after load
            try:
                self.profile_manager.load_profile(self.profile_manager.current_profile)
            except Exception:
                self.logger.exception("Failed to apply current profile after state load")

            self.statusBar().showMessage(
                f"Načítané nastavenia profilu: {self.profile_manager.current_profile}"
            )
        except Exception as e:
            self.logger.exception("Load state error")
            self.statusBar().showMessage("Chyba pri načítavaní stavu", 4000)

    def _profiles_iter(self):
        return list(self.profile_manager.profiles.items())

    def _cell_exists_in_profile(self, prof_dict: dict, btn_name: str) -> bool:
        if not btn_name.startswith("btn") or len(btn_name) < 5:
            return False
        try:
            r = int(btn_name[3])
            c = int(btn_name[4])
        except Exception:
            return False
        rows = max(1, min(4, int(prof_dict.get("rows", 3))))
        cols = max(1, min(4, int(prof_dict.get("cols", 4))))
        return (0 <= r < rows) and (0 <= c < cols)

    def _btn_name(self, r: int, c: int) -> str:
        return f"btn{r}{c}"

    def _parse_btn_name(self, name: str) -> tuple[int, int] | None:
        text = (name or "").strip()
        if not text.startswith("btn") or len(text) < 5:
            return None
        try:
            r = int(text[3])
            c = int(text[4])
        except Exception:
            return None
        return r, c

    def _sanitize_span_values(
        self,
        span_rows: int | str | None,
        span_cols: int | str | None,
        rows: int,
        cols: int,
        r: int,
        c: int,
    ) -> tuple[int, int]:
        try:
            sr = int(span_rows or 1)
        except Exception:
            sr = 1
        try:
            sc = int(span_cols or 1)
        except Exception:
            sc = 1
        # Current UX supports up to 2x2 widgets.
        sr = max(1, min(2, sr, max(1, rows - r)))
        sc = max(1, min(2, sc, max(1, cols - c)))
        return sr, sc

    def _get_button_span(self, btn_data: dict[str, Any], rows: int, cols: int, r: int, c: int) -> tuple[int, int]:
        if not isinstance(btn_data, dict):
            return 1, 1
        return self._sanitize_span_values(
            btn_data.get("span_rows", 1),
            btn_data.get("span_cols", 1),
            rows,
            cols,
            r,
            c,
        )

    def _resolve_grid_layout(
        self,
        profile_dict: dict[str, Any],
        rows: int,
        cols: int,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        owners: list[list[str | None]] = [[None for _ in range(cols)] for _ in range(rows)]
        anchors: list[dict[str, Any]] = []

        def _is_free(rr: int, cc: int, sr: int, sc: int) -> bool:
            if rr < 0 or cc < 0 or sr <= 0 or sc <= 0:
                return False
            if rr + sr > rows or cc + sc > cols:
                return False
            for y in range(rr, rr + sr):
                for x in range(cc, cc + sc):
                    if owners[y][x] is not None:
                        return False
            return True

        for r in range(rows):
            for c in range(cols):
                if owners[r][c] is not None:
                    continue
                name = self._btn_name(r, c)
                raw = profile_dict.get(name, {})
                btn_data = raw if isinstance(raw, dict) else {}
                span_rows, span_cols = self._get_button_span(btn_data, rows, cols, r, c)

                candidates: list[tuple[int, int]] = [(span_rows, span_cols)]
                if span_cols > 1:
                    candidates.append((span_rows, 1))
                if span_rows > 1:
                    candidates.append((1, span_cols))
                if (1, 1) not in candidates:
                    candidates.append((1, 1))

                chosen_rows, chosen_cols = 1, 1
                for cand_rows, cand_cols in candidates:
                    if _is_free(r, c, cand_rows, cand_cols):
                        chosen_rows, chosen_cols = cand_rows, cand_cols
                        break

                for rr in range(r, r + chosen_rows):
                    for cc in range(c, c + chosen_cols):
                        owners[rr][cc] = name

                anchors.append(
                    {
                        "name": name,
                        "row": r,
                        "col": c,
                        "span_rows": chosen_rows,
                        "span_cols": chosen_cols,
                        "data": btn_data,
                    }
                )

        cell_to_anchor: dict[str, str] = {}
        for r in range(rows):
            for c in range(cols):
                cell = self._btn_name(r, c)
                owner = owners[r][c] or cell
                cell_to_anchor[cell] = owner

        return anchors, cell_to_anchor

    def _fit_span_for_anchor(
        self,
        profile_dict: dict[str, Any],
        rows: int,
        cols: int,
        anchor_name: str,
        requested_rows: int | str | None,
        requested_cols: int | str | None,
    ) -> tuple[int, int]:
        coords = self._parse_btn_name(anchor_name)
        if coords is None:
            return 1, 1
        r, c = coords
        req_rows, req_cols = self._sanitize_span_values(requested_rows, requested_cols, rows, cols, r, c)

        def _btn_has_payload(name: str) -> bool:
            raw = profile_dict.get(name, {})
            if not isinstance(raw, dict):
                return False
            if str(raw.get("name", "") or "").strip():
                return True
            if str(raw.get("icon", "") or "").strip():
                return True
            if str(raw.get("action", "") or "").strip():
                return True
            if str(raw.get("path", "") or "").strip():
                return True
            style = raw.get("style")
            if isinstance(style, dict) and bool(style):
                return True
            try:
                if int(raw.get("span_rows", 1) or 1) > 1:
                    return True
            except Exception:
                pass
            try:
                if int(raw.get("span_cols", 1) or 1) > 1:
                    return True
            except Exception:
                pass
            return False

        baseline = dict(profile_dict)
        base_raw = baseline.get(anchor_name, {})
        base_btn = dict(base_raw) if isinstance(base_raw, dict) else {}
        base_btn.pop("span_rows", None)
        base_btn.pop("span_cols", None)
        baseline[anchor_name] = base_btn
        _, cell_to_anchor = self._resolve_grid_layout(baseline, rows, cols)

        occupied_by_other: set[str] = set()
        for cell, owner in cell_to_anchor.items():
            if not owner or owner == anchor_name:
                continue
            if _btn_has_payload(owner):
                occupied_by_other.add(cell)

        candidates: list[tuple[int, int]] = [(req_rows, req_cols)]
        if req_cols > 1:
            candidates.append((req_rows, 1))
        if req_rows > 1:
            candidates.append((1, req_cols))
        if (1, 1) not in candidates:
            candidates.append((1, 1))

        for cand_rows, cand_cols in candidates:
            sr, sc = self._sanitize_span_values(cand_rows, cand_cols, rows, cols, r, c)
            ok = True
            for rr in range(r, r + sr):
                for cc in range(c, c + sc):
                    if self._btn_name(rr, cc) in occupied_by_other:
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                return sr, sc

        return 1, 1

    def _widget_size_text(self, span_rows: int, span_cols: int) -> str:
        return f"{int(span_cols)}x{int(span_rows)}"

    def _parse_widget_size_text(self, text: str) -> tuple[int, int]:
        m = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", str(text or ""), re.IGNORECASE)
        if not m:
            return 1, 1
        try:
            cols = int(m.group(1))
            rows = int(m.group(2))
        except Exception:
            return 1, 1
        rows = max(1, min(2, rows))
        cols = max(1, min(2, cols))
        return rows, cols

    def _allowed_widget_spans(self, anchor_name: str, rows: int, cols: int) -> list[tuple[int, int]]:
        coords = self._parse_btn_name(anchor_name)
        if coords is None:
            return [(1, 1)]
        r, c = coords
        max_rows = max(1, min(2, rows - r))
        max_cols = max(1, min(2, cols - c))
        out: list[tuple[int, int]] = [(1, 1)]
        if max_cols >= 2:
            out.append((1, 2))
        if max_rows >= 2:
            out.append((2, 1))
        if max_rows >= 2 and max_cols >= 2:
            out.append((2, 2))
        return out

    def _propagate_single_button_to_all_profiles(self, src_profile_name: str, btn_name: str) -> None:
        src_prof = self.profile_manager.profiles.get(src_profile_name)
        if not isinstance(src_prof, dict):
            return

        src_btn = src_prof.get(btn_name)
        if not isinstance(src_btn, dict):
            return

        for name, prof in self._profiles_iter():
            if name == src_profile_name:
                continue
            if self._cell_exists_in_profile(prof, btn_name):
                try:
                    span_rows = int(src_btn.get("span_rows", 1) or 1)
                except Exception:
                    span_rows = 1
                try:
                    span_cols = int(src_btn.get("span_cols", 1) or 1)
                except Exception:
                    span_cols = 1
                prof[btn_name] = {
                    "name": src_btn.get("name", ""),
                    "icon": src_btn.get("icon", ""),
                    "action": src_btn.get("action", ""),
                    "path": src_btn.get("path", ""),
                    "style": dict(src_btn.get("style", {}) or {}),
                    "span_rows": span_rows,
                    "span_cols": span_cols,
                }

    def _on_profile_list_changed(self, cur, prev):
        """Reakcia na zmenu vybraného profilu v ľavom zozname."""
        self._flush_button_autosave()
        name = cur.text() if cur else ""
        if not name:
            return

        try:
            profile = self.profile_manager.load_profile(name)
            # if profile validation passed, the callback _apply_loaded_profile will run
            # and do all UI changes
            pass
        except Exception as e:
            self.logger.exception(f"Profile load failed: {name}")
            self.statusBar().showMessage(f"Chyba pri načítaní profilu: {e}", 4000)

    def _apply_loaded_profile(self, profile_name: str, profile: dict[str, Any]) -> None:
        """Apply profile data to UI components (coverage of load event)."""
        mode = profile.get("mode", "grid")
        self._media_send_enabled = (mode == "media")

        self.statusBar().showMessage(f"Profil '{profile_name}' – režim: {mode}", 2000)

        if mode == "grid":
            self._lock_grid_ui(False)
            self._update_right_panel_for_mode(mode)
            rows = int(profile.get("rows", 3))
            cols = int(profile.get("cols", 4))
            self.render_grid(rows, cols)
        else:
            self._lock_grid_ui(True)
            self._update_right_panel_for_mode(mode)
            if mode == "monitor":
                self._render_monitor_preview(profile_name)
            elif mode == "media":
                self._render_media_preview(profile_name)
            elif mode == "mixer":
                self._render_mixer_preview(profile_name)
            else:
                self._clear_grid_widgets()
                placeholder = QLabel(f"{mode.upper()} – náhľad bude doplnený")
                placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.gridLayout.addWidget(placeholder, 0, 0)

        self._apply_cell_size_to_all()
        self._apply_grid_background_style()
        self._update_system_stats_state()
        self._send_profile_change_to_esp(profile_name)
        self._media_status_allowed = (mode == "media")
        self._update_media_timers(mode == "media")
        self._update_mixer_timers(mode == "mixer")
        self._refresh_weather_sync_state()
        self._schedule_weather_sync(delay_ms=180, force=False)
        self._refresh_weather_preview_in_app()
        self._refresh_metric_preview_in_app()
        self._send_metric_widgets_to_esp(force=True)

    def _lock_grid_ui(self, locked: bool):
        """
        Enable/disable grid a pravý panel tlačidiel podľa typu profilu.
        """
        # Grid (stred) - kontrola existencie grid_buttons
        if hasattr(self, "gridLayout") and hasattr(self, "grid_buttons"):
            for name, btn in self.grid_buttons.items():
                btn.setEnabled(not locked)
            self._refresh_grid_button_styles()

        # Pravý panel – všeobecné vstupy tlačidiel
        to_lock = [
            self.inputName,
            self.inputPath,
            self.lineIconPath,
            self.comboActionType,
            self.comboWidgetSize,
            self.btnChooseIcon,
        ]

        for w in to_lock:
            if w:
                w.setEnabled(not locked)
        if getattr(self, "lblWidgetSize", None):
            self.lblWidgetSize.setEnabled(not locked)

        # SpinBoxy pre rows/cols
        if hasattr(self, "spinRows"):
            self.spinRows.setEnabled(not locked)
        if hasattr(self, "spinColumns"):
            self.spinColumns.setEnabled(not locked)

        # Hardvérové tlačidlá (A/B/POT) nechávame povolené

    def _update_right_panel_for_mode(self, mode: str) -> None:
        is_grid = (mode == "grid")
        grp_buttons = self.findChild(QWidget, "grpButtonSettings")
        if grp_buttons:
            grp_buttons.setVisible(is_grid)

        self._update_profile_mode_info(mode)

    def _profile_mode_info_payload(self, mode: str) -> dict[str, str]:
        payload = PROFILE_MODE_INFO.get(mode)
        if payload:
            return payload

        pretty = (mode or "profile").replace("_", " ").strip().title() or "Profile"
        return {
            "badge": pretty.upper(),
            "title": f"{pretty} preview",
            "description": "This profile uses a dedicated screen preview instead of the editable grid layout.",
            "hint": "Shared hardware mappings below remain available and take effect on the device after upload.",
        }

    def _update_profile_mode_info(self, mode: str) -> None:
        grp = getattr(self, "grpProfileModeInfo", None)
        if not isinstance(grp, QGroupBox):
            return

        if mode == "grid":
            grp.hide()
            return

        payload = self._profile_mode_info_payload(mode)

        if isinstance(getattr(self, "lblProfileModeBadge", None), QLabel):
            self.lblProfileModeBadge.setText(payload["badge"])
        if isinstance(getattr(self, "lblProfileModeTitle", None), QLabel):
            self.lblProfileModeTitle.setText(payload["title"])
        if isinstance(getattr(self, "lblProfileModeDescription", None), QLabel):
            self.lblProfileModeDescription.setText(payload["description"])
        if isinstance(getattr(self, "lblProfileModeHint", None), QLabel):
            self.lblProfileModeHint.setText(payload["hint"])

        grp.show()

    def _ensure_profile_defaults(self, profile_name: str, mode: str):
        """
        Nastaví základné hodnoty pre profil podľa typu (mode).
        Používame ju pri vytvorení profilu aj pri načítaní zo súboru.
        """
        prof = self.profile_manager.profiles.setdefault(profile_name, {})
        normalized = apply_profile_mode_defaults(prof, mode)
        prof.clear()
        prof.update(normalized)

    def _on_stats_updated(self, stats: dict):
        """
        Aktualizuje SystemMonitorWidget a posiela MON packet na ESP.
        UI riešime len vtedy, keď sme v monitor profile a widget je reálne zobrazený.
        """
        self._last_stats = stats
        mode_is_monitor = (self._current_profile_mode() == "monitor")
        has_widget = hasattr(self, "monitor_widget") and self.monitor_widget is not None

        # --- 1) UI v PC – len ak má zmysel ---
        if mode_is_monitor and has_widget and self.monitor_widget.isVisible():
            # === CPU ===
            cpu_pct = float(stats.get("cpu_percent", 0.0) or 0.0)
            cpu_ghz = float(stats.get("cpu_ghz", 0.0) or 0.0)
            cpu_cores = int(stats.get("cpu_cores", 0) or 0)
            cpu_threads = int(stats.get("cpu_threads", 0) or 0)

            self.monitor_widget.set_cpu(
                cpu_pct,
                primary=f"{cpu_pct:.0f} %",
                secondary=f"{cpu_ghz:.1f} GHz   {cpu_cores}/{cpu_threads}",
            )

            # === RAM ===
            ram_used = float(stats.get("ram_used_gb", 0.0) or 0.0)
            ram_total = float(stats.get("ram_total_gb", 0.0) or 0.0)
            ram_pct = float(stats.get("ram_percent", 0.0) or 0.0)

            self.monitor_widget.set_ram(
                ram_pct,
                primary=f"{ram_pct:.0f} %",
                secondary=f"{ram_used:.1f} / {ram_total:.1f} GB",
            )

            # === GPU ===
            gpu_pct = stats.get("gpu_percent")
            gpu_temp = stats.get("gpu_temp")

            gpu_pct_val = float(gpu_pct) if isinstance(gpu_pct, (int, float)) else 0.0
            gpu_primary = f"{gpu_pct_val:.0f} %" if isinstance(gpu_pct, (int, float)) else "N/A"
            if isinstance(gpu_temp, (int, float)):
                gpu_secondary = f"{gpu_temp:.0f} °C"
            else:
                gpu_secondary = "N/A"

            self.monitor_widget.set_gpu(gpu_pct_val, gpu_primary, gpu_secondary)


            # === DISK ===
            disk_mb_s = float(stats.get("disk_mb_s", 0.0) or 0.0)
            DISK_MAX_MB = 50.0
            disk_pct = max(0.0, min(100.0, (disk_mb_s / DISK_MAX_MB) * 100.0))
            if disk_mb_s < 1.0:
                disk_text = f"{disk_mb_s * 1024:.0f} KB/s"
            else:
                disk_text = f"{disk_mb_s:.1f} MB/s"
            self.monitor_widget.disk_card.set_values(
                disk_pct,
                primary=disk_text,
                secondary="",
            )

            # === NET ===
            net_mb_s = float(stats.get("net_mb_s", 0.0) or 0.0)
            NET_MAX_MB = 10.0
            net_pct = max(0.0, min(100.0, (net_mb_s / NET_MAX_MB) * 100.0))
            if net_mb_s < 1.0:
                net_text = f"{net_mb_s * 1024:.0f} KB/s"
            else:
                net_text = f"{net_mb_s:.1f} MB/s"
            self.monitor_widget.net_card.set_values(
                net_pct,
                primary=net_text,
                secondary="",
            )

            # === FPS ===
            fps = stats.get("fps")
            fps_text = f"{fps:.0f}" if isinstance(fps, (int, float)) else "N/A"
            self.monitor_widget.set_fps(fps_text)

        # --- 2) POSIELANIE NA ESP – stále len v monitor profile ---
        cpu_pct = float(stats.get("cpu_percent", 0.0) or 0.0)
        ram_pct = float(stats.get("ram_percent", 0.0) or 0.0)
        disk_mb_s = float(stats.get("disk_mb_s", 0.0) or 0.0)
        net_mb_s = float(stats.get("net_mb_s", 0.0) or 0.0)
        gpu_pct = stats.get("gpu_percent")
        fps_val = stats.get("fps")

        if (
            mode_is_monitor
            and getattr(self, "serial_service", None)
            and self.serial_service.is_connected
        ):
            now = time.monotonic()
            if now - getattr(self, "_last_mon_packet_ts", 0.0) >= 2.0:
                self._last_mon_packet_ts = now

                line = (
                    "MON:"
                    f"CPU={cpu_pct:.1f};"
                    f"RAM={ram_pct:.1f};"
                    f"GPU={(gpu_pct if isinstance(gpu_pct, (int, float)) else -1.0):.1f};"
                    f"DISK={disk_mb_s:.2f};"
                    f"NET={net_mb_s:.2f};"
                    f"FPS={(fps_val if isinstance(fps_val, (int, float)) else -1.0):.1f}"
                )

                self.logger.debug("→ MON packet: %s", line)
                try:
                    self.serial_service.send_line(line)
                except Exception:
                    self.logger.exception("Error serial_service.send_line (MON)")

        # --- 3) METRIC WIDGETY (grid tlačidlá + ESP packet) ---
        self._refresh_metric_preview_in_app()
        self._send_metric_widgets_to_esp(force=False)


    def _update_system_stats_state(self):
        """
        Systémové štatistiky nech bežia stále – je to lacné
        a zjednoduší to logiku.
        """
        if not hasattr(self, "system_stats"):
            return

        if not self.system_stats.is_running():
            self.system_stats.start()


    def _propagate_controls_to_all_profiles(self, src_profile_name: str):
        src = self.profile_manager.profiles.get(src_profile_name, {})
        keys = ("btnA_action", "btnB_action", "pot_action")
        for name, prof in self._profiles_iter():
            if name == src_profile_name:
                continue
            for k in keys:
                if k in src:
                    prof[k] = src.get(k, "None")

    # ---------- init / lifecycle ----------

    def __init__(self, start_hidden: bool = False):
        super().__init__()
        self._start_hidden = bool(start_hidden)
        self._booting = True
        self._setup_data()
        self._app_flags = {
            "first_run_complete": False,
            "esp_setup_complete": False,
        }
        self._serial_debug = False
        self._serial_show_logs = False
        self._last_stats: dict | None = None
        self._is_quitting = False
        self._tray_icon: QSystemTrayIcon | None = None
        self._core_thread: QThread | None = None
        self._core_worker: TaskWorker | None = None
        self._usb_driver_warned = False
        self._save_lock = threading.Lock()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_state_now)
        self._grid_resize_timer = QTimer(self)
        self._grid_resize_timer.setSingleShot(True)
        self._grid_resize_timer.timeout.connect(self.on_grid_size_changed)
        self._resize_apply_timer = QTimer(self)
        self._resize_apply_timer.setSingleShot(True)
        self._resize_apply_timer.setInterval(24)
        self._resize_apply_timer.timeout.connect(self._apply_resize_layout)
        self._time_sync_timer = QTimer(self)
        self._time_sync_timer.setSingleShot(False)
        self._time_sync_timer.setInterval(60000)
        self._time_sync_timer.timeout.connect(self._send_time_to_esp)
        self._weather_sync_timer = QTimer(self)
        self._weather_sync_timer.setSingleShot(False)
        self._weather_sync_timer.setInterval(self._weather_sync_interval_ms)
        self._weather_sync_timer.timeout.connect(self._on_weather_sync_timer)
        self._weather_resync_timer = QTimer(self)
        self._weather_resync_timer.setSingleShot(True)
        self._weather_resync_timer.timeout.connect(lambda: self._queue_weather_sync(force=False))
        self._weather_marquee_timer = QTimer(self)
        self._weather_marquee_timer.setSingleShot(False)
        self._weather_marquee_timer.setInterval(280)
        self._weather_marquee_timer.timeout.connect(self._tick_weather_marquee)
        self._mixer_timer = QTimer(self)
        self._mixer_timer.setSingleShot(False)
        self._mixer_timer.setInterval(1000)
        self._mixer_timer.timeout.connect(self._refresh_mixer_state)
        self._mixer_timer_running = False
        self._mixer_updating = False
        self._mixer_volume_timers: dict[str, QTimer] = {}
        self._mixer_pending_volume: dict[str, tuple[int, Any]] = {}
        self._mixer_volume_throttle_ms = 40
        self._mixer_app_slots: list[dict[str, Any]] = []
        self.mixer_widget: MixerWidget | None = None
        self._mixer_status_label: QLabel | None = None
        self._mixer_default_sink: str | None = None
        self._mixer_default_source: str | None = None
        self._mixer_master_muted = False
        self._mixer_mic_muted = False
        self._last_mixer_packet: str | None = None
        self._upload_busy = False
        self._last_upload_speed: str | None = None
        self._last_upload_variant: str | None = None
        self._win_audio_failed = False
        self._win_audio_error: str | None = None
        self._win_master_endpoint = None
        self._win_mic_endpoint = None
        self._encoder_pending = 0
        self._encoder_max_steps_per_flush = 8
        self._encoder_smooth_ms = 25
        self._encoder_flush_timer = QTimer(self)
        self._encoder_flush_timer.setSingleShot(True)
        self._encoder_flush_timer.setInterval(self._encoder_smooth_ms)
        self._encoder_flush_timer.timeout.connect(self._flush_encoder_pending)

        self.smart_home_base_url = SMART_HOME_BASE_URL
        self.smartHomeSaveTimer = QTimer(self)
        self.smartHomeSaveTimer.setSingleShot(True)
        self.smartHomeSaveTimer.setInterval(400) 
        self.smartHomeSaveTimer.timeout.connect(self._save_smart_home_state)

        self.arduino_cli = find_arduino_cli()
        if self.arduino_cli:
            print(f"[MacroTouch] Arduino CLI: {self.arduino_cli}")
        else:
            print("[MacroTouch] Upozornenie: 'arduino-cli' nenájdené – upload nebude fungovať.")
            self.statusBar().showMessage("arduino-cli nenájdené – nahrávanie na ESP vypnuté", 5000)

        self.grid_buttons = {}
        self._grid_cell_to_anchor = {}
        self._swipe_enabled = False
        self._swipe_start_pos = None
        self._touch_start_pos = None
        self._touch_press_target = None
        self._touch_block_clicks = False
        self._last_touch_click_ts = 0.0
        self._last_touch_click_btn: str | None = None
        self._touch_click_min_interval = 0.18
        self._mixer_swipe_start_pos = None
        self._mixer_swipe_slider = None
        self._mixer_swipe_start_value = None
        self._mixer_swipe_armed = False
        self._mixer_swipe_consumed = False
        self._mixer_zone_start_pos = None
        self._mixer_zone_consumed = False
        self._grid_resize_drag_btn = None
        self._grid_resize_drag_anchor = None
        self._grid_resize_drag_start_global = None
        self._grid_resize_drag_start_span = (1, 1)
        self._grid_resize_drag_candidate = (1, 1)

        # Backend musíme mať pripravený skôr, než príde prvý seriový príkaz
        self.media_backend = get_media_backend()

        # MusicManager musí existovať ešte pred spustením MediaStatusProvideru
        self.music_manager = MusicManager(self)
        self.music_manager.state_changed.connect(self._on_music_state_changed)
        self._last_music_state: MusicState | None = None
        self._media_timer_running = False

        self._setup_ui()
        self.runtime_manager = RuntimeManager(self.app_root, "MacroTouch")
        self._setup_tray()
        self._setup_smart_home_persistence()
        self._load_smart_home_state()
        self._load_state()
        self._setup_connections()
        self._setup_hardware()
        self._setup_system_stats()
        self._init_status_widgets()
        self._media_send_enabled = self._current_profile_mode() == "media"
        self._media_status_allowed = self._current_profile_mode() == "media"
        self.smart_home_base_url = normalize_smart_home_base_url(
            getattr(self, "smart_home_base_url", SMART_HOME_BASE_URL)
        )

        if MediaStatusProvider is not None:
            self._setup_media_status()
        else:
            self.media_status = None

        self._booting = False

        if self._start_hidden and self._tray_icon is not None and self._tray_icon.isVisible():
            self.hide()
        else:
            self.show()
        self._update_system_stats_state()
        if not self._start_hidden:
            QTimer.singleShot(1500, self._maybe_first_run_esp_setup)
            QTimer.singleShot(2500, self._check_usb_serial_drivers)

        self._last_mon_packet_ts = 0.0
        self._last_media_packet_ts = 0.0
        self._last_media_progress_ts = time.monotonic()

        self._media_progress_timer = QTimer(self)
        self._media_progress_timer.setInterval(500)
        self._media_progress_timer.timeout.connect(self._tick_media_progress)
        self._update_media_timers(self._media_status_allowed)
        self._refresh_weather_sync_state()

        print("STATE FILE ->", _state_file())
        self.statusBar().showMessage(f"Ukladací súbor: {_state_file()}", 4000)

        try:
            fp = _appdata_dir() / "window.bin"
            if fp.exists():
                with open(fp, "rb") as f:
                    self.restoreGeometry(f.read())
        except Exception:
            pass

    def _setup_ui(self):
        app_root = Path(__file__).resolve().parent
        self.app_root = app_root
        self.project_root = _appdata_dir()  # zapisuj sketchy do user config (nie Program Files)

        # cesta k .ui – priečinok "ui"
        ui_file = app_root / "ui" / "aplikacia.ui"
        if not ui_file.exists():
            raise FileNotFoundError(f"UI súbor {ui_file} nebol nájdený!")

        uic.loadUi(str(ui_file), self)
        self._apply_gray_label_properties()

        # Layout stretch (nie cez .ui kvôli PyQt6 uic kompatibilite)
        macro_grid = self.findChild(QGridLayout, "macroGrid")
        if macro_grid is not None:
            macro_grid.setColumnStretch(0, 0)
            macro_grid.setColumnStretch(1, 1)
            macro_grid.setColumnStretch(2, 0)

        # QTabWidget môže mať rôzne objectName v .ui (tabWidget/tabMain)
        self.tabWidget = self.findChild(QTabWidget, "tabWidget") or self.findChild(QTabWidget, "tabMain")
        if self.tabWidget is None:
            print("Varovanie: nenašiel som QTabWidget s objectName='tabWidget'")
        else:
            # default tab po štarte – MacroTouch (index 0)
            self.tabWidget.setCurrentIndex(0)

            # keď sa zmení tab, chceme prípadne prepočítať grid
            self.tabWidget.currentChanged.connect(self._on_tab_changed)


        # načítaj theme QSS podľa nastavení (default je Dark)
        self._apply_app_theme_from_settings()

        self._init_ui_elements()
        self._init_top_bar_effect()
        self._init_action_and_smart_home_ui()
        self._refresh_smart_home_status_label()
        self._init_gestures()

    def _apply_gray_label_properties(self) -> None:
        for name in self.GRAY_LABELS:
            lbl = self.findChild(QLabel, name)
            if lbl is not None:
                lbl.setProperty("gray", True)

    def _resource_path(self, *parts: str) -> Path:
        base = getattr(self, "app_root", Path(__file__).resolve().parent)
        return Path(base, *parts)

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("[MacroTouch] System tray not available.")
            return

        icon_path = self._resource_path("icons", "MacroTouch.ico")
        icon = QIcon(str(icon_path)) if icon_path.exists() else self.windowIcon()
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip("MacroTouch")

        menu = QMenu(self)
        action_show = QAction("Open MacroTouch", self)
        action_show.triggered.connect(self._show_from_tray)
        action_update = QAction("Update App", self)
        action_update.triggered.connect(self._update_from_tray)
        action_autostart = QAction("Start On Login", self)
        action_autostart.setCheckable(True)
        action_autostart.setChecked(self.runtime_manager.is_autostart_enabled())
        action_autostart.triggered.connect(self._toggle_autostart_from_tray)
        action_quit = QAction("Quit", self)
        action_quit.triggered.connect(self._quit_from_tray)
        menu.addAction(action_show)
        menu.addSeparator()
        menu.addAction(action_update)
        menu.addAction(action_autostart)
        menu.addSeparator()
        menu.addAction(action_quit)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self._tray_icon = tray

    def _on_tray_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _toggle_autostart_from_tray(self, checked: bool) -> None:
        ok, msg = self.runtime_manager.set_autostart(bool(checked))
        if not ok:
            QMessageBox.warning(self, "Autostart", msg)
            menu = self._tray_icon.contextMenu() if self._tray_icon is not None else None
            if menu is not None:
                for action in menu.actions():
                    if action.text() == "Start On Login":
                        action.blockSignals(True)
                        action.setChecked(self.runtime_manager.is_autostart_enabled())
                        action.blockSignals(False)
                        break
            return
        self.statusBar().showMessage(msg, 3500)

    def _update_from_tray(self) -> None:
        res = self.runtime_manager.update_from_source()
        if not res.success:
            QMessageBox.warning(self, "Update", res.message)
            return

        if res.updated:
            QMessageBox.information(
                self,
                "Update",
                "Aplikacia bola aktualizovana.\nRestartni ju, aby sa nacitali zmeny.\n\n"
                f"{res.message}",
            )
            return

        QMessageBox.information(self, "Update", res.message)

    def _quit_from_tray(self) -> None:
        self._is_quitting = True
        self.close()
        app = QGuiApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)


    def _setup_connections(self):
        self.serialLine.connect(self.on_serial_line)
        self.serialConnected.connect(self._on_serial_connected)
        self.serialDisconnected.connect(self._on_serial_disconnected)
        self.weatherPreviewRefresh.connect(self._refresh_weather_preview_in_app)

        self.spinRows.blockSignals(True)
        self.spinColumns.blockSignals(True)
        self.spinRows.setValue(self.profile_manager.profiles[self.profile_manager.current_profile]["rows"])
        self.spinColumns.setValue(self.profile_manager.profiles[self.profile_manager.current_profile]["cols"])
        self.spinRows.blockSignals(False)
        self.spinColumns.blockSignals(False)

        self.listProfiles.clear()
        self.listProfiles.addItems(list(self.profile_manager.profiles.keys()))
        self._set_selected_profile(self.profile_manager.current_profile)

        self._init_styles()

    def _arduino_env(self) -> dict[str, str]:
        return build_arduino_env()

    def _arduino_paths(self) -> dict[str, Path]:
        return arduino_paths()

    def _copy_bundled_library(self, lib_name: str) -> bool:
        try:
            return copy_bundled_library(self.app_root, lib_name)
        except Exception as e:
            print(f"[Arduino] Failed to copy bundled {lib_name}: {e}")
            return False

    def _ensure_arduino_cli_config(
        self,
        config_path: Path,
        data_dir: Path,
        sketchbook_dir: Path,
        downloads_dir: Path,
    ) -> None:
        try:
            from modules.arduino_utils import ensure_arduino_cli_config

            ensure_arduino_cli_config(config_path, data_dir, sketchbook_dir, downloads_dir)
        except Exception as e:
            print(f"[Arduino] Failed to write arduino-cli config: {e}")

    def _arduino_cli_run(self, cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        return arduino_cli_run(cmd, **kwargs)

    def _esp32_core_installed(self) -> bool:
        if not self.arduino_cli:
            return False
        result = self._arduino_cli_run(
            [self.arduino_cli, "core", "list"],
            env=self._arduino_env(),
        )
        if result.returncode != 0:
            return False
        return any("esp32:esp32" in line for line in result.stdout.splitlines())

    def _library_installed(self, lib_name: str) -> bool:
        if not self.arduino_cli:
            return False
        result = self._arduino_cli_run(
            [self.arduino_cli, "lib", "list"],
            env=self._arduino_env(),
        )
        if result.returncode != 0:
            return False
        needle = lib_name.strip().lower()
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("name"):
                continue
            name = line.split()[0].strip().lower()
            if name == needle:
                return True
        return False

    def _lovyangfx_ready(self) -> bool:
        if not self._library_installed("LovyanGFX"):
            return False
        return lovyangfx_ready()

    def _install_esp32_core(self) -> None:
        if not self.arduino_cli:
            raise RuntimeError("arduino-cli not available.")
        env = self._arduino_env()
        commands = [
            [self.arduino_cli, "core", "update-index", "--additional-urls", ESP32_BOARD_URL],
            [self.arduino_cli, "core", "install", "esp32:esp32", "--additional-urls", ESP32_BOARD_URL],
        ]
        for cmd in commands:
            run = self._arduino_cli_run(cmd, env=env)
            if run.returncode != 0:
                raise RuntimeError(
                    f"Arduino CLI failed.\nCMD: {' '.join(cmd)}\n\nSTDOUT:\n{run.stdout}\n\nSTDERR:\n{run.stderr}"
                )

    def _install_library(self, lib_name: str) -> None:
        if not self.arduino_cli:
            raise RuntimeError("arduino-cli not available.")
        if lib_name == "LovyanGFX":
            self._install_lovyangfx()
            return
        env = self._arduino_env()
        commands = [
            [self.arduino_cli, "lib", "update-index"],
            [self.arduino_cli, "lib", "install", lib_name],
        ]
        for cmd in commands:
            run = self._arduino_cli_run(cmd, env=env)
            if run.returncode != 0:
                raise RuntimeError(
                    f"Arduino CLI failed.\nCMD: {' '.join(cmd)}\n\nSTDOUT:\n{run.stdout}\n\nSTDERR:\n{run.stderr}"
                )

    def _install_lovyangfx(self) -> None:
        if not self.arduino_cli:
            raise RuntimeError("arduino-cli not available.")
        env = self._arduino_env()
        lib_dir = self._arduino_paths()["libraries"] / "LovyanGFX"
        if lib_dir.exists():
            shutil.rmtree(lib_dir, ignore_errors=True)
        cmd = [self.arduino_cli, "lib", "install", "--git-url", LOVYANGFX_GIT_URL]
        run = self._arduino_cli_run(cmd, env=env)
        if run.returncode != 0:
            raise RuntimeError(
                f"Arduino CLI failed.\nCMD: {' '.join(cmd)}\n\nSTDOUT:\n{run.stdout}\n\nSTDERR:\n{run.stderr}"
            )
    def _ensure_esp32_core_async(self, on_ready=None) -> None:
        if self._esp32_core_installed():
            if callable(on_ready):
                on_ready()
            return

        resp = QMessageBox.question(
            self,
            "ESP32",
            "ESP32 core is not installed. Download and install it now? (Internet required)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        self.statusBar().showMessage("Installing ESP32 core... this can take a few minutes.")
        self._core_thread = QThread()
        self._core_worker = TaskWorker(self._install_esp32_core)
        self._core_worker.moveToThread(self._core_thread)

        self._core_thread.started.connect(self._core_worker.run)
        self._core_worker.finished.connect(self._core_thread.quit)
        self._core_worker.finished.connect(self._core_worker.deleteLater)
        self._core_thread.finished.connect(self._core_thread.deleteLater)

        def _done():
            self.statusBar().showMessage("ESP32 core installed.", 4000)
            if callable(on_ready):
                on_ready()

        def _error(msg: str):
            QMessageBox.critical(self, "ESP32", msg)

        self._core_worker.finished.connect(_done)
        self._core_worker.error.connect(_error)
        self._core_thread.start()

    def _ensure_library_async(self, lib_name: str, on_ready=None) -> None:
        if self._library_installed(lib_name):
            if callable(on_ready):
                on_ready()
            return

        resp = QMessageBox.question(
            self,
            "Arduino Library",
            f"{lib_name} library is not installed. Download and install it now? (Internet required)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        self.statusBar().showMessage(f"Installing {lib_name} library... this can take a few minutes.")
        self._lib_thread = QThread()
        self._lib_worker = TaskWorker(self._install_library, lib_name)
        self._lib_worker.moveToThread(self._lib_thread)

        self._lib_thread.started.connect(self._lib_worker.run)
        self._lib_worker.finished.connect(self._lib_thread.quit)
        self._lib_worker.finished.connect(self._lib_worker.deleteLater)
        self._lib_thread.finished.connect(self._lib_thread.deleteLater)

        def _done():
            self.statusBar().showMessage(f"{lib_name} library installed.", 4000)
            if callable(on_ready):
                on_ready()

        def _error(msg: str):
            QMessageBox.critical(self, "Arduino Library", msg)

        self._lib_worker.finished.connect(_done)
        self._lib_worker.error.connect(_error)
        self._lib_thread.start()

    def _ensure_lovyangfx_async(self, on_ready=None) -> None:
        if self._lovyangfx_ready():
            if callable(on_ready):
                on_ready()
            return
        if self._copy_bundled_library("LovyanGFX") and self._lovyangfx_ready():
            if callable(on_ready):
                on_ready()
            return

        resp = QMessageBox.question(
            self,
            "Arduino Library",
            "LovyanGFX needs a newer GitHub version for ESP32 core 3.x. Install it now? (Internet required)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        self.statusBar().showMessage("Installing LovyanGFX (GitHub)... this can take a few minutes.")
        self._lib_thread = QThread()
        self._lib_worker = TaskWorker(self._install_lovyangfx)
        self._lib_worker.moveToThread(self._lib_thread)

        self._lib_thread.started.connect(self._lib_worker.run)
        self._lib_worker.finished.connect(self._lib_thread.quit)
        self._lib_worker.finished.connect(self._lib_worker.deleteLater)
        self._lib_thread.finished.connect(self._lib_thread.deleteLater)

        def _done():
            self.statusBar().showMessage("LovyanGFX installed.", 4000)
            if callable(on_ready):
                on_ready()

        def _error(msg: str):
            QMessageBox.critical(self, "Arduino Library", msg)

        self._lib_worker.finished.connect(_done)
        self._lib_worker.error.connect(_error)
        self._lib_thread.start()

    def _check_usb_serial_drivers(self) -> None:
        if not IS_WINDOWS or self._usb_driver_warned:
            return

        try:
            from serial.tools import list_ports
        except Exception:
            return

        known_vids = {0x1A86, 0x10C4}  # CH34x, CP210x
        ports = list(list_ports.comports())
        if any((p.vid in known_vids) for p in ports if p.vid is not None):
            return

        try:
            import wmi  # type: ignore
        except Exception:
            return

        missing = []
        try:
            for dev in wmi.WMI().Win32_PnPEntity():
                hwids = dev.HardwareID or []
                combined = " ".join(hwids).upper()
                label = None
                if "VID_1A86" in combined:
                    label = dev.Name or "CH34x"
                elif "VID_10C4" in combined:
                    label = dev.Name or "CP210x"
                else:
                    continue

                err = getattr(dev, "ConfigManagerErrorCode", None)
                if err == 28:
                    missing.append(label)
        except Exception:
            return

        if missing:
            self._usb_driver_warned = True
            devices = ", ".join(sorted(set(missing)))
            QMessageBox.warning(
                self,
                "USB driver missing",
                "Detected USB device without driver:\n"
                f"{devices}\n\n"
                "Install CH34x (CH340/CH343) or CP210x driver, then reconnect the ESP.",
            )

    def _maybe_first_run_esp_setup(self) -> None:
        if self._app_flags.get("first_run_complete") or self._app_flags.get("esp_setup_complete"):
            return

        if not self.arduino_cli:
            self.statusBar().showMessage(
                "Arduino CLI was not found. ESP setup is disabled.", 5000
            )
            return

        port = None
        if getattr(self, "serial_service", None):
            port = self.serial_service.serial_port or self.serial_service.detect_esp32_port()
        if not port:
            self.statusBar().showMessage(
                "ESP32 not detected. Connect it and use 'Upload to ESP' later.", 5000
            )
            return

        resp = QMessageBox.question(
            self,
            "MacroTouch",
            f"Detected ESP32 on {port}. Flash MacroTouch firmware now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            self._app_flags["first_run_complete"] = True
            self._request_save()
            return

        self._app_flags["first_run_complete"] = True
        self._request_save()
        self._ensure_esp32_core_async(on_ready=self.handle_upload_to_esp)

    def _on_tab_changed(self, index: int):
        """
        Bezpečné prepočítanie layoutu pri prepnutí tabu.
        Zatiaľ len obnoví veľkosti gridu na MacroTouch karte.
        """
        if index == 0 and hasattr(self, "_apply_cell_size_to_all"):
            self._apply_cell_size_to_all()

    def _setup_hardware(self):
        # media_backend je cross-platform – už ho máš v __init__
        # self.media_backend = get_media_backend()  # už máš nižšie v __init__

        # Windows-špecifické media API (GS MTC, jas atď.)
        if IS_WINDOWS and WindowsMediaController is not None:
            self.media = WindowsMediaController()
        else:
            self.media = None

        self.volume_step = 0.02
        self.brightness_step = 5
        self.volume_step_fine = 0.02
        self.brightness_step_fine = 2

        self.encoder_handler = ImprovedEncoderHandler(self)

        self.serial_service = SerialService(self, status_callback=self.statusBar().showMessage)
        self.serial_monitor = self.serial_service.monitor
        self.serial_monitor.verbose = bool(self._serial_debug)
        self.serial_service.start()

        self.port_timer = QTimer(self)
        # častejší refresh, aby po resete/reenumerácii rýchlejšie chytil nový port
        self.port_timer.setInterval(1000)
        self.port_timer.timeout.connect(self.serial_monitor.refresh_port)
        self.port_timer.start()
        # prvý refresh krátko po štarte – pre prípady, keď sa ACM objaví s oneskorením
        QTimer.singleShot(600, self.serial_monitor.refresh_port)

    def _pause_port_refresh(self) -> None:
        timer = getattr(self, "port_timer", None)
        if isinstance(timer, QTimer) and timer.isActive():
            timer.stop()

    def _resume_port_refresh(self) -> None:
        timer = getattr(self, "port_timer", None)
        if isinstance(timer, QTimer) and not timer.isActive():
            timer.start()

    def _set_upload_busy(self, busy: bool, message: str | None = None) -> None:
        self._upload_busy = bool(busy)
        widget = self.centralWidget()
        if widget is not None:
            widget.setEnabled(not busy)
        if hasattr(self, "grid_buttons") and self.grid_buttons:
            self._refresh_grid_button_styles()
        if busy:
            self.setCursor(Qt.CursorShape.WaitCursor)
        else:
            self.unsetCursor()
        if message is not None:
            self.statusBar().showMessage(message)


    def _setup_system_stats(self):
        self.system_stats = SystemStatsProvider(self, interval_ms=1000)
        self.system_stats.stats_updated.connect(self._on_stats_updated)

    def _setup_media_status(self):
        # provider pre Spotify / globálnu media session
        self.media_status = MediaStatusProvider(self, interval_ms=1000)
        self.media_status.media_updated.connect(self._on_media_status)
        self.media_status.start()

        # po chvíli skontroluj, či sa provider reálne rozbehol
        QTimer.singleShot(2500, self._check_media_status)

    def _check_media_status(self):
        if not self.media_status.is_running():
            self.statusBar().showMessage(
                "Media monitoring nie je podporovaný alebo beží bez MPRIS/session.",
                5000,
            )

    def _on_media_status(self, info: dict):
        """
        Aktualizuje centrálny MusicState a pošle dáta na ESP.
        UI (MediaWidget) sa už aktualizuje cez _on_music_state_changed.
        """
        if not getattr(self, "_media_status_allowed", False):
            return

        source = (info.get("source") or "").strip() or "Spotify"
        title = (info.get("title") or "").strip() or "—"
        artist = (info.get("artist") or "").strip() or ""
        album = (info.get("album") or "").strip() or ""

        position = float(info.get("position") or 0.0)
        duration = float(info.get("duration") or 0.0)
        is_playing = bool(info.get("is_playing"))

        display_artist = artist or source

        try:
            if IS_WINDOWS and self.media is not None:
                vol_pct = int(round(self.media.get_volume() * 100))
                vol_pct = max(0, min(100, vol_pct))
            else:
                vol_pct = -1
        except Exception:
            vol_pct = -1


        # 1) aktualizuj centrálny stav (vrátane hlasitosti)
        self.music_manager.update_metadata(
            source=source.upper(),
            title=title,
            artist=display_artist,
            pos_s=position,
            dur_s=max(duration, 1.0),
            is_playing=is_playing,
            volume_pct=vol_pct,
        )

        # 2) ESP – MED packet posielame len v media profile
        if self._current_profile_mode() == "media":
            self._send_media_packet_to_esp(info)



    def _send_media_packet_to_esp(self, info: dict) -> None:
        """
        Pošle na ESP informácie o prehrávanej skladbe/videu,
        ale len ak:
          - sme v 'media' profile
          - seriový port je otvorený
          - neprekračujeme throttle (napr. raz za 0.5 s)

        Protokol:
          SRC:SPOTIFY / YOUTUBE / VLC / DEFAULT
          TRK:Title - Artist       # jednoduchý ASCII text, nie UTF-8 bordel
          POS:current/total        # sekundy / sekundy
          STATE:PLAYING / PAUSED
          VOL:0-100                # voliteľné
        """
        if (not getattr(self, "_media_send_enabled", False)) or self._current_profile_mode() != "media":
            return

        if not getattr(self, "serial_service", None) or not self.serial_service.is_connected:
            return

        now = time.monotonic()
        last_meta = getattr(self, "_last_media_meta", None)
        last_ts = getattr(self, "_last_media_packet_ts", 0.0)

        # --- mapovanie zdroja na ID pre ESP ---
        src = (info.get("source") or "").strip().lower()
        app = (info.get("source_app") or "").strip().lower()

        if "spotify" in src or "spotify" in app:
            src_id = "SPOTIFY"
        elif "browser" in src or any(x in app for x in ("chrome", "msedge", "firefox")):
            # pre jednoduchosť označíme všetko z browsera ako YT
            src_id = "YOUTUBE"
        elif "vlc" in src or "vlc" in app:
            src_id = "VLC"
        else:
            src_id = "DEFAULT"

        title = (info.get("title") or "").strip()
        artist = (info.get("artist") or "").strip()
        source_label = (info.get("source") or "").strip()

        # to, čo pôjde do "interpret" časti, ak chýba artist
        display_artist = artist or source_label

        # TRK pre ESP: Title - Artist / Title / Artist / "-"
        if title and display_artist:
            trk = f"{title} - {display_artist}"     # správne poradie, obyčajný '-'
        elif title:
            trk = title
        elif display_artist:
            trk = display_artist
        else:
            trk = "-"

        position = int(float(info.get("position") or 0.0))
        duration = int(float(info.get("duration") or 0.0))
        is_playing = bool(info.get("is_playing"))

        state = "PLAYING" if is_playing else "PAUSED"

        # volume – ak nevieš korektne získať, môžeš to vypnúť
        vol_pct = info.get("volume_pct", -1) if isinstance(info, dict) else -1
        if not isinstance(vol_pct, (int, float)) or vol_pct < 0:
            try:
                vol_pct = int(round(self.media.get_volume() * 100))
                vol_pct = max(0, min(100, vol_pct))
            except Exception:
                vol_pct = -1

        # Rozhodovanie, či posielať: pri nezmenenom title/state/volume posielaj len každých 5 s
        meta_key = (src_id, trk, duration, state, vol_pct)
        if meta_key == last_meta and is_playing and (now - last_ts) < 5.0:
            return
        if meta_key == last_meta and not is_playing and (now - last_ts) < 5.0:
            return
        self._last_media_meta = meta_key
        self._last_media_packet_ts = now

        # zostavíme riadky
        lines = [
            f"SRC:{src_id}",
            f"TRK:{trk}",
            f"POS:{position}/{max(duration, 1)}",  # ochrana pred delením nulou na ESP
            f"STATE:{state}",
        ]
        if vol_pct >= 0:
            lines.append(f"VOL:{vol_pct}")

        # pošli ich na ESP
        try:
            for l in lines:
                self.serial_service.send_line(l)
        except Exception:
            self.logger.exception("Error pri posielaní MED packetu")

    def _send_mixer_packet_to_esp(self, force: bool = False) -> None:
        if not force and self._current_profile_mode() != "mixer":
            return
        if not hasattr(self, "_mixer_channels"):
            return
        if not getattr(self, "serial_service", None) or not self.serial_service.is_connected:
            return

        master = int(self._mixer_channels["master"]["slider"].value())
        mic = int(self._mixer_channels["mic"]["slider"].value())
        master_m = 1 if self._mixer_master_muted else 0
        mic_m = 1 if self._mixer_mic_muted else 0

        def app_payload(slot: dict[str, Any]) -> str:
            if slot.get("id") is None or not slot["container"].isVisible():
                return ""
            name = self._sanitize_mixer_label(slot.get("name", "App"))
            vol = int(slot["slider"].value())
            mute = 1 if slot.get("mute") else 0
            return f"{name},{vol},{mute}"

        app1 = app_payload(self._mixer_app_slots[0]) if len(self._mixer_app_slots) > 0 else ""
        app2 = app_payload(self._mixer_app_slots[1]) if len(self._mixer_app_slots) > 1 else ""

        line = f"MIX:MASTER={master};MM={master_m};MIC={mic};MICM={mic_m};APP1={app1};APP2={app2}"

        if line == self._last_mixer_packet:
            return
        self._last_mixer_packet = line
        try:
            self.serial_monitor.send_line(line)
        except Exception as e:
            print(f"Error pri posielani MIX packetu: {e}")

    # ---------- UI helpers ----------

    def test_serial_communication(self):
        """Testovacia funkcia pre komunikáciu s ESP32"""
        if not getattr(self, "serial_service", None):
            print("[TEST] Serial service neexistuje")
            return

        print(f"[TEST] Serial port: {self.serial_service.serial_port}")
        print(f"[TEST] Serial objekt: {self.serial_service.ser}")

        if self.serial_service.is_connected:
            print("[TEST] Port je otvorený")
        else:
            print("[TEST] Port nie je inicializovaný alebo nie je pripojený")

        test_msg = "TEST:Hello"
        print(f"[TEST] Posielam testovaciu správu: {test_msg}")
        self.serial_service.send_line(test_msg)

        monitor = self.serial_service.monitor
        with monitor._lock:
            queue_size = len(monitor._outgoing)
            print(f"[TEST] Veľkosť odosielacej fronty: {queue_size}")

    def _get_required(self, cls, name):
        w = self.findChild(cls, name)
        if w is None:
            existing = [x.objectName() for x in self.findChildren(cls)]
            raise RuntimeError(
                f"V .ui chýba '{name}' typu {cls.__name__}. "
                f"Nájdené {cls.__name__}: {existing}"
            )
        return w

    def _set_selected_profile(self, name: str):
        items = self.listProfiles.findItems(name, Qt.MatchFlag.MatchExactly)
        if items:
            self.listProfiles.setCurrentItem(items[0])

    def _current_profile_name(self) -> str | None:
        it = self.listProfiles.currentItem()
        return it.text() if it else None

    def _set_combo_safe(self, cb: QComboBox, value: str | None, fallback: str = "None"):
        raw = (value or "").strip()
        i = -1

        if raw:
            for idx in range(cb.count()):
                if self._combo_item_value(cb, idx) == raw or cb.itemText(idx).strip() == raw:
                    i = idx
                    break

        if i < 0 and raw:
            wanted = self._normalize_action(raw)
            for idx in range(cb.count()):
                if self._normalize_action(self._combo_item_value(cb, idx)) == wanted:
                    i = idx
                    break

        if i < 0:
            for idx in range(cb.count()):
                if self._combo_item_value(cb, idx) == fallback or cb.itemText(idx).strip() == fallback:
                    i = idx
                    break

        cb.setCurrentIndex(max(i, 0))

    def _combo_item_value(self, cb: QComboBox, idx: int) -> str:
        data = cb.itemData(idx, Qt.ItemDataRole.UserRole)
        if isinstance(data, str) and data.strip():
            return data.strip()
        return (cb.itemText(idx) or "").strip()

    def _combo_selected_value(self, cb: QComboBox) -> str:
        data = cb.currentData(Qt.ItemDataRole.UserRole)
        if isinstance(data, str) and data.strip():
            return data.strip()
        return (cb.currentText() or "").strip()

    def _populate_value_combo(
        self,
        cb: QComboBox,
        values: list[str],
        labels: dict[str, str] | None = None,
    ) -> None:
        cb.blockSignals(True)
        cb.clear()
        for value in values:
            cb.addItem((labels or {}).get(value, value), value)
        cb.blockSignals(False)

    def _init_ui_elements(self):
        self.listProfiles = self._get_required(QListWidget, "listProfiles")
        self.listProfiles.currentItemChanged.connect(self._on_profile_list_changed)
        try:
            self.listProfiles.viewport().installEventFilter(self)
            self.listProfiles.viewport().setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        except Exception:
            pass
        self._profiles_drag_start = None
        self._profiles_dragging = False

        self.inputName = self._get_required(QLineEdit, "lineName")
        self.inputPath = self._get_required(QLineEdit, "linePath")
        self.lineIconPath = self._get_required(QLineEdit, "lineIconPath")
        self.comboActionType = self._get_required(QComboBox, "comboAction")
        self.comboWidgetSize: QComboBox | None = None
        self.lblWidgetSize: QLabel | None = None
        self._init_button_style_button()
        self._init_widget_size_controls()

        self.gridLayout = self.findChild(QGridLayout, "gridPreviewLayout")
        if self.gridLayout is None:
            raise RuntimeError("Nenájdený gridPreviewLayout v grpPreview -> gridCanvas.")
        self.gridCanvas = self.findChild(QWidget, "gridCanvas")
        self.displayFrame = self.findChild(QFrame, "displayFrame")
        self.gridCanvasLayout = self.findChild(QVBoxLayout, "gridCanvasV")
        self.displayBgLabel = None
        self.gridFrameOverlay = None
        self.gridResizePreview = None
        self._grid_bg_rect = None
        if self.displayFrame is not None:
            self.displayBgLabel = QLabel(self.displayFrame)
            self.displayBgLabel.setObjectName("displayBgLabel")
            self.displayBgLabel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.displayBgLabel.setScaledContents(False)
            self.displayBgLabel.lower()
            self.displayBgLabel.hide()
            self.gridFrameOverlay = QLabel(self.displayFrame)
            self.gridFrameOverlay.setObjectName("gridFrameOverlay")
            self.gridFrameOverlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.gridFrameOverlay.setScaledContents(False)
            self.gridFrameOverlay.hide()
            self.gridResizePreview = QFrame(self.displayFrame)
            self.gridResizePreview.setObjectName("gridResizePreview")
            self.gridResizePreview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.gridResizePreview.setStyleSheet(
                "QFrame#gridResizePreview {"
                "background: rgba(59, 130, 246, 0.24);"
                "border: 2px solid #3B82F6;"
                "border-radius: 14px;"
                "}"
            )
            self.gridResizePreview.hide()

        self.spinRows = self._get_required(QSpinBox, "spinRows")
        self.spinColumns = self._get_required(QSpinBox, "spinCols")
        self.spinRows.setMinimum(1)
        self.spinRows.setMaximum(4)
        self.spinColumns.setMinimum(1)
        self.spinColumns.setMaximum(4)
        self.spinRows.valueChanged.connect(self._schedule_grid_resize)
        self.spinColumns.valueChanged.connect(self._schedule_grid_resize)

        self.btnChooseIcon = self._get_required(QPushButton, "btnBrowseIcon")
        self.btnChooseIcon.clicked.connect(self.choose_icon)
        self.btnBrowsePath = self.findChild(QPushButton, "btnBrowsePath")
        if self.btnBrowsePath:
            self.btnBrowsePath.clicked.connect(self.choose_app_path)

        self.btnUploadToESP = self._get_required(QPushButton, "btnUploadESP")
        self.btnUploadToESP.clicked.connect(self.handle_upload_to_esp)
        self.btnUploadToESP.setToolTip("Upload current profiles, visuals and hardware mappings to the ESP.")
        self.grpProfileModeInfo = self.findChild(QGroupBox, "grpProfileModeInfo")
        self.lblProfileModeBadge = self.findChild(QLabel, "lblProfileModeBadge")
        self.lblProfileModeTitle = self.findChild(QLabel, "lblProfileModeTitle")
        self.lblProfileModeDescription = self.findChild(QLabel, "lblProfileModeDescription")
        self.lblProfileModeHint = self.findChild(QLabel, "lblProfileModeHint")

        self.btnAddProfile = self.findChild(QPushButton, "btnAddProfile")
        self.btnRenameProfile = self.findChild(QPushButton, "btnEditProfile")
        self.btnDeleteProfile = self.findChild(QPushButton, "btnRemoveProfile")
        if self.btnAddProfile:
            self.btnAddProfile.clicked.connect(self._on_add_profile_clicked)
        if self.btnRenameProfile:
            self.btnRenameProfile.clicked.connect(self.rename_profile)
        if self.btnDeleteProfile:
            self.btnDeleteProfile.clicked.connect(self.delete_profile)

        self.comboBtnA = self._get_required(QComboBox, "comboBtnA")
        self.comboBtnB = self._get_required(QComboBox, "comboBtnB")
        self.comboPot = self._get_required(QComboBox, "comboPot")
        self.comboBtnA.setToolTip("Primary action for hardware Button A.")
        self.comboBtnB.setToolTip("Primary action for hardware Button B.")
        self.comboPot.setToolTip("Encoder behavior. Press the encoder on the device to cycle this mode.")

        self._populate_value_combo(self.comboBtnA, BTN_ACTIONS, HARDWARE_INPUT_ACTION_LABELS)
        self._populate_value_combo(self.comboBtnB, BTN_ACTIONS, HARDWARE_INPUT_ACTION_LABELS)
        self._populate_value_combo(self.comboPot, KNOB_MODES, HARDWARE_INPUT_KNOB_LABELS)

        self.comboActionType.blockSignals(True)
        self.comboActionType.clear()
        self.comboActionType.addItems(BUTTON_ACTIONS)
        self.comboActionType.blockSignals(False)
        self.comboActionType.currentTextChanged.connect(self._on_action_type_changed)

        cur_prof = self.profile_manager.current_profile
        prof = self.profile_manager.profiles.get(cur_prof, {})
        self._set_combo_safe(self.comboBtnA, prof.get("btnA_action"))
        self._set_combo_safe(self.comboBtnB, prof.get("btnB_action"))
        self._set_combo_safe(self.comboPot, prof.get("pot_action"))

        if self._button_autosave_timer is None:
            self._button_autosave_timer = QTimer(self)
            self._button_autosave_timer.setSingleShot(True)
            self._button_autosave_timer.timeout.connect(self._auto_save_button_settings)

        for line in (self.inputName, self.inputPath, self.lineIconPath):
            if line:
                line.textEdited.connect(self._schedule_button_autosave)
                line.editingFinished.connect(self._schedule_button_autosave)

    def _init_button_style_button(self) -> None:
        self.btnOpenStyle = self.findChild(QPushButton, "btnOpenStyle")
        if self.btnOpenStyle:
            self.btnOpenStyle.clicked.connect(self._open_button_style_dialog)

    def _init_widget_size_controls(self) -> None:
        btn_grid = self.findChild(QGridLayout, "btnGrid")
        if btn_grid is None:
            return

        parent = self.findChild(QWidget, "grpButtonSettings") or self
        lbl = QLabel("Widget size", parent)
        lbl.setObjectName("lblWidgetSize")

        combo = QComboBox(parent)
        combo.setObjectName("comboWidgetSize")
        combo.setToolTip("Veľkosť widgetu v mriežke (stĺpce x riadky).")
        combo.addItems(["1x1", "2x1", "1x2", "2x2"])
        combo.currentTextChanged.connect(self._on_widget_size_changed)

        btn_grid.addWidget(lbl, 5, 0)
        btn_grid.addWidget(combo, 5, 2)

        btn_perf = QPushButton("GIF Perf Test", parent)
        btn_perf.setObjectName("btnGifPerfTest")
        btn_perf.setToolTip("Run local GIF performance benchmark (FPS/CPU/dropped frames).")
        btn_perf.clicked.connect(self._open_gif_perf_test_dialog)
        btn_grid.addWidget(btn_perf, 6, 0, 1, 2)

        btn_clear = self.findChild(QPushButton, "btnClearButton")
        if btn_clear is not None:
            btn_grid.removeWidget(btn_clear)
            btn_grid.addWidget(btn_clear, 6, 2)

        self.comboWidgetSize = combo
        self.lblWidgetSize = lbl
        self.btnGifPerfTest = btn_perf

    def _open_gif_perf_test_dialog(self) -> None:
        dlg = self._gif_perf_dialog
        if dlg is None:
            dlg = GifPerfTestDialog(self)
            self._gif_perf_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
            
    def _sync_widget_size_control(self, anchor_name: str, btn_data: dict[str, Any]) -> None:
        combo = getattr(self, "comboWidgetSize", None)
        if combo is None:
            return
        cur = self.profile_manager.current_profile
        prof = self.profile_manager.profiles.get(cur, {})
        rows = max(1, min(4, int(prof.get("rows", 3))))
        cols = max(1, min(4, int(prof.get("cols", 4))))
        coords = self._parse_btn_name(anchor_name)
        if coords is None:
            allowed = [(1, 1)]
            span_rows, span_cols = 1, 1
        else:
            r, c = coords
            allowed = self._allowed_widget_spans(anchor_name, rows, cols)
            span_rows, span_cols = self._get_button_span(btn_data, rows, cols, r, c)
            span_rows, span_cols = self._fit_span_for_anchor(
                prof,
                rows,
                cols,
                anchor_name,
                span_rows,
                span_cols,
            )
        texts = [self._widget_size_text(sr, sc) for sr, sc in allowed]
        selected_text = self._widget_size_text(span_rows, span_cols)
        if selected_text not in texts:
            selected_text = texts[0] if texts else "1x1"

        combo.blockSignals(True)
        combo.clear()
        combo.addItems(texts if texts else ["1x1"])
        idx = combo.findText(selected_text, Qt.MatchFlag.MatchExactly)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)
        combo.setEnabled(self._current_profile_mode() == "grid")

    def _on_widget_size_changed(self, text: str) -> None:
        if self._booting or self._button_ui_loading:
            return
        if self._current_profile_mode() != "grid":
            return
        anchor = self.selected_button_name
        if not anchor:
            return
        span_rows, span_cols = self._parse_widget_size_text(text)
        self._set_button_span(anchor, span_rows, span_cols, from_drag=False)

    def _set_button_span(self, anchor_name: str, span_rows: int, span_cols: int, from_drag: bool = False) -> None:
        self._flush_button_autosave()
        cur = self.profile_manager.current_profile
        prof = self.profile_manager.profiles.get(cur)
        if not isinstance(prof, dict):
            return
        coords = self._parse_btn_name(anchor_name)
        if coords is None:
            return
        rows = max(1, min(4, int(prof.get("rows", 3))))
        cols = max(1, min(4, int(prof.get("cols", 4))))
        btn_data = prof.setdefault(anchor_name, {})
        if not isinstance(btn_data, dict):
            btn_data = {}
            prof[anchor_name] = btn_data

        fitted_rows, fitted_cols = self._fit_span_for_anchor(
            prof,
            rows,
            cols,
            anchor_name,
            span_rows,
            span_cols,
        )
        old_rows, old_cols = self._get_button_span(btn_data, rows, cols, coords[0], coords[1])
        if old_rows == fitted_rows and old_cols == fitted_cols:
            self._sync_widget_size_control(anchor_name, btn_data)
            if from_drag:
                self.statusBar().showMessage("Resize: nie je voľné miesto pre väčší widget.", 1800)
            return

        if fitted_rows <= 1:
            btn_data.pop("span_rows", None)
        else:
            btn_data["span_rows"] = int(fitted_rows)
        if fitted_cols <= 1:
            btn_data.pop("span_cols", None)
        else:
            btn_data["span_cols"] = int(fitted_cols)

        self.render_grid(rows, cols)
        if anchor_name in self.grid_buttons:
            self.on_button_click(anchor_name)
        self._request_save()
        if from_drag:
            self.statusBar().showMessage(f"Widget size: {self._widget_size_text(fitted_rows, fitted_cols)}", 2000)

    def _is_grid_resize_handle_hit(self, btn: QPushButton, local_pos: QPointF) -> bool:
        if btn is None or self._current_profile_mode() != "grid":
            return False
        handle = max(16, min(28, int(min(btn.width(), btn.height()) * 0.22)))
        x = int(local_pos.x())
        y = int(local_pos.y())
        return x >= (btn.width() - handle) and y >= (btn.height() - handle)

    def _start_grid_resize_drag(self, btn: QPushButton, global_pos: QPointF) -> None:
        anchor = btn.objectName()
        if not anchor:
            return
        start_rows = max(1, int(btn.property("gridRowSpan") or 1))
        start_cols = max(1, int(btn.property("gridColSpan") or 1))
        self._grid_resize_drag_btn = btn
        self._grid_resize_drag_anchor = anchor
        self._grid_resize_drag_start_global = QPointF(global_pos)
        self._grid_resize_drag_start_span = (start_rows, start_cols)
        self._grid_resize_drag_candidate = (start_rows, start_cols)
        btn.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self._show_grid_resize_preview(anchor, start_rows, start_cols)

    def _update_grid_resize_drag(self, global_pos: QPointF) -> None:
        btn = self._grid_resize_drag_btn
        anchor = self._grid_resize_drag_anchor
        start_pos = self._grid_resize_drag_start_global
        if btn is None or not anchor or start_pos is None:
            return
        cur = self.profile_manager.current_profile
        prof = self.profile_manager.profiles.get(cur, {})
        rows = max(1, min(4, int(prof.get("rows", 3))))
        cols = max(1, min(4, int(prof.get("cols", 4))))
        cell_w, cell_h = self._compute_cell_size(rows, cols)
        gap = int(self._grid_settings().get("gap", 8))

        dx = float(global_pos.x() - start_pos.x())
        dy = float(global_pos.y() - start_pos.y())
        step_w = max(1.0, float(cell_w + gap))
        step_h = max(1.0, float(cell_h + gap))

        def _delta_from_drag(d: float, step: float) -> int:
            # Make resize more responsive than strict 50% threshold.
            trigger = step * 0.28
            if d >= 0:
                return int((d + trigger) // step)
            return -int(((-d) + trigger) // step)

        start_rows, start_cols = self._grid_resize_drag_start_span
        req_cols = start_cols + _delta_from_drag(dx, step_w)
        req_rows = start_rows + _delta_from_drag(dy, step_h)
        fitted_rows, fitted_cols = self._fit_span_for_anchor(prof, rows, cols, anchor, req_rows, req_cols)
        self._grid_resize_drag_candidate = (fitted_rows, fitted_cols)
        self._show_grid_resize_preview(anchor, fitted_rows, fitted_cols)
        self.statusBar().showMessage(
            f"Resize: {self._widget_size_text(fitted_rows, fitted_cols)} (uvoľni pre potvrdenie)",
            1000,
        )

    def _finish_grid_resize_drag(self, global_pos: QPointF) -> None:
        btn = self._grid_resize_drag_btn
        anchor = self._grid_resize_drag_anchor
        if btn is None or not anchor:
            self._grid_resize_drag_btn = None
            self._grid_resize_drag_anchor = None
            self._grid_resize_drag_start_global = None
            self._grid_resize_drag_start_span = (1, 1)
            self._grid_resize_drag_candidate = (1, 1)
            self._hide_grid_resize_preview()
            return

        self._update_grid_resize_drag(global_pos)
        start_rows, start_cols = self._grid_resize_drag_start_span
        cand_rows, cand_cols = self._grid_resize_drag_candidate

        self._grid_resize_drag_btn = None
        self._grid_resize_drag_anchor = None
        self._grid_resize_drag_start_global = None
        self._grid_resize_drag_start_span = (1, 1)
        self._grid_resize_drag_candidate = (1, 1)
        btn.unsetCursor()
        self._hide_grid_resize_preview()

        if (cand_rows, cand_cols) != (start_rows, start_cols):
            self._set_button_span(anchor, cand_rows, cand_cols, from_drag=True)

    def _grid_rect_for_anchor_span(self, anchor_name: str, span_rows: int, span_cols: int) -> QRect | None:
        if self._current_profile_mode() != "grid":
            return None
        coords = self._parse_btn_name(anchor_name)
        if coords is None:
            return None
        r, c = coords
        cur = self.profile_manager.current_profile
        prof = self.profile_manager.profiles.get(cur, {})
        rows = max(1, min(4, int(prof.get("rows", 3))))
        cols = max(1, min(4, int(prof.get("cols", 4))))
        if r >= rows or c >= cols:
            return None
        sr, sc = self._sanitize_span_values(span_rows, span_cols, rows, cols, r, c)
        cell_w, cell_h = self._compute_cell_size(rows, cols)

        if not hasattr(self, "gridLayout") or self.gridLayout is None:
            return None
        margins = self.gridLayout.contentsMargins()
        gap_x = self.gridLayout.horizontalSpacing()
        gap_y = self.gridLayout.verticalSpacing()
        default_gap = int(self._grid_settings().get("gap", 8))
        if gap_x < 0:
            gap_x = default_gap
        if gap_y < 0:
            gap_y = default_gap

        x = int(margins.left() + c * (cell_w + gap_x))
        y = int(margins.top() + r * (cell_h + gap_y))
        w = int(sc * cell_w + (sc - 1) * gap_x)
        h = int(sr * cell_h + (sr - 1) * gap_y)
        if w <= 0 or h <= 0:
            return None
        return QRect(x, y, w, h)

    def _show_grid_resize_preview(self, anchor_name: str, span_rows: int, span_cols: int) -> None:
        overlay = getattr(self, "gridResizePreview", None)
        frame = getattr(self, "displayFrame", None)
        if not isinstance(overlay, QFrame) or not isinstance(frame, QFrame):
            return
        rect = self._grid_rect_for_anchor_span(anchor_name, span_rows, span_cols)
        if rect is None:
            overlay.hide()
            return
        overlay.setGeometry(rect)
        overlay.show()
        overlay.raise_()

    def _hide_grid_resize_preview(self) -> None:
        overlay = getattr(self, "gridResizePreview", None)
        if isinstance(overlay, QFrame):
            overlay.hide()

    def _init_top_bar_effect(self):
        """Pridá jemný tieň pod TopBar, aby pôsobil ako glass panel."""
        try:
            bar = self.findChild(QFrame, "TopBar")
            if not bar:
                return
            eff = QGraphicsDropShadowEffect(bar)
            eff.setBlurRadius(14)
            eff.setOffset(0, 3)
            eff.setColor(QColor(0, 0, 0, 80))
            bar.setGraphicsEffect(eff)
        except Exception:
            pass

    def _init_action_and_smart_home_ui(self) -> None:
        """Inicializuje akcie pre hardvérové vstupy a Smart Home UI."""
        self.save_extra_inputs()

        self._update_action_fields(self.comboActionType.currentText())

        self.btn_clear = self.findChild(QPushButton, "btnClearButton")
        if self.btn_clear:
            self.btn_clear.clicked.connect(self.on_clear_button_clicked)


        for cb in (self.comboBtnA, self.comboBtnB, self.comboPot):
            cb.blockSignals(False)
            cb.currentTextChanged.connect(self.save_extra_inputs)

        self.btnSaveHardware = self.findChild(QPushButton, "btnSave")
        if self.btnSaveHardware:
            self.btnSaveHardware.clicked.connect(self.save_hardware_inputs_to_all)
            self.btnSaveHardware.setToolTip(
                "Copy the encoder mode and Button A/B mappings to every profile."
            )

        # ==== SMART HOME TAB (ESP #2) ====
        self.lineSmartSSID = self.findChild(QLineEdit, "lineSmartSSID")
        self.lineSmartPass = self.findChild(QLineEdit, "lineSmartPass")
        self.lineSmartBaseUrl = self.findChild(QLineEdit, "lineSmartBaseUrl")
        self.lineSmartRelay1 = self.findChild(QLineEdit, "lineSmartRelay1")
        self.lineSmartRelay2 = self.findChild(QLineEdit, "lineSmartRelay2")
        self.lineSmartRelay3 = self.findChild(QLineEdit, "lineSmartRelay3")
        self.lineSmartRelay4 = self.findChild(QLineEdit, "lineSmartRelay4")
        self.btnUploadSmartHome = self.findChild(QPushButton, "btnUploadSmartHome")
        self.btnSmartBaseSave = self.findChild(QPushButton, "btnSmartBaseSave")

        if self.btnUploadSmartHome:
            self.btnUploadSmartHome.clicked.connect(self.handle_upload_smarthome_esp)
        if self.btnSmartBaseSave:
            self.btnSmartBaseSave.clicked.connect(self.save_smart_home_base_url)

        # ==== DISPLAY TAB (Screensaver + Button theme) ====
        self._init_display_settings_ui()

    def _init_display_settings_ui(self) -> None:
        self.chkScrEnable = self.findChild(QCheckBox, "chkScrEnable")
        if not self.chkScrEnable:
            return

        self.spinScrIdle = self.findChild(QSpinBox, "spinScrIdle")
        self.spinScrTimeSize = self.findChild(QSpinBox, "spinScrTimeSize")
        self.lineScrTimeColor = self.findChild(QLineEdit, "lineScrTimeColor")
        self.lineScrBgColor = self.findChild(QLineEdit, "lineScrBgColor")
        self.lineScrBgImage = self.findChild(QLineEdit, "lineScrBgImage")
        self.lineScrLabelText = self.findChild(QLineEdit, "lineScrLabelText")
        self.spinScrLabelSize = self.findChild(QSpinBox, "spinScrLabelSize")
        self.lineScrLabelColor = self.findChild(QLineEdit, "lineScrLabelColor")
        self.comboScrTimeFont = self.findChild(QComboBox, "comboScrTimeFont")
        self.btnScrTimePick = self.findChild(QPushButton, "btnScrTimePick")
        self.btnScrBgPick = self.findChild(QPushButton, "btnScrBgPick")
        self.btnScrBgBrowse = self.findChild(QPushButton, "btnScrBgBrowse")
        self.btnScrLabelPick = self.findChild(QPushButton, "btnScrLabelPick")
        self.lineGridBgColor = self.findChild(QLineEdit, "lineGridBgColor")
        self.btnGridBgPick = self.findChild(QPushButton, "btnGridBgPick")
        self.lineGridBgImage = self.findChild(QLineEdit, "lineGridBgImage")
        self.btnGridBgBrowse = self.findChild(QPushButton, "btnGridBgBrowse")
        self.lineBtnBgColor = self.findChild(QLineEdit, "lineBtnBgColor")
        self.lineBtnFgColor = self.findChild(QLineEdit, "lineBtnFgColor")
        self.btnBtnBgPick = self.findChild(QPushButton, "btnBtnBgPick")
        self.btnBtnFgPick = self.findChild(QPushButton, "btnBtnFgPick")
        self.btnResetBtnStyles = self.findChild(QPushButton, "btnResetBtnStyles")
        self.chkIconTransparency = self.findChild(QCheckBox, "chkIconTransparency")
        self.comboTheme = self.findChild(QComboBox, "comboTheme")
        self.lineAccentColor = self.findChild(QLineEdit, "lineAccentColor")
        self.btnAccentPick = self.findChild(QPushButton, "btnAccentPick")
        self.chkWallpaperEnable = self.findChild(QCheckBox, "chkWallpaperEnable")
        self.lineWallpaperPath = self.findChild(QLineEdit, "lineWallpaperPath")
        self.btnWallpaperBrowse = self.findChild(QPushButton, "btnWallpaperBrowse")
        self.spinWallpaperDim = self.findChild(QSpinBox, "spinWallpaperDim")


        if self.spinScrIdle:
            self.spinScrIdle.setRange(5, 3600)
            self.spinScrIdle.setSingleStep(5)
            self.spinScrIdle.setSuffix(" s")
        if self.spinScrTimeSize:
            self.spinScrTimeSize.setRange(1, 8)
        if self.comboScrTimeFont:
            self.comboScrTimeFont.clear()
            self.comboScrTimeFont.addItems(["Title", "Body", "Meta", "Default"])
        if self.spinScrLabelSize:
            self.spinScrLabelSize.setRange(1, 6)
        if self.spinWallpaperDim:
            self.spinWallpaperDim.setRange(0, 80)
            self.spinWallpaperDim.setSuffix(" %")

        self.chkScrEnable.toggled.connect(self._on_display_setting_changed)
        if self.spinScrIdle:
            self.spinScrIdle.valueChanged.connect(self._on_display_setting_changed)
        if self.spinScrTimeSize:
            self.spinScrTimeSize.valueChanged.connect(self._on_display_setting_changed)
        if self.spinScrLabelSize:
            self.spinScrLabelSize.valueChanged.connect(self._on_display_setting_changed)
        for line in (
            self.lineScrTimeColor,
            self.lineScrBgColor,
            self.lineScrBgImage,
            self.lineScrLabelText,
            self.lineScrLabelColor,
            self.lineGridBgColor,
            self.lineGridBgImage,
            self.lineBtnBgColor,
            self.lineBtnFgColor,
            self.lineAccentColor,
            self.lineWallpaperPath,
        ):
            if line:
                line.editingFinished.connect(self._on_display_setting_changed)

        if self.comboTheme:
            self.comboTheme.currentTextChanged.connect(self._on_display_setting_changed)
        if self.comboScrTimeFont:
            self.comboScrTimeFont.currentTextChanged.connect(self._on_display_setting_changed)
        if self.chkWallpaperEnable:
            self.chkWallpaperEnable.toggled.connect(self._on_display_setting_changed)
        if self.chkIconTransparency:
            self.chkIconTransparency.toggled.connect(self._on_display_setting_changed)
        if self.spinWallpaperDim:
            self.spinWallpaperDim.valueChanged.connect(self._on_display_setting_changed)

        if self.btnScrTimePick:
            self.btnScrTimePick.clicked.connect(lambda: self._pick_color_for(self.lineScrTimeColor))
        if self.btnScrBgPick:
            self.btnScrBgPick.clicked.connect(lambda: self._pick_color_for(self.lineScrBgColor))
        if self.btnScrBgBrowse:
            self.btnScrBgBrowse.clicked.connect(lambda: self._choose_image_for(self.lineScrBgImage))
        if self.btnScrLabelPick:
            self.btnScrLabelPick.clicked.connect(lambda: self._pick_color_for(self.lineScrLabelColor))
        if self.btnGridBgPick:
            self.btnGridBgPick.clicked.connect(lambda: self._pick_color_for(self.lineGridBgColor))
        if self.btnGridBgBrowse:
            self.btnGridBgBrowse.clicked.connect(lambda: self._choose_image_for(self.lineGridBgImage))
        if self.btnBtnBgPick:
            self.btnBtnBgPick.clicked.connect(lambda: self._pick_color_for(self.lineBtnBgColor))
        if self.btnBtnFgPick:
            self.btnBtnFgPick.clicked.connect(lambda: self._pick_color_for(self.lineBtnFgColor))
        if self.btnResetBtnStyles:
            self.btnResetBtnStyles.clicked.connect(self._reset_all_button_styles)
        if self.btnAccentPick:
            self.btnAccentPick.clicked.connect(lambda: self._pick_color_for(self.lineAccentColor))
        if self.btnWallpaperBrowse:
            self.btnWallpaperBrowse.clicked.connect(self.choose_wallpaper)
        self._apply_display_settings_to_ui()


    def on_clear_button_clicked(self):
        """
        Vymaže aktuálne vybrané tlačidlo v aktuálnom profile:
        - name, icon, action, path -> ""
        - grid button sa vizuálne vyčistí
        - uloží sa stav
        """
        try:
            prof, cell_key, btn = self._get_current_profile_and_cell()
        except RuntimeError as e:
            QMessageBox.warning(self, "Vymazanie tlačidla", str(e))
            return

        # 1) vyčisti dáta v profile
        old_action_key = self._normalize_action(str(btn.get("action", "") or ""))
        btn["name"] = ""
        btn["icon"] = ""
        btn["action"] = ""   # KĽÚČOVÉ – ESP potom nič neposiela
        btn["path"] = ""
        btn["style"] = {}
        btn.pop("span_rows", None)
        btn.pop("span_cols", None)

        # 2) update tlačidla v gride
        qbtn = self.grid_buttons.get(cell_key)
        if qbtn:
            qbtn.setText("")
            qbtn.setIcon(QIcon())

        # 3) update pravého panelu
        self._button_ui_loading = True
        try:
            self.inputName.setText("")
            self.inputPath.setText("")
            self.lineIconPath.setText("")
            self.comboActionType.setCurrentIndex(0)
            if getattr(self, "comboWidgetSize", None):
                self.comboWidgetSize.blockSignals(True)
                self.comboWidgetSize.clear()
                self.comboWidgetSize.addItem("1x1")
                self.comboWidgetSize.setCurrentIndex(0)
                self.comboWidgetSize.blockSignals(False)
            self._current_button_style = self._default_button_style()
        finally:
            self._button_ui_loading = False

        # 4) uložiť
        self._request_save()
        if old_action_key in {"WeatherWidget", "MetricWidget"}:
            self._refresh_weather_sync_state()
            self._schedule_weather_sync(delay_ms=300, force=True)
            self._refresh_metric_preview_in_app()
            self._send_metric_widgets_to_esp(force=True)
        self.statusBar().showMessage(f"Tlačidlo {cell_key} bolo vymazané", 2000)



    def save_hardware_inputs_to_all(self):
        self.save_extra_inputs()
        cur = self.profile_manager.current_profile
        self._propagate_controls_to_all_profiles(cur)
        self._request_save()
        self.statusBar().showMessage(
            "Encoder / Button A / Button B použité pre všetky profily",
            2000,
        )

    def _on_action_type_changed(self, action: str) -> None:
        self._update_action_fields(action)
        self._schedule_button_autosave()

    def _schedule_button_autosave(self, *args, delay_ms: int = 450) -> None:
        if self._button_ui_loading:
            return
        if not self.selected_button_name:
            return
        if self._button_autosave_timer is None:
            self._button_autosave_timer = QTimer(self)
            self._button_autosave_timer.setSingleShot(True)
            self._button_autosave_timer.timeout.connect(self._auto_save_button_settings)
        self._button_autosave_target = self.selected_button_name
        self._button_autosave_timer.start(delay_ms)

    def _auto_save_button_settings(self) -> None:
        if getattr(self, "_button_autosave_target", None) != self.selected_button_name:
            return
        self.save_button_settings(silent=True)

    def _flush_button_autosave(self) -> None:
        timer = self._button_autosave_timer
        if timer and timer.isActive():
            timer.stop()
            self.save_button_settings(silent=True)

    def _update_action_fields(self, action: str):
        action = (action or "").strip()

        action_key = self._normalize_action(action)

        needs_path = action_key in {
            "OpenApp",
            "SendKeys",
            "HTTPRequest",
            "DiscordWebhook",
            "WeatherWidget",
            "MetricWidget",
            "OpenURL",
            "SwitchProfile",
            "CopyToClipboard",
            "SpotifyPlaylist",
        }
        is_smart_relay = action_key.startswith("SmartRelay")
        self.inputPath.setPlaceholderText("…")
        labelPath = self.findChild(QLabel, "labelPath")
        if labelPath:
            labelPath.setEnabled(needs_path)
            if is_smart_relay:
                labelPath.setToolTip("IP adresa SmartHome modulu – nastav v záložke Smart Home (Base URL).")
            elif action_key == "DiscordWebhook":
                labelPath.setToolTip("Webhook URL + správa (alebo JSON payload).")
            elif action_key == "WeatherWidget":
                labelPath.setToolTip("Mesto, alebo lat,lon (voliteľne |popis).")
            elif action_key == "MetricWidget":
                labelPath.setToolTip("Kľúč metriky (CPU, RAM, GPU, GPU_TEMP, FPS, NET, DISK, CPU_GHZ).")
            else:
                labelPath.setToolTip("")
        if hasattr(self, "inputPath"):
            self.inputPath.setEnabled(needs_path)
        if hasattr(self, "btnBrowsePath") and self.btnBrowsePath:
            self.btnBrowsePath.setEnabled(action_key == "OpenApp")

        ph = "…"
        if action_key == "OpenApp":
            ph = r"C:\Program Files\App\app.exe" if IS_WINDOWS else "/usr/bin/app"
        elif action_key == "SendKeys":
            ph = "ctrl+alt+k"
        elif action_key == "HTTPRequest":
            ph = "POST https://127.0.0.1:8080/hook {\"event\":\"macro\"}"
        elif action_key == "DiscordWebhook":
            ph = "https://discord.com/api/webhooks/... Správa z MacroTouch"
        elif action_key == "WeatherWidget":
            ph = "Bratislava | alebo 48.1486,17.1077|Bratislava"
        elif action_key == "MetricWidget":
            ph = "CPU | RAM | GPU | GPU_TEMP | FPS | NET | DISK | CPU_GHZ"
        elif action_key == "OpenURL":
            ph = "https://example.com"
        elif action_key == "SwitchProfile":
            ph = "Názov existujúceho profilu"
        elif action_key == "CopyToClipboard":
            ph = "Text, ktorý sa skopíruje do schránky"
        elif action_key == "SpotifyPlaylist":
            ph = "playlist ID alebo URL/URI (napr. spotify:playlist:...)"
        elif is_smart_relay:
            cur_base = getattr(self, "smart_home_base_url", SMART_HOME_BASE_URL)
            ph = f"SmartHome Base URL (napr. {cur_base}) – nastav v záložke Smart Home"

        self.inputPath.setPlaceholderText(ph)

        if is_smart_relay:
            self.statusBar().showMessage("Pre SmartRelay nastav IP v záložke Smart Home (Base URL).", 3000)

    def save_extra_inputs(self):
        if getattr(self, "_booting", False):
            return
        if not hasattr(self, "profile_manager") or not self.profile_manager.current_profile:
            return
        cur = self.profile_manager.current_profile
        profs = self.profile_manager.profiles
        profs[cur]["btnA_action"] = self._combo_selected_value(self.comboBtnA)
        profs[cur]["btnB_action"] = self._combo_selected_value(self.comboBtnB)
        profs[cur]["pot_action"] = self._combo_selected_value(self.comboPot)
        self._request_save()

    def _init_styles(self):
        for name in ["headingTitle", "sectionTitle2", "sectionTitle4"]:
            label = self.findChild(QLabel, name)
            if label:
                label.setProperty("sectionTitle", True)
        for name in [
            "labelProfiles",
            "labelName",
            "labelIcon",
            "labelAction",
            "labelPath",
            "labelPot",
            "labelBtnA",
            "labelBtnB",
            "labelRows",
            "labelColumns",
        ]:
            label = self.findChild(QLabel, name)
            if label:
                label.setProperty("inputLabel", True)
        for name in ["btnAddProfile", "btnRenameProfile", "btnDeleteProfile"]:
            button = self.findChild(QPushButton, name)
            if button:
                button.setProperty("actionButton", True)
        self.style().polish(self)

    def _init_gestures(self) -> None:
        """Nastavi touch handling. Swipe je vypnuty pre spolahlivejsi tap."""
        try:
            self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
            cw = self.centralWidget()
            if cw is not None:
                cw.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
            if self._swipe_enabled:
                self.grabGesture(Qt.GestureType.SwipeGesture)
                if cw is not None:
                    cw.grabGesture(Qt.GestureType.SwipeGesture)
        except Exception as e:
            print(f"[GESTURE] init failed: {e}")

    # ---------- Serial & device events ----------

    def _on_serial_connected(self, port: str) -> None:
        self._last_mixer_packet = None
        if not getattr(self, "mixer_widget", None):
            self._build_mixer_widget()
        try:
            self._refresh_mixer_state()
        except Exception as e:
            print(f"[Mixer] refresh on connect failed: {e}")
        self._send_mixer_packet_to_esp(force=True)
        self._send_time_to_esp()
        if self._time_sync_timer is not None:
            self._time_sync_timer.start()
        self._weather_last_sent_line = ""
        self._metric_widget_last_sent_line = ""
        self._refresh_weather_sync_state()
        self._send_cached_weather_to_esp(force=True)
        self._schedule_weather_sync(delay_ms=250, force=True)
        self._send_metric_widgets_to_esp(force=True)

    def _on_serial_disconnected(self, port: str) -> None:
        self._last_mixer_packet = None
        if self._time_sync_timer is not None:
            self._time_sync_timer.stop()
        if self._weather_sync_timer is not None:
            self._weather_sync_timer.stop()
        if self._weather_resync_timer is not None:
            self._weather_resync_timer.stop()
        self._weather_last_sent_line = ""
        self._metric_widget_last_sent_line = ""

    def on_serial_line(self, line: str):
        if self._serial_debug:
            print(f"[DEBUG SERIAL] Prijatá správa: {line}")
        # ignoruj prvú sekundu po štarte (boot šum)
        if time.monotonic() - self._startup_t0 < 1.0:
            return

        line = line.rstrip("\r\n")

        # uloženie do interného bufferu serial monitora (ak to používaš)
        try:
            self.serial_monitor.serial_monitor_output.append(line)
        except Exception:
            pass

        kind = "RAW"
        parsed_json: Dict[str, Any] | None = None

        # DEBUG – nech vidíš, či vôbec niečo chodí
        if self._serial_debug:
            print(f"[SERIAL-IN] {line}")
            self.statusBar().showMessage(f"SERIAL: {line}", 1000)

        if line.startswith("LOG:"):
            if getattr(self, "_serial_show_logs", False):
                now = time.monotonic()
                if now - getattr(self, "_serial_log_last_ts", 0.0) >= getattr(self, "_serial_log_min_interval", 0.20):
                    self._serial_log_last_ts = now
                    try:
                        print(line)
                    except Exception:
                        pass
                    try:
                        self.statusBar().showMessage(line, 1200)
                    except Exception:
                        pass
            return
        if line.startswith((
            "E (", "W (", "I (", "D (", "V (",
            "rst:", "boot:", "entry 0x", "configsip:",
            "SPIWP:", "mode:", "load:", "ets ",
        )):
            return
        if line.startswith("BENCH:"):
            self._handle_esp_bench_line(line)
            return

        # 1) enkóder riadky
        if line.startswith("ENC:") or line.startswith("ENC"):
            kind = "ENC"

        else:
            # 2) JSON eventy
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "event" in data:
                    kind = "EVENT"
                    parsed_json = data
                else:
                    parsed_json = None
            except Exception:
                parsed_json = None

            # 3) textové príkazy (ACTION:... alebo len ACTION)
            if kind == "RAW":
                action = line.split(":", 1)[0].strip()
                if action in BUTTON_ACTIONS or action in {
                    "BrightnessAbs", "BrightnessRel", "LOADED"
                }:
                    kind = "CMD"

        # --- vlastné spracovanie ---

        if kind == "ENC":
            self._process_encoder_line(line)
            return

        if parsed_json is not None:
            self.handle_device_event(parsed_json)
            return

        # fallback – klasické textové príkazy
        self.handle_serial_command(line)

    def _handle_esp_bench_line(self, line: str) -> None:
        text = (line or "").strip()
        if not text.startswith("BENCH:"):
            return
        dlg = getattr(self, "_gif_perf_dialog", None)
        if dlg is not None:
            try:
                dlg.handle_esp_bench_line(text)
            except Exception:
                pass
        if text.startswith("BENCH:RESULT"):
            try:
                self.statusBar().showMessage(text, 5000)
            except Exception:
                pass


    def _cycle_mode(self):
        if not hasattr(self, "comboPot"):
            return

        idx = self.comboPot.currentIndex()
        if idx < 0:
            idx = 0
        idx = (idx + 1) % self.comboPot.count()
        self.comboPot.setCurrentIndex(idx)

        self.save_extra_inputs()

        self.statusBar().showMessage(
            f"Režim enkódera: {self.comboPot.currentText()}",
            1500,
        )

    def _process_encoder_line(self, line: str):
        s = line.strip()
        count = 0

        if s.endswith("+1"):
            count = +1
        elif s.endswith("-1"):
            count = -1
        else:
            parts = s.split(":")
            if len(parts) >= 2:
                try:
                    v = int(parts[1].strip())
                    if v != 0:
                        count = v
                except Exception:
                    return
            else:
                return

        if count == 0:
            return

        count = -count
        self._queue_encoder_count(count)

    def _queue_encoder_count(self, count: int) -> None:
        if count == 0:
            return
        self._encoder_pending += int(count)
        if not self._encoder_flush_timer.isActive():
            self._encoder_flush_timer.start()

    def _flush_encoder_pending(self) -> None:
        pending = int(self._encoder_pending)
        self._encoder_pending = 0
        if pending == 0:
            return
        sign = 1 if pending > 0 else -1
        steps = abs(pending)
        if steps > self._encoder_max_steps_per_flush:
            remaining = steps - self._encoder_max_steps_per_flush
            self._encoder_pending = sign * remaining
            pending = sign * self._encoder_max_steps_per_flush
        self._apply_encoder_count(pending)
        if self._encoder_pending != 0:
            self._encoder_flush_timer.start()

    def _apply_encoder_count(self, count: int) -> None:
        if count == 0:
            return
        prev_min_interval = self.encoder_handler._min_detent_interval_ms
        prev_debounce = self.encoder_handler._debounce_time_ms
        try:
            # spracuj batch detentov bez straty kvôli debounce
            self.encoder_handler._min_detent_interval_ms = 1
            self.encoder_handler._debounce_time_ms = 1

            steps_left = abs(count)
            sign = +1 if count > 0 else -1
            while steps_left > 0:
                s_acc = self.encoder_handler.process_encoder_with_acceleration(sign)
                if s_acc > 0:
                    self._on_valid_detent(sign, s_acc)
                else:
                    self.encoder_handler._last_detent_time = 0
                    self._on_valid_detent(sign, 1)
                steps_left -= 1
        finally:
            self.encoder_handler._min_detent_interval_ms = prev_min_interval
            self.encoder_handler._debounce_time_ms = prev_debounce

    # ---------- MediaWidget actions ----------

    def _media_prev(self):
        now = time.monotonic()
        if _debounced(self._cmd_last_ts, "MEDIA_PREV", now):
            if IS_WINDOWS and self.media is not None and VK:
                self.media.send_media_vk(VK["PREV"])
            else:
                self.media_backend.prev_track()

    def _media_playpause(self):
        now = time.monotonic()
        if _debounced(self._cmd_last_ts, "MEDIA_PLAY_PAUSE", now):
            if IS_WINDOWS and self.media is not None and VK:
                self.media.send_media_vk(VK["PLAY_PAUSE"])
            else:
                self.media_backend.play_pause()

    def _media_next(self):
        now = time.monotonic()
        if _debounced(self._cmd_last_ts, "MEDIA_NEXT", now):
            if IS_WINDOWS and self.media is not None and VK:
                self.media.send_media_vk(VK["NEXT"])
            else:
                self.media_backend.next_track()

    def _on_valid_detent(self, sign: int, steps: int = 1):
        knob_mode = self._combo_selected_value(self.comboPot).strip()

        if knob_mode == "Volume":
            if IS_WINDOWS and self.media is not None:
                current = self.media.get_volume()
                base = self.volume_step
                if current > 0.8:
                    base *= 0.5
                delta = sign * base
                self.media.change_volume(delta)
                new_vol = int(round(self.media.get_volume() * 100))
                self.statusBar().showMessage(f"🔊 Hlasitosť: {new_vol}%", 800)
            else:
                # Linux / iné OS – používame backend v percentách
                delta_pct = sign * 3  # 3 % na "cvak"
                self.media_backend.change_volume(delta_pct)
                self.statusBar().showMessage("🔊 Hlasitosť upravená", 800)


        elif knob_mode in ("Brightness", "BrightnessAbs"):
            acceleration_factor = min(max(1, steps), 3)
            delta = sign * self.brightness_step * acceleration_factor
            if IS_WINDOWS and self.media is not None:
                self.media.change_brightness(int(delta))
            else:
                self.media_backend.change_brightness(int(delta))
            self.statusBar().showMessage("💡 Jas upravený", 800)

        elif knob_mode == "BrightnessRel":
            acceleration_factor = min(max(1, steps), 3)
            delta = sign * self.brightness_step_fine * acceleration_factor
            if IS_WINDOWS and self.media is not None:
                self.media.change_brightness(int(delta))
            else:
                self.media_backend.change_brightness(int(delta))
            self.statusBar().showMessage("💡 Jas (fine) upravený", 800)

        self._last_encoder_ms = int(time.monotonic() * 1000)

    def handle_device_event(self, data: dict):
        event_type = data.get("event")
        if event_type == "ENCODER":
            direction = data.get("dir")
            sign = +1 if direction == "CW" else -1
            sign = -sign
            steps = self.encoder_handler.process_encoder_with_acceleration(sign)
            if steps > 0:
                self._on_valid_detent(sign, steps)
        elif event_type == "BTN" and data.get("state") == "DOWN":
            btn_id = data.get("id")
            if btn_id == "ENC_SW":
                self._cycle_mode()
            elif btn_id == "A":
                self.execute_special_action(self._combo_selected_value(self.comboBtnA))
            elif btn_id == "B":
                self.execute_special_action(self._combo_selected_value(self.comboBtnB))

    def _confirm_risky_action(self, action_type: str) -> bool:
        labels = {
            "LockPC": "Uzamknúť PC",
            "SleepPC": "Uspať PC",
            "ShutdownPC": "Vypnúť PC",
            "RestartPC": "Reštartovať PC",
        }
        action_label = labels.get(action_type)
        if not action_label:
            return True
        if not bool(getattr(self, "_confirm_risky_actions", True)):
            return True

        now = time.monotonic()
        cache = getattr(self, "_risky_action_confirm_until", {})
        if now < float(cache.get(action_type, 0.0)):
            return True

        details = {
            "LockPC": "Počítač sa okamžite uzamkne.",
            "SleepPC": "Počítač sa okamžite uspí.",
            "ShutdownPC": "Počítač sa vypne a aplikácie sa ukončia.",
            "RestartPC": "Počítač sa reštartuje a aplikácie sa ukončia.",
        }
        msg = (
            f"Naozaj chceš vykonať akciu „{action_label}“?\n\n"
            f"{details.get(action_type, '')}"
        )
        choice = QMessageBox.question(
            self,
            "Potvrdenie rizikovej akcie",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            self.statusBar().showMessage(f"Akcia '{action_label}' zrušená.", 1800)
            return False

        cache[action_type] = now + max(0.0, float(getattr(self, "_risky_action_grace_s", 1.5)))
        self._risky_action_confirm_until = cache
        return True

    def handle_system_action(self, action_type: str, action_value: str | None = None) -> None:
        """Mapovanie akcií z ESP / špeciálnych tlačidiel na backend (cross-platform)."""
        now = time.monotonic()

        if action_type == "PlayMusic":
            if _debounced(self._cmd_last_ts, "PLAY_PAUSE", now):
                self.media_backend.play_pause()
            return

        if action_type == "Mute":
            if _debounced(self._cmd_last_ts, "MUTE", now):
                self.media_backend.toggle_mute()
            return

        if action_type == "OpenURL":
            if _debounced(self._cmd_last_ts, "OPEN_URL", now):
                url = (action_value or "").strip()
                if url and not url.lower().startswith(("http://", "https://")):
                    url = f"https://{url}"
                if url:
                    open_path_smart(url)
            return

        if action_type == "MuteMic":
            if _debounced(self._cmd_last_ts, "MUTE_MIC", now):
                self._set_mic_mute_state(True)
            return

        if action_type == "UnmuteMic":
            if _debounced(self._cmd_last_ts, "UNMUTE_MIC", now):
                self._set_mic_mute_state(False)
            return

        if action_type == "Next":
            if _debounced(self._cmd_last_ts, "NEXT", now):
                self.media_backend.next_track()
            return

        if action_type == "Previous":
            if _debounced(self._cmd_last_ts, "PREV", now):
                self.media_backend.prev_track()
            return

        if action_type == "SpotifyPlaylist":
            if _debounced(self._cmd_last_ts, "SPOTIFY_PLAYLIST", now):
                self._play_spotify_playlist(action_value)
            return

        if action_type == "BrightnessUp":
            if IS_WINDOWS and self.media is not None:
                self.media.change_brightness(+self.brightness_step)
            else:
                self.media_backend.change_brightness(+self.brightness_step)
            return

        if action_type == "BrightnessDown":
            if IS_WINDOWS and self.media is not None:
                self.media.change_brightness(-self.brightness_step)
            else:
                self.media_backend.change_brightness(-self.brightness_step)
            return

        if action_type == "OpenApp" and action_value:
            # cross-platform – backend + open_path_smart ako fallback
            self.media_backend.open_app(action_value)
            return

        if action_type == "SendKeys" and action_value:
            self.media_backend.send_keys(action_value)
            return

        if action_type == "MinimizeWindow":
            self.media_backend.minimize_active_window()
            return

        if action_type == "MaximizeWindow":
            self.media_backend.toggle_maximize_active_window()
            return

        if action_type == "CopyToClipboard":
            self._copy_to_clipboard(action_value or "")
            return

        if action_type == "PasteClipboard":
            if IS_WINDOWS and win32api is not None and win32con is not None:
                win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                win32api.keybd_event(ord('V'), 0, 0, 0)
                win32api.keybd_event(ord('V'), 0, win32con.KEYEVENTF_KEYUP, 0)
                win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
            else:
                self.media_backend.send_keys("ctrl+v")
            return

        if action_type == "CloseWindow":
            if IS_WINDOWS and win32api is not None and win32con is not None:
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                win32api.keybd_event(win32con.VK_F4, 0, 0, 0)
                win32api.keybd_event(win32con.VK_F4, 0, win32con.KEYEVENTF_KEYUP, 0)
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            elif IS_LINUX:
                if os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland":
                    self.media_backend.send_keys("alt+F4")
                elif not self._run_linux_command_candidates(
                    "CloseWindow",
                    [
                        ["wmctrl", "-c", ":ACTIVE:"],
                        ["xdotool", "key", "alt+F4"],
                    ],
                ):
                    self.media_backend.send_keys("alt+F4")
            else:
                self.media_backend.send_keys("alt+F4")
            return

        if action_type == "SwitchWindow":
            if IS_WINDOWS and win32api is not None and win32con is not None:
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                win32api.keybd_event(win32con.VK_TAB, 0, 0, 0)
                win32api.keybd_event(win32con.VK_TAB, 0, win32con.KEYEVENTF_KEYUP, 0)
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            elif IS_LINUX:
                if os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland":
                    self.media_backend.send_keys("alt+Tab")
                elif not self._run_linux_command_candidates(
                    "SwitchWindow",
                    [
                        ["xdotool", "key", "alt+Tab"],
                    ],
                ):
                    self.media_backend.send_keys("alt+Tab")
            else:
                self.media_backend.send_keys("alt+Tab")
            return

        if action_type in {"LockPC", "SleepPC", "ShutdownPC", "RestartPC"}:
            if not self._confirm_risky_action(action_type):
                return

        if action_type == "LockPC":
            if IS_WINDOWS:
                import ctypes
                ctypes.windll.user32.LockWorkStation()
            elif IS_LINUX:
                self._run_linux_command_candidates(
                    "LockPC",
                    [
                        ["loginctl", "lock-session"],
                        ["xdg-screensaver", "lock"],
                        ["gnome-screensaver-command", "-l"],
                        ["dm-tool", "lock"],
                    ],
                )
            return

        if action_type == "SleepPC":
            if IS_WINDOWS:
                subprocess.run(
                    ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                    shell=False,
                )
            elif IS_LINUX:
                self._run_linux_command_candidates(
                    "SleepPC",
                    [
                        ["systemctl", "suspend"],
                        ["loginctl", "suspend"],
                    ],
                )
            return

        if action_type == "ShutdownPC":
            if IS_WINDOWS:
                subprocess.run(["shutdown", "/s", "/t", 0], shell=False)
            elif IS_LINUX:
                self._run_linux_command_candidates(
                    "ShutdownPC",
                    [
                        ["systemctl", "poweroff"],
                        ["shutdown", "-h", "now"],
                    ],
                )
            return

        if action_type == "RestartPC":
            if IS_WINDOWS:
                subprocess.run(["shutdown", "/r", "/t", 0], shell=False)
            elif IS_LINUX:
                self._run_linux_command_candidates(
                    "RestartPC",
                    [
                        ["systemctl", "reboot"],
                        ["shutdown", "-r", "now"],
                    ],
                )
            return


    def handle_serial_command(self, line: str):
        print(f"Prijatý príkaz: {line}")

        action, value = "", ""
        if ":" in line:
            parts = line.strip().split(":", 1)
            action = parts[0].strip()
            value = parts[1].strip() if len(parts) > 1 else ""
        else:
            action = line.strip()

        action = self._normalize_action(action)

        if action in {
            "PlayMusic", "Mute", "Next", "Previous",
            "MuteMic", "UnmuteMic",
            "SpotifyPlaylist",
            "OpenURL",
            "BrightnessUp", "BrightnessDown",
            "OpenApp", "SendKeys",
            "MinimizeWindow", "MaximizeWindow",
            "CloseWindow", "SwitchWindow",
            "LockPC", "SleepPC", "ShutdownPC", "RestartPC",
            "CopyToClipboard", "PasteClipboard",
        }:
            self.handle_system_action(action, value or None)
            return

        if action == "LOADED":
            return

        if action == "MIX" and value:
            self._handle_mixer_command(value)
            return

        if action == "HTTPRequest" and value:
            self._queue_http_request(value)
            return

        if action == "DiscordWebhook":
            self._trigger_discord_webhook(value)
            return

        if action == "WeatherWidget":
            self._queue_weather_sync(force=True)
            return

        if action == "MetricWidget":
            self._refresh_metric_preview_in_app()
            self._send_metric_widgets_to_esp(force=True)
            return

        if action == "NextProfile":
            row = self.listProfiles.currentRow()
            total = self.listProfiles.count()
            if total:
                self.listProfiles.setCurrentRow((row + 1) % total)
            return

        if action == "PreviousProfile":
            row = self.listProfiles.currentRow()
            total = self.listProfiles.count()
            if total:
                self.listProfiles.setCurrentRow((row - 1 + total) % total)
            return

        if action == "SwitchProfile":
            if value:
                # switch to profile by name
                items = self.listProfiles.findItems(value, Qt.MatchFlag.MatchExactly)
                if items:
                    self.listProfiles.setCurrentItem(items[0])
                else:
                    print(f"SwitchProfile: profil '{value}' neexistuje")
            else:
                row = self.listProfiles.currentRow()
                total = self.listProfiles.count()
                if total:
                    self.listProfiles.setCurrentRow((row + 1) % total)
            return


        if action == "GoBack":

            self.handle_button_b()
            return
        if action == "SmartRelay1Toggle":
            self._send_smarthome_toggle(1)
            return

        if action == "SmartRelay2Toggle":
            self._send_smarthome_toggle(2)
            return

        if action == "SmartRelay3Toggle":
            self._send_smarthome_toggle(3)
            return

        if action == "SmartRelay4Toggle":
            self._send_smarthome_toggle(4)
            return

 # ----- špeciálne správy pre monitor -----
        if action == "FPS" and value:
            try:
                v = float(value)
            except Exception:
                v = None
            if hasattr(self, "system_stats") and v is not None:
                self.system_stats.set_fps(v)
            if hasattr(self, "monitor_widget") and v is not None:
                self.monitor_widget.set_fps(f"{v:.0f}")
            return

        if action == "GPU" and value:
            # napr. "GPU:41;62"  => percent;temp
            parts = value.split(";")
            pct = temp = None
            if len(parts) >= 1:
                try:
                    pct = float(parts[0])
                except Exception:
                    pct = None
            if len(parts) >= 2:
                try:
                    temp = float(parts[1])
                except Exception:
                    temp = None

            if hasattr(self, "monitor_widget"):
                # percent pre bar
                percent_val = pct if isinstance(pct, (int, float)) else 0.0

                primary = f"{percent_val:.0f} %" if pct is not None else "-- %"
                secondary = f"{temp:.0f} °C" if temp is not None else "-- °C"

                self.monitor_widget.set_gpu(percent_val, primary, secondary)

            # ak chceš zároveň nakŕmiť aj SystemStatsProvider:
            if hasattr(self, "system_stats") and pct is not None:
                try:
                    self.system_stats.set_gpu(pct, temp)
                except Exception:
                    pass

            return


        print(f"Neznámy príkaz: {line}")

    def _parse_http_request_spec(self, raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            raise ValueError("HTTP Request je prázdny.")

        allowed_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
        method = "GET"
        url = ""
        json_payload: Any = None
        headers: dict[str, str] | None = None

        if text.startswith("{"):
            try:
                obj = json.loads(text)
            except Exception as e:
                raise ValueError(f"Neplatný JSON špecifikácie: {e}") from e
            if not isinstance(obj, dict):
                raise ValueError("JSON špecifikácia musí byť objekt.")
            method = str(obj.get("method", "GET") or "GET").upper()
            url = str(obj.get("url", "") or "").strip()
            json_payload = obj.get("json", obj.get("payload"))
            headers_raw = obj.get("headers")
            if headers_raw is not None:
                if not isinstance(headers_raw, dict):
                    raise ValueError("Pole 'headers' musí byť JSON objekt.")
                headers = {str(k): str(v) for k, v in headers_raw.items()}
        else:
            m = re.match(
                r"^(GET|POST|PUT|PATCH|DELETE)\s+(\S+)(?:\s+(.+))?$",
                text,
                re.IGNORECASE,
            )
            if m:
                method = str(m.group(1) or "GET").upper()
                url = (m.group(2) or "").strip()
                rest = (m.group(3) or "").strip()
                if rest:
                    if rest.lower().startswith("json="):
                        rest = rest[5:].strip()
                    try:
                        json_payload = json.loads(rest)
                    except Exception as e:
                        raise ValueError(f"Neplatný JSON payload: {e}") from e
            else:
                # Backward compatible format: plain URL -> GET
                url = text
                method = "GET"

        if method not in allowed_methods:
            raise ValueError(f"Nepodporovaná HTTP metóda: {method}")

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("URL musí začínať http:// alebo https:// a obsahovať host.")

        spec: dict[str, Any] = {
            "method": method,
            "url": url,
        }
        if json_payload is not None:
            spec["json"] = json_payload
        if headers:
            spec["headers"] = headers
        return spec

    def _parse_discord_webhook_spec(
        self,
        raw: str,
        fallback_content: str | None = None,
    ) -> dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            raise ValueError("Pre Discord Webhook zadaj webhook URL.")

        url = ""
        payload: dict[str, Any] = {}

        if text.startswith("{"):
            try:
                obj = json.loads(text)
            except Exception as e:
                raise ValueError(f"Neplatný JSON webhook špecifikácie: {e}") from e
            if not isinstance(obj, dict):
                raise ValueError("Webhook špecifikácia musí byť JSON objekt.")
            url = str(obj.get("url", "") or "").strip()
            for key in ("content", "username", "avatar_url", "tts", "embeds", "allowed_mentions"):
                if key in obj and obj.get(key) not in (None, ""):
                    payload[key] = obj.get(key)
        else:
            msg = ""
            if "||" in text:
                left, right = text.split("||", 1)
                url = left.strip()
                msg = right.strip()
            else:
                m = re.match(r"^(https?://\S+)(?:\s+(.+))?$", text)
                if m:
                    url = (m.group(1) or "").strip()
                    msg = (m.group(2) or "").strip()
                else:
                    url = text
            if not msg:
                msg = (fallback_content or "").strip() or "MacroTouch trigger"
            payload["content"] = msg

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Discord webhook URL musí začínať http:// alebo https://.")

        low = url.lower()
        if "/api/webhooks/" not in low or ("discord.com" not in low and "discordapp.com" not in low):
            raise ValueError("Zadaj platný Discord webhook URL.")

        if "content" not in payload and "embeds" not in payload:
            payload["content"] = (fallback_content or "").strip() or "MacroTouch trigger"

        return {
            "method": "POST",
            "url": url,
            "json": payload,
        }

    def _queue_http_request(self, request_spec: str) -> None:
        threading.Thread(
            target=self._fire_http_request,
            args=(request_spec,),
            daemon=True,
        ).start()

    def _trigger_discord_webhook(self, raw: str, fallback_content: str | None = None) -> None:
        try:
            spec = self._parse_discord_webhook_spec(raw, fallback_content=fallback_content)
        except ValueError as e:
            self.statusBar().showMessage(f"Discord webhook: {e}", 3200)
            print(f"[DiscordWebhook] validation error: {e}")
            return
        self._queue_http_request(json.dumps(spec, ensure_ascii=False))

    def _fire_http_request(self, request_spec: str) -> None:
        """Spustí HTTP požiadavku mimo GUI vlákna, aby UI nezamŕzalo."""
        try:
            spec = self._parse_http_request_spec(request_spec)
        except Exception as e:
            print(f"[HTTP] invalid request spec: {e} | raw={request_spec}")
            return

        method = str(spec.get("method", "GET")).upper()
        url = str(spec.get("url", "")).strip()
        kwargs: dict[str, Any] = {"timeout": 8}
        if "json" in spec:
            kwargs["json"] = spec["json"]
        if "headers" in spec:
            kwargs["headers"] = spec["headers"]

        try:
            import requests  # type: ignore
            resp = requests.request(method, url, **kwargs)
            if resp.status_code >= 400:
                body = (resp.text or "").strip().replace("\n", " ")
                if len(body) > 220:
                    body = body[:220] + "..."
                print(f"[HTTP] {method} {url} -> {resp.status_code} {body}")
            else:
                print(f"[HTTP] {method} {url} -> {resp.status_code}")
        except Exception as e:
            print(f"HTTP Error: {e} (spec={request_spec})")

    def _serial_ready_for_weather(self) -> bool:
        if not hasattr(self, "serial_monitor"):
            return False
        ser = getattr(self.serial_monitor, "ser", None)
        return bool(ser is not None and ser.is_open)

    def _weather_clean_field(self, value: Any, max_len: int = 24) -> str:
        text = str(value or "")
        text = re.sub(r"[\r\n;=]+", " ", text).strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) > max_len:
            text = text[:max_len].rstrip()
        return text

    def _weather_desc_from_code(self, code: int) -> str:
        mapping = {
            0: "Clear",
            1: "Mostly clear",
            2: "Partly cloudy",
            3: "Cloudy",
            45: "Fog",
            48: "Rime fog",
            51: "Light drizzle",
            53: "Drizzle",
            55: "Heavy drizzle",
            56: "Freezing drizzle",
            57: "Heavy freezing drizzle",
            61: "Light rain",
            63: "Rain",
            65: "Heavy rain",
            66: "Freezing rain",
            67: "Heavy freezing rain",
            71: "Light snow",
            73: "Snow",
            75: "Heavy snow",
            77: "Snow grains",
            80: "Rain showers",
            81: "Heavy showers",
            82: "Violent showers",
            85: "Snow showers",
            86: "Heavy snow showers",
            95: "Thunderstorm",
            96: "Thunder and hail",
            99: "Severe thunderstorm",
        }
        return mapping.get(int(code), "Weather")

    def _weather_category_from_code(self, code: int | float | None) -> str:
        try:
            c = int(round(float(code or 0)))
        except Exception:
            c = 0
        if c == 0:
            return "clear"
        if c in (1, 2, 3):
            return "cloudy"
        if c in (45, 48):
            return "fog"
        if 51 <= c <= 67 or 80 <= c <= 82:
            return "rain"
        if 71 <= c <= 77 or c in (85, 86):
            return "snow"
        if c >= 95:
            return "storm"
        return "cloudy"

    def _weather_palette_for_category(self, category: str) -> tuple[str, str, str]:
        palettes = {
            "clear": ("#0B6DDC", "#36A8F6", "#FDE68A"),
            "cloudy": ("#304860", "#5C6F87", "#E2E8F0"),
            "rain": ("#14416A", "#1F6EA5", "#93C5FD"),
            "snow": ("#3F6B98", "#7AA4CD", "#F0F9FF"),
            "storm": ("#4A2A67", "#6F3F9A", "#FDE047"),
            "fog": ("#4A5A72", "#667892", "#E5E7EB"),
        }
        return palettes.get(category, palettes["cloudy"])

    def _make_weather_icon(self, category: str, size: int, accent_hex: str) -> QIcon:
        side = max(20, min(128, int(size)))
        key = (category, side, accent_hex)
        cached = self._icon_render_cache.get(key)
        if cached is not None:
            return cached

        pm = QPixmap(side, side)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        accent = QColor(accent_hex)
        cloud = QColor("#EAF3FF")
        cloud_soft = QColor("#C9DBF4")

        def draw_cloud(cx: float, cy: float, scale: float = 1.0) -> None:
            w = side * 0.64 * scale
            h = side * 0.30 * scale
            x = cx - w / 2.0
            y = cy - h / 2.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(cloud)
            p.drawRoundedRect(QRectF(x, y + h * 0.35, w, h * 0.65), h * 0.32, h * 0.32)
            p.drawEllipse(QRectF(x + w * 0.08, y + h * 0.18, w * 0.30, h * 0.58))
            p.drawEllipse(QRectF(x + w * 0.31, y, w * 0.36, h * 0.72))
            p.drawEllipse(QRectF(x + w * 0.56, y + h * 0.20, w * 0.30, h * 0.58))

        if category == "clear":
            c = QPointF(side * 0.50, side * 0.48)
            r = side * 0.20
            p.setPen(QPen(QColor("#FFF5BF"), max(2, side // 14)))
            for i in range(8):
                ang = (i * 45.0) * 3.14159265 / 180.0
                inner = QPointF(c.x() + (r + side * 0.06) * float(math.cos(ang)),
                                c.y() + (r + side * 0.06) * float(math.sin(ang)))
                outer = QPointF(c.x() + (r + side * 0.15) * float(math.cos(ang)),
                                c.y() + (r + side * 0.15) * float(math.sin(ang)))
                p.drawLine(inner, outer)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            p.drawEllipse(c, r, r)
        elif category == "cloudy":
            draw_cloud(side * 0.50, side * 0.52, 1.0)
        elif category == "fog":
            draw_cloud(side * 0.50, side * 0.46, 1.0)
            p.setPen(QPen(cloud_soft, max(2, side // 14), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i in range(3):
                y = side * (0.62 + 0.10 * i)
                p.drawLine(QPointF(side * 0.24, y), QPointF(side * 0.76, y))
        elif category == "rain":
            draw_cloud(side * 0.50, side * 0.44, 1.0)
            p.setPen(QPen(accent, max(2, side // 14), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i in range(3):
                x = side * (0.36 + 0.14 * i)
                p.drawLine(QPointF(x, side * 0.63), QPointF(x - side * 0.05, side * 0.80))
        elif category == "snow":
            draw_cloud(side * 0.50, side * 0.44, 1.0)
            p.setPen(QPen(accent, max(2, side // 15), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i in range(3):
                cx = side * (0.36 + 0.14 * i)
                cy = side * 0.75
                p.drawLine(QPointF(cx - side * 0.035, cy), QPointF(cx + side * 0.035, cy))
                p.drawLine(QPointF(cx, cy - side * 0.035), QPointF(cx, cy + side * 0.035))
        else:  # storm
            draw_cloud(side * 0.50, side * 0.42, 1.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(accent)
            path = QPainterPath()
            path.moveTo(side * 0.48, side * 0.58)
            path.lineTo(side * 0.62, side * 0.58)
            path.lineTo(side * 0.52, side * 0.74)
            path.lineTo(side * 0.62, side * 0.74)
            path.lineTo(side * 0.42, side * 0.94)
            path.lineTo(side * 0.50, side * 0.76)
            path.lineTo(side * 0.40, side * 0.76)
            path.closeSubpath()
            p.drawPath(path)

        p.end()
        icon = QIcon(pm)
        self._cache_put(self._icon_render_cache, key, icon, self._icon_render_cache_limit)
        return icon

    def _parse_weather_widget_spec(self, raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            return {
                "lat": None,
                "lon": None,
                "query": "Bratislava",
                "label": "Bratislava",
            }

        if text.startswith("{"):
            try:
                obj = json.loads(text)
            except Exception as e:
                raise ValueError(f"Neplatný JSON pre Weather Widget: {e}") from e
            if not isinstance(obj, dict):
                raise ValueError("Weather Widget špecifikácia musí byť JSON objekt.")
            lat_raw = obj.get("lat", obj.get("latitude"))
            lon_raw = obj.get("lon", obj.get("longitude"))
            query = str(obj.get("query", obj.get("city", obj.get("location", ""))) or "").strip()
            label = str(obj.get("label", query) or "").strip()
            lat = lon = None
            if lat_raw not in (None, "") or lon_raw not in (None, ""):
                try:
                    lat = float(lat_raw)
                    lon = float(lon_raw)
                except Exception as e:
                    raise ValueError("Pole 'lat/lon' musí byť číslo.") from e
                if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                    raise ValueError("lat/lon sú mimo rozsahu.")
            if lat is None and not query:
                raise ValueError("Zadaj aspoň city/query alebo lat/lon.")
            return {
                "lat": lat,
                "lon": lon,
                "query": query,
                "label": self._weather_clean_field(label, max_len=24),
            }

        m = re.match(
            r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)(?:\s*\|\s*(.+))?$",
            text,
        )
        if m:
            lat = float(m.group(1))
            lon = float(m.group(2))
            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                raise ValueError("lat/lon sú mimo rozsahu.")
            label = self._weather_clean_field((m.group(3) or "").strip(), max_len=24)
            if not label:
                label = f"{lat:.4f},{lon:.4f}"
            return {
                "lat": lat,
                "lon": lon,
                "query": "",
                "label": label,
            }

        if "|" in text:
            query, label = text.split("|", 1)
            query = query.strip()
            label = label.strip()
        else:
            query = text
            label = text
        query = self._weather_clean_field(query, max_len=48)
        label = self._weather_clean_field(label, max_len=24)
        if not query:
            raise ValueError("Zadaj mesto alebo lat,lon.")
        return {
            "lat": None,
            "lon": None,
            "query": query,
            "label": label or query,
        }

    def _parse_metric_widget_spec(self, raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            return {"key": "CPU", "label": METRIC_WIDGET_DEFAULT_LABELS["CPU"]}

        metric_key = ""
        label = ""
        if text.startswith("{"):
            try:
                obj = json.loads(text)
            except Exception as e:
                raise ValueError(f"Neplatný JSON pre Metric Widget: {e}") from e
            if not isinstance(obj, dict):
                raise ValueError("Metric Widget špecifikácia musí byť JSON objekt.")
            metric_key = str(obj.get("key", obj.get("metric", obj.get("value", ""))) or "").strip()
            label = str(obj.get("label", "") or "").strip()
        else:
            if "|" in text:
                metric_key, label = text.split("|", 1)
            else:
                metric_key = text
                label = ""
            metric_key = metric_key.strip()
            label = label.strip()

        key = self._normalize_metric_key(metric_key)
        if not key:
            raise ValueError(
                "Neplatný kľúč metriky. Použi CPU, RAM, GPU, GPU_TEMP, FPS, NET, DISK alebo CPU_GHZ."
            )

        clean_label = self._weather_clean_field(label, max_len=20)
        if not clean_label:
            clean_label = METRIC_WIDGET_DEFAULT_LABELS.get(key, key)
        return {"key": key, "label": clean_label}

    def _collect_metric_widget_specs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        profiles = getattr(self.profile_manager, "profiles", {})
        if not isinstance(profiles, dict):
            return out
        for profile_name, prof in profiles.items():
            if not isinstance(prof, dict):
                continue
            if prof.get("mode", "grid") != "grid":
                continue
            rows = max(1, min(4, int(prof.get("rows", 3))))
            cols = max(1, min(4, int(prof.get("cols", 4))))
            anchors, _cell_to_anchor = self._resolve_grid_layout(prof, rows, cols)
            for entry in anchors:
                btn = entry.get("data", {})
                if not isinstance(btn, dict):
                    continue
                action_key = self._normalize_action(str(btn.get("action", "") or ""))
                if action_key != "MetricWidget":
                    continue
                try:
                    spec = self._parse_metric_widget_spec(str(btn.get("path", "") or ""))
                except ValueError as e:
                    print(f"[Metric] invalid widget spec in '{profile_name}/{entry.get('name', '')}': {e}")
                    continue
                spec["profile"] = profile_name
                spec["button"] = str(entry.get("name", "") or "")
                out.append(spec)
        return out

    def _collect_weather_widget_specs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        profiles = getattr(self.profile_manager, "profiles", {})
        if not isinstance(profiles, dict):
            return out
        for profile_name, prof in profiles.items():
            if not isinstance(prof, dict):
                continue
            if prof.get("mode", "grid") != "grid":
                continue
            rows = max(1, min(4, int(prof.get("rows", 3))))
            cols = max(1, min(4, int(prof.get("cols", 4))))
            anchors, _cell_to_anchor = self._resolve_grid_layout(prof, rows, cols)
            for entry in anchors:
                btn = entry.get("data", {})
                if not isinstance(btn, dict):
                    continue
                action_key = self._normalize_action(str(btn.get("action", "") or ""))
                if action_key != "WeatherWidget":
                    continue
                try:
                    spec = self._parse_weather_widget_spec(str(btn.get("path", "") or ""))
                except ValueError as e:
                    print(f"[Weather] invalid widget spec in '{profile_name}/{entry.get('name', '')}': {e}")
                    continue
                spec["profile"] = profile_name
                spec["button"] = str(entry.get("name", "") or "")
                out.append(spec)
        return out

    def _select_weather_widget_spec(self) -> dict[str, Any] | None:
        specs = self._collect_weather_widget_specs()
        if not specs:
            return None
        current = self.profile_manager.current_profile
        for spec in specs:
            if spec.get("profile") == current:
                return spec
        return specs[0]

    def _resolve_weather_location(self, spec: dict[str, Any]) -> tuple[float, float, str]:
        lat = spec.get("lat")
        lon = spec.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            label = self._weather_clean_field(spec.get("label", ""), max_len=24)
            if not label:
                label = f"{float(lat):.4f},{float(lon):.4f}"
            return float(lat), float(lon), label

        query = self._weather_clean_field(spec.get("query", ""), max_len=48)
        if not query:
            raise ValueError("Weather query je prázdny.")

        cache_key = query.lower()
        cached = self._weather_geocode_cache.get(cache_key)
        if cached is not None:
            return cached

        import requests  # type: ignore

        resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={
                "name": query,
                "count": 1,
                "language": "en",
                "format": "json",
            },
            timeout=8,
        )
        resp.raise_for_status()
        payload = resp.json() if hasattr(resp, "json") else {}
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            raise ValueError(f"Mesto '{query}' nebolo nájdené.")

        first = results[0] if isinstance(results[0], dict) else {}
        lat_v = float(first.get("latitude"))
        lon_v = float(first.get("longitude"))
        label = self._weather_clean_field(
            spec.get("label")
            or first.get("name")
            or query,
            max_len=24,
        )
        if not label:
            label = query
        resolved = (lat_v, lon_v, label)
        self._weather_geocode_cache[cache_key] = resolved
        return resolved

    def _fetch_weather_payload(self, spec: dict[str, Any]) -> dict[str, Any]:
        lat, lon, label = self._resolve_weather_location(spec)

        import requests  # type: ignore

        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": f"{lat:.6f}",
                "longitude": f"{lon:.6f}",
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
                "wind_speed_unit": "ms",
                "timezone": "auto",
            },
            timeout=8,
        )
        resp.raise_for_status()
        payload = resp.json() if hasattr(resp, "json") else {}
        current = payload.get("current") if isinstance(payload, dict) else {}
        if not isinstance(current, dict):
            current = {}
        fallback = payload.get("current_weather") if isinstance(payload, dict) else {}
        if not isinstance(fallback, dict):
            fallback = {}

        def _float_value(*keys: str, default: float = 0.0) -> float:
            for key in keys:
                src = current.get(key)
                if src is None:
                    src = fallback.get(key)
                if src is None:
                    continue
                try:
                    return float(src)
                except Exception:
                    continue
            return float(default)

        def _int_value(*keys: str, default: int = 0) -> int:
            for key in keys:
                src = current.get(key)
                if src is None:
                    src = fallback.get(key)
                if src is None:
                    continue
                try:
                    return int(round(float(src)))
                except Exception:
                    continue
            return int(default)

        temp = _float_value("temperature_2m", "temperature", default=0.0)
        feels = _float_value("apparent_temperature", "temperature_2m", "temperature", default=temp)
        humidity = _int_value("relative_humidity_2m", default=-1)
        wind = _float_value("wind_speed_10m", "windspeed", default=0.0)
        code = _int_value("weather_code", "weathercode", default=0)
        desc = self._weather_desc_from_code(code)

        return {
            "temp": temp,
            "feels": feels,
            "humidity": humidity,
            "wind": wind,
            "code": code,
            "label": self._weather_clean_field(label, max_len=24),
            "desc": self._weather_clean_field(desc, max_len=24),
        }

    def _build_weather_widget_line(self, payload: dict[str, Any]) -> str:
        temp = float(payload.get("temp", 0.0) or 0.0)
        feels = float(payload.get("feels", temp) or temp)
        humidity = int(payload.get("humidity", -1) or -1)
        wind = float(payload.get("wind", 0.0) or 0.0)
        code = int(payload.get("code", 0) or 0)
        label = self._weather_clean_field(payload.get("label", "Weather"), max_len=24)
        desc = self._weather_clean_field(payload.get("desc", "Weather"), max_len=24)

        line = (
            "WIDGET:TYPE=WEATHER;"
            f"T={temp:.1f};F={feels:.1f};H={humidity};W={wind:.1f};C={code};"
            f"L={label};D={desc}"
        )
        if len(line) > 210:
            cut = max(8, 210 - (len(line) - len(desc)))
            desc = desc[:cut].rstrip()
            line = (
                "WIDGET:TYPE=WEATHER;"
                f"T={temp:.1f};F={feels:.1f};H={humidity};W={wind:.1f};C={code};"
                f"L={label};D={desc}"
            )
        return line

    def _weather_payload_snapshot(self) -> dict[str, Any]:
        with self._weather_lock:
            payload = dict(self._weather_cached_payload or {})
        return payload

    def _fit_weather_text_line(self, text: str, max_chars: int) -> str:
        clean = self._weather_clean_field(text, max_len=max(8, max_chars * 3))
        if len(clean) <= max_chars:
            return clean
        if max_chars <= 4:
            return clean[:max_chars]
        return clean[: max_chars - 3].rstrip() + "..."

    def _marquee_weather_text(self, text: str, max_chars: int, key: str) -> str:
        clean = self._weather_clean_field(text, max_len=max(16, max_chars * 6))
        if len(clean) <= max_chars or max_chars < 4:
            return clean

        st = self._weather_marquee_state.get(key)
        if not isinstance(st, dict):
            st = {"pos": 0, "dir": 1, "hold": 2, "step": -1}
            self._weather_marquee_state[key] = st

        pos = int(st.get("pos", 0))
        direction = 1 if int(st.get("dir", 1)) >= 0 else -1
        hold = max(0, int(st.get("hold", 0)))
        last_step = int(st.get("step", -1))
        max_pos = max(0, len(clean) - max_chars)

        if self._weather_marquee_step != last_step:
            if hold > 0:
                hold -= 1
            else:
                pos += direction
                if pos <= 0:
                    pos = 0
                    direction = 1
                    hold = 2
                elif pos >= max_pos:
                    pos = max_pos
                    direction = -1
                    hold = 3
            st["pos"] = pos
            st["dir"] = direction
            st["hold"] = hold
            st["step"] = self._weather_marquee_step

        window = clean[pos : pos + max_chars]
        if pos > 0 and len(window) >= 3:
            window = "..." + window[3:]
        if pos < max_pos and len(window) >= 3:
            window = window[:-3] + "..."
        return window

    def _weather_short_desc(self, desc: str, max_words: int = 2) -> str:
        text = self._weather_clean_field(desc, max_len=48)
        if not text:
            return "Weather"
        words = text.split()
        if len(words) > max_words:
            text = " ".join(words[:max_words])
        return text

    def _weather_preview_text_for_button(
        self,
        btn_data: dict[str, Any],
        btn: QPushButton | None = None,
        btn_name: str | None = None,
    ) -> str:
        action_key = self._normalize_action(str(btn_data.get("action", "") or ""))
        base_name = str(btn_data.get("name", "") or "").strip()
        if action_key != "WeatherWidget":
            return base_name
        metrics = self._weather_layout_metrics(btn, btn_data)
        mode = str(metrics.get("mode", "compact"))
        line_chars = int(metrics.get("line_chars", 16))
        resolved_name = (btn_name or (btn.objectName() if isinstance(btn, QPushButton) else "")).strip()

        def line_text(text: str, width: int, slot: str, marquee: bool = False) -> str:
            if marquee and resolved_name:
                return self._marquee_weather_text(text, width, f"{resolved_name}:{slot}:{width}")
            return self._fit_weather_text_line(text, width)

        payload = self._weather_payload_snapshot()
        label = self._weather_clean_field(payload.get("label", ""), max_len=24)
        if not label:
            try:
                spec = self._parse_weather_widget_spec(str(btn_data.get("path", "") or ""))
                label = self._weather_clean_field(spec.get("label", ""), max_len=24)
            except Exception:
                label = ""
        if not label:
            label = base_name or "Weather"

        if not payload:
            if mode == "micro":
                return "--\nLoading"
            if mode == "row":
                return f"{line_text(label, line_chars, 'row_l1')}\nLoading..."
            return f"{line_text(label, line_chars, 'load_l1', marquee=True)}\nLoading weather..."

        try:
            temp = float(payload.get("temp", 0.0) or 0.0)
            temp_text = f"{temp:.0f} C"
        except Exception:
            temp_text = "--"
        try:
            feels = float(payload.get("feels", temp) or temp)
            feels_text = f"{feels:.0f} C"
        except Exception:
            feels_text = "--"
        try:
            hum = int(payload.get("humidity", -1) or -1)
        except Exception:
            hum = -1
        try:
            wind = float(payload.get("wind", 0.0) or 0.0)
        except Exception:
            wind = 0.0

        desc = self._weather_clean_field(payload.get("desc", ""), max_len=24)
        if not desc:
            try:
                desc = self._weather_desc_from_code(int(payload.get("code", 0) or 0))
            except Exception:
                desc = "Weather"
        desc_short = self._weather_short_desc(desc, max_words=2)

        if mode == "micro":
            temp_micro = temp_text.replace(" ", "")
            desc_micro = self._weather_short_desc(desc, max_words=1)
            if hum >= 0 and line_chars >= 11:
                desc_micro = f"{desc_micro} {hum}%"
            l1 = line_text(temp_micro, max(line_chars, 6), "micro_l1")
            l2 = line_text(desc_micro, max(line_chars, 6), "micro_l2")
            return f"{l1}\n{l2}"

        if mode == "narrow":
            l1 = line_text(f"{temp_text} {desc_short}", line_chars, "narrow_l1", marquee=True)
            meta = [f"F {feels_text.replace(' ', '')}"]
            if hum >= 0:
                meta.append(f"H {hum}%")
            if wind > 0 and line_chars >= 14:
                meta.append(f"W {wind:.1f}")
            l2 = line_text(" ".join(meta), line_chars, "narrow_l2", marquee=True)
            return f"{l1}\n{l2}"

        if mode == "row":
            l1 = line_text(f"{temp_text}  {desc_short}", line_chars, "row_l1", marquee=True)
            meta_parts: list[str] = []
            if hum >= 0:
                meta_parts.append(f"H {hum}%")
            if wind > 0:
                meta_parts.append(f"W {wind:.1f}")
            if not meta_parts:
                meta_parts.append(f"Feels {feels_text}")
            l2 = line_text("  ".join(meta_parts), line_chars, "row_l2")
            return f"{l1}\n{l2}"

        if mode == "compact":
            l1 = line_text(label, line_chars, "compact_l1", marquee=True)
            l2 = line_text(f"{temp_text}  {desc_short}", line_chars, "compact_l2")
            meta_parts = [f"F {feels_text.replace(' ', '')}"]
            if hum >= 0:
                meta_parts.append(f"H {hum}%")
            if wind > 0:
                meta_parts.append(f"W {wind:.1f}")
            l3 = line_text("  ".join(meta_parts), line_chars, "compact_l3")
            return f"{l1}\n{l2}\n{l3}"

        # full
        l1 = line_text(f"{temp_text}  {desc_short}", line_chars, "full_l1")
        l2 = line_text(label, line_chars, "full_l2", marquee=True)
        details = [f"F {feels_text.replace(' ', '')}"]
        if hum >= 0:
            details.append(f"H {hum}%")
        if wind > 0:
            details.append(f"W {wind:.1f}")
        l3 = line_text("  ".join(details), line_chars, "full_l3")
        return f"{l1}\n{l2}\n{l3}"

    def _widget_preview_text_for_button(
        self,
        btn_data: dict[str, Any],
        btn: QPushButton | None = None,
        btn_name: str | None = None,
    ) -> str:
        action_key = self._normalize_action(str(btn_data.get("action", "") or ""))
        if action_key == "WeatherWidget":
            return self._weather_preview_text_for_button(btn_data, btn=btn, btn_name=btn_name)
        if action_key == "MetricWidget":
            return self._metric_preview_text_for_button(btn_data, btn=btn, btn_name=btn_name)
        return str(btn_data.get("name", "") or "").strip()

    def _refresh_metric_preview_in_app(self) -> None:
        if self._current_profile_mode() != "grid":
            return
        cur = self.profile_manager.current_profile
        if not cur:
            return
        prof = self.profile_manager.profiles.get(cur, {})
        if not isinstance(prof, dict):
            return
        for name, btn in list(self.grid_buttons.items()):
            btn_data = prof.get(name, {})
            if not isinstance(btn_data, dict):
                continue
            action_key = self._normalize_action(str(btn_data.get("action", "") or ""))
            if action_key != "MetricWidget":
                continue
            self._apply_grid_button_style(btn, btn_data)

    def _refresh_weather_preview_in_app(self) -> None:
        if self._current_profile_mode() != "grid":
            return
        cur = self.profile_manager.current_profile
        if not cur:
            return
        prof = self.profile_manager.profiles.get(cur, {})
        if not isinstance(prof, dict):
            return
        for name, btn in list(self.grid_buttons.items()):
            btn_data = prof.get(name, {})
            if not isinstance(btn_data, dict):
                continue
            action_key = self._normalize_action(str(btn_data.get("action", "") or ""))
            if action_key != "WeatherWidget":
                continue
            self._apply_grid_button_style(btn, btn_data)

    def _refresh_weather_preview_text_in_app(self) -> None:
        if self._current_profile_mode() != "grid":
            return
        cur = self.profile_manager.current_profile
        if not cur:
            return
        prof = self.profile_manager.profiles.get(cur, {})
        if not isinstance(prof, dict):
            return
        for name, btn in list(self.grid_buttons.items()):
            btn_data = prof.get(name, {})
            if not isinstance(btn_data, dict):
                continue
            action_key = self._normalize_action(str(btn_data.get("action", "") or ""))
            if action_key != "WeatherWidget":
                continue
            btn.setText(self._weather_preview_text_for_button(btn_data, btn=btn, btn_name=name))

    def _send_weather_line_to_esp(self, line: str, force: bool = False) -> bool:
        if not line or not self._serial_ready_for_weather():
            return False
        with self._weather_lock:
            if not force and line == self._weather_last_sent_line:
                return False
        try:
            self.serial_monitor.send_line(line)
        except Exception as e:
            print(f"[Weather] send failed: {e}")
            return False
        with self._weather_lock:
            self._weather_last_sent_line = line
        return True

    def _send_cached_weather_to_esp(self, force: bool = False) -> bool:
        with self._weather_lock:
            line = self._weather_cached_line
        if not line:
            return False
        return self._send_weather_line_to_esp(line, force=force)

    def _build_metric_widget_line(self, stats: dict[str, Any] | None = None) -> str:
        vals = self._metric_values_from_stats(stats)

        def as_val(key: str, digits: int = 1) -> str:
            val = vals.get(key)
            if val is None:
                return "-1"
            if digits <= 0:
                return f"{val:.0f}"
            return f"{val:.{digits}f}"

        line = (
            "WIDGET:TYPE=METRIC;"
            f"CPU={as_val('CPU', 1)};"
            f"RAM={as_val('RAM', 1)};"
            f"GPU={as_val('GPU', 1)};"
            f"GPUT={as_val('GPU_TEMP', 1)};"
            f"FPS={as_val('FPS', 1)};"
            f"NET={as_val('NET', 2)};"
            f"DISK={as_val('DISK', 2)};"
            f"CPUGHZ={as_val('CPU_GHZ', 2)}"
        )
        return line

    def _has_metric_widgets(self) -> bool:
        return bool(self._collect_metric_widget_specs())

    def _send_metric_widgets_to_esp(self, force: bool = False) -> bool:
        if not self._has_metric_widgets():
            return False
        if not self._serial_ready_for_weather():
            return False
        line = self._build_metric_widget_line(self._last_stats if isinstance(self._last_stats, dict) else None)
        if not line:
            return False
        if not force and line == self._metric_widget_last_sent_line:
            return False
        try:
            self.serial_monitor.send_line(line)
        except Exception as e:
            print(f"[Metric] send failed: {e}")
            return False
        self._metric_widget_last_sent_line = line
        return True

    def _has_weather_widgets(self) -> bool:
        return bool(self._collect_weather_widget_specs())

    def _refresh_weather_sync_state(self) -> None:
        timer = getattr(self, "_weather_sync_timer", None)
        if timer is None:
            return
        should_run = self._has_weather_widgets()
        if should_run:
            if not timer.isActive():
                timer.start()
        elif timer.isActive():
            timer.stop()
        self._refresh_weather_marquee_state()

    def _has_weather_widgets_in_current_profile(self) -> bool:
        cur = self.profile_manager.current_profile if hasattr(self, "profile_manager") else None
        if not cur:
            return False
        prof = self.profile_manager.profiles.get(cur, {})
        if not isinstance(prof, dict):
            return False
        if prof.get("mode", "grid") != "grid":
            return False
        rows = max(1, min(4, int(prof.get("rows", 3))))
        cols = max(1, min(4, int(prof.get("cols", 4))))
        anchors, _ = self._resolve_grid_layout(prof, rows, cols)
        for entry in anchors:
            btn_data = entry.get("data", {})
            if not isinstance(btn_data, dict):
                continue
            if self._normalize_action(str(btn_data.get("action", "") or "")) == "WeatherWidget":
                return True
        return False

    def _refresh_weather_marquee_state(self) -> None:
        timer = getattr(self, "_weather_marquee_timer", None)
        if timer is None:
            return
        should_run = self._current_profile_mode() == "grid" and self._has_weather_widgets_in_current_profile()
        if should_run:
            if not timer.isActive():
                timer.start()
        else:
            if timer.isActive():
                timer.stop()
            self._weather_marquee_state.clear()

    def _tick_weather_marquee(self) -> None:
        if self._current_profile_mode() != "grid":
            return
        if not self._has_weather_widgets_in_current_profile():
            return
        self._weather_marquee_step += 1
        self._refresh_weather_preview_text_in_app()

    def _schedule_weather_sync(self, delay_ms: int = 220, force: bool = False) -> None:
        if force:
            with self._weather_lock:
                self._weather_force_pending = True
        timer = getattr(self, "_weather_resync_timer", None)
        if timer is None:
            self._queue_weather_sync(force=force)
            return
        timer.start(max(80, int(delay_ms)))

    def _on_weather_sync_timer(self) -> None:
        self._queue_weather_sync(force=False)

    def _queue_weather_sync(self, force: bool = False) -> None:
        spec = self._select_weather_widget_spec()
        if spec is None:
            return
        with self._weather_lock:
            if force:
                self._weather_force_pending = True
            if self._weather_fetch_inflight:
                return
            should_force = bool(self._weather_force_pending)
            self._weather_force_pending = False
            self._weather_fetch_inflight = True

        threading.Thread(
            target=self._weather_sync_worker,
            args=(spec, should_force),
            daemon=True,
        ).start()

    def _weather_sync_worker(self, spec: dict[str, Any], force_send: bool) -> None:
        try:
            payload = self._fetch_weather_payload(spec)
            line = self._build_weather_widget_line(payload)
            with self._weather_lock:
                self._weather_cached_payload = dict(payload)
                self._weather_cached_line = line
            sent = self._send_weather_line_to_esp(line, force=force_send)
            self.weatherPreviewRefresh.emit()
            if sent:
                print(f"[Weather] updated {payload.get('label', 'Weather')} -> {payload.get('temp', 0.0):.1f} C")
        except Exception as e:
            print(f"[Weather] sync failed: {e}")
        finally:
            retry = False
            with self._weather_lock:
                self._weather_fetch_inflight = False
                if self._weather_force_pending:
                    retry = True
                    self._weather_force_pending = False
            if retry:
                self._queue_weather_sync(force=False)

    # ---------- SmartHome – HTTP príkazy na ESP2 ----------
    def _update_grid_selection_highlight(self, new_name: str | None):
        """
        Prepne vizuálny highlight na nové tlačidlo v gride.
        - starému zruší selected=True
        - novému nastaví selected=True
        """
        old_name = getattr(self, "selected_button_name", None)

        # staré tlačidlo – vypnúť selected
        if old_name and old_name in self.grid_buttons:
            old_btn = self.grid_buttons[old_name]
            old_btn.setProperty("selected", False)
            old_prof = self.profile_manager.profiles.get(self.profile_manager.current_profile, {})
            self._apply_grid_button_style(old_btn, old_prof.get(old_name, {}))

        # nové tlačidlo – zapnúť selected
        if new_name and new_name in self.grid_buttons:
            new_btn = self.grid_buttons[new_name]
            new_btn.setProperty("selected", True)
            new_prof = self.profile_manager.profiles.get(self.profile_manager.current_profile, {})
            self._apply_grid_button_style(new_btn, new_prof.get(new_name, {}))

        self.selected_button_name = new_name



    def _send_smarthome_toggle(self, relay_index: int) -> None:
        if relay_index not in (1, 2, 3, 4):
            return

        # SPRÁVNA URL podľa SmartHomeSketch.ino
        base = ""
        try:
            base = self.lineSmartBaseUrl.text().strip() if self.lineSmartBaseUrl else ""
        except Exception:
            base = ""
        base = base or getattr(self, "smart_home_base_url", "") or SMART_HOME_BASE_URL
        url = f"{base}/toggle?ch={relay_index}"

        threading.Thread(
            target=self._smarthome_toggle_worker,
            args=(url,),
            daemon=True,
        ).start()

    def _smarthome_toggle_worker(self, url: str) -> None:
        """HTTP volanie do samostatného threadu, aby UI nezamrzlo."""
        try:
            import requests
            r = requests.get(url, timeout=3)
            if r.status_code != 200:
                print(f"[SmartHome] ERROR {r.status_code} -> {url} | resp={r.text}")
            else:
                print(f"[SmartHome] OK -> {url} | resp={r.text}")
        except Exception as e:
            print(f"[SmartHome] ERROR pri požiadavke na {url}: {e}")


    def do_brightness_abs(self, pct: int | str):
        pct_int = max(0, min(100, int(pct)))
        if IS_WINDOWS and self.media is not None:
            self.media.set_brightness(pct_int)
        else:
            self.media_backend.set_brightness(pct_int)
        self.statusBar().showMessage(f"Jas: {pct_int}%")

    def do_brightness_rel(self, delta_pct: int | str):
        try:
            delta = int(delta_pct)
        except Exception:
            return
        if IS_WINDOWS and self.media is not None:
            self.media.change_brightness(delta)
        else:
            self.media_backend.change_brightness(delta)



    # ---------- Grid & profiles ----------

    def _schedule_grid_resize(self) -> None:
        if getattr(self, "_booting", False):
            return
        self._grid_resize_timer.start(120)

    def on_grid_size_changed(self):
        profs = self.profile_manager.profiles
        cur = self.profile_manager.current_profile
        if cur not in profs:
            return
        rows = max(1, min(4, self.spinRows.value()))
        cols = max(1, min(4, self.spinColumns.value()))
        if rows != self.spinRows.value():
            self.spinRows.blockSignals(True)
            self.spinRows.setValue(rows)
            self.spinRows.blockSignals(False)
        if cols != self.spinColumns.value():
            self.spinColumns.blockSignals(True)
            self.spinColumns.setValue(cols)
            self.spinColumns.blockSignals(False)
        profs[cur]["rows"] = rows
        profs[cur]["cols"] = cols
        self.render_grid(rows, cols)
        self._request_save()

    def render_grid(self, rows: int, cols: int):
        prev_selected = self.selected_button_name
        self._clear_grid_widgets()

        data_source = self.profile_manager.profiles[self.profile_manager.current_profile]
        cell_w, cell_h = self._compute_cell_size(rows, cols)
        gap = int(self._grid_settings().get("gap", 8))

        anchors, cell_to_anchor = self._resolve_grid_layout(data_source, rows, cols)
        self._grid_cell_to_anchor = cell_to_anchor

        for entry in anchors:
            name = str(entry.get("name") or "")
            r = int(entry.get("row", 0))
            c = int(entry.get("col", 0))
            span_rows = int(entry.get("span_rows", 1))
            span_cols = int(entry.get("span_cols", 1))
            btn_data = entry.get("data", {})
            if not isinstance(btn_data, dict):
                btn_data = {}
            action_key = self._normalize_action(str(btn_data.get("action", "") or ""))
            label = self._widget_preview_text_for_button(btn_data, btn_name=name)
            icon_path = str(btn_data.get("icon", "") or "").strip()

            btn = QPushButton(label)
            btn.setObjectName(name)
            target_w = cell_w * span_cols + gap * (span_cols - 1)
            target_h = cell_h * span_rows + gap * (span_rows - 1)
            btn.setFixedSize(target_w, target_h)
            btn.installEventFilter(self)
            try:
                btn.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay, True)
            except Exception:
                pass

            btn.setProperty("gridButton", True)
            btn.setProperty("selected", False)
            btn.setProperty("hovered", False)
            btn.setProperty("gridRowSpan", int(span_rows))
            btn.setProperty("gridColSpan", int(span_cols))
            btn.setToolTip("Vyber tlačidlo. Potiahni pravý dolný roh pre zmenu veľkosti.")

            if action_key in {"WeatherWidget", "MetricWidget"}:
                btn.setIcon(QIcon())
            elif icon_path and os.path.exists(icon_path):
                bg_color = self._icon_bg_color_for_btn(btn_data)
                self._set_button_icon(btn, icon_path, target_w, target_h, bg_color=bg_color)
                if not label:
                    btn.setText("")

            self._apply_grid_button_style(btn, btn_data)

            btn.clicked.connect(lambda checked, n=name: self.on_button_click(n))
            self.gridLayout.addWidget(btn, r, c, span_rows, span_cols)
            self.grid_buttons[name] = btn

        self._apply_cell_size_to_all()
        self._refresh_weather_preview_in_app()
        self._refresh_metric_preview_in_app()

        if prev_selected and prev_selected in self.grid_buttons:
            self.on_button_click(prev_selected)
        elif self.grid_buttons:
            first_name = next(iter(self.grid_buttons.keys()))
            self.on_button_click(first_name)

        self._apply_grid_background_style()


    def _apply_grid_shadow(self, btn: QPushButton) -> None:
        """Pridá nenápadný tieň na grid tlačidlo."""
        try:
            eff = QGraphicsDropShadowEffect(btn)
            eff.setBlurRadius(10)
            eff.setOffset(0, 2)
            eff.setColor(QColor(0, 0, 0, 70))
            btn.setGraphicsEffect(eff)
            btn._shadow = eff  # type: ignore[attr-defined]
            btn._hover_anim = None  # type: ignore[attr-defined]
            btn._ripple_anim = None  # type: ignore[attr-defined]
            btn._scale_anim = None  # type: ignore[attr-defined]
            btn._ripple = None      # type: ignore[attr-defined]
        except Exception:
            # UI funguje aj bez efektu, ak by Qt styly nepodporovali tieň
            pass

    def _apply_grid_button_mask(self, btn: QPushButton) -> None:
        """Ostrihne tlačidlo do zaobleného tvaru, aby ikony nemali štvorcové rohy."""
        try:
            w = btn.width()
            h = btn.height()
            if w <= 4 or h <= 4:
                return
            mask_sig = (w, h)
            if getattr(btn, "_mask_sig", None) == mask_sig:
                return
            radius = max(10, int(min(w, h) * 0.22))
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
            region = QRegion(path.toFillPolygon().toPolygon())
            btn.setMask(region)
            btn._mask_sig = mask_sig  # type: ignore[attr-defined]
        except Exception:
            pass

    def _animate_grid_hover(self, btn: QPushButton, entering: bool) -> None:
        """Pohne tieňom pri hover, aby tlačidlo pôsobilo zdvihnuté."""
        eff = getattr(btn, "_shadow", None)
        if eff is None:
            return
        try:
            anim = getattr(btn, "_hover_anim", None)
            if anim is None:
                anim = QPropertyAnimation(eff, b"offset", btn)
                anim.setDuration(140)
                anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                btn._hover_anim = anim  # type: ignore[attr-defined]
            anim.stop()
            start = eff.offset()
            target = QPointF(0, 2) if entering else QPointF(0, 4)
            anim.setStartValue(start)
            anim.setEndValue(target)
            anim.start()
        except Exception:
            pass



    def _grid_settings(self):
        cur = self.profile_manager.current_profile
        prof = self.profile_manager.profiles.setdefault(cur, {})
        gs = prof.setdefault("_grid", {})
        gs.setdefault("mode", "Square")
        gs.setdefault("gap", 8)
        gs.setdefault("padding", 12)
        try:
            if int(gs.get("padding", 12)) < 10:
                gs["padding"] = 12
        except Exception:
            gs["padding"] = 12
        gs.setdefault("cell_px", 120)
        gs.setdefault("cap_px", 120)
        return gs

    def _tap_slop_px(self) -> float:
        # Larger tolerance makes taps more forgiving on touch panels.
        return max(18.0, min(48.0, self.width() * 0.04))

    def _grid_container_widget(self) -> QWidget | None:
        return self._grid_display_widget()

    def _grid_display_widget(self) -> QWidget | None:
        cont = getattr(self, "displayFrame", None)
        if isinstance(cont, QWidget):
            return cont
        cont = self.findChild(QWidget, "displayFrame")
        if cont is not None:
            return cont
        if not hasattr(self, "gridLayout") or self.gridLayout is None:
            return None
        return self.gridLayout.parentWidget() or self.centralWidget() or self

    def _grid_outer_widget(self) -> QWidget | None:
        cont = getattr(self, "gridCanvas", None)
        if isinstance(cont, QWidget):
            return cont
        cont = self.findChild(QWidget, "gridCanvas")
        if cont is not None:
            return cont
        if not hasattr(self, "gridLayout") or self.gridLayout is None:
            return None
        return self.gridLayout.parentWidget() or self.centralWidget() or self

    def _is_pos_in_grid(self, pos: QPointF) -> bool:
        cont = self._grid_container_widget()
        if cont is None:
            return False
        try:
            local = cont.mapFrom(self, QPoint(int(pos.x()), int(pos.y())))
            return cont.rect().contains(local)
        except Exception:
            return False

    def _grid_button_at(self, pos: QPointF) -> QPushButton | None:
        obj = self.childAt(int(pos.x()), int(pos.y()))
        while isinstance(obj, QWidget):
            if isinstance(obj, QPushButton) and obj.property("gridButton"):
                return obj
            obj = obj.parentWidget()
        return None

    def eventFilter(self, obj, ev):
        if hasattr(self, "listProfiles") and obj == self.listProfiles.viewport():
            et = ev.type()
            def _ev_pos(event):
                if et in (
                    QEvent.Type.TouchBegin,
                    QEvent.Type.TouchUpdate,
                    QEvent.Type.TouchEnd,
                    QEvent.Type.TouchCancel,
                ):
                    try:
                        points = event.points()
                    except Exception:
                        return None
                    if not points:
                        return None
                    return points[0].position()
                try:
                    return event.position()
                except Exception:
                    return None
            if et in (QEvent.Type.TouchBegin, QEvent.Type.MouseButtonPress):
                pos = _ev_pos(ev)
                if pos is not None:
                    self._profiles_drag_start = pos
                    self._profiles_dragging = False
                return False
            if et in (QEvent.Type.TouchUpdate, QEvent.Type.MouseMove):
                if self._profiles_drag_start is None:
                    return False
                if et == QEvent.Type.MouseMove and not (ev.buttons() & Qt.MouseButton.LeftButton):
                    return False
                pos = _ev_pos(ev)
                if pos is None:
                    return False
                dx = pos.x() - self._profiles_drag_start.x()
                dy = pos.y() - self._profiles_drag_start.y()
                if abs(dx) > 6 or abs(dy) > 6:
                    self._profiles_dragging = True
                return False
            if et in (QEvent.Type.TouchEnd, QEvent.Type.MouseButtonRelease):
                if self._profiles_dragging:
                    self._profiles_drag_start = None
                    self._profiles_dragging = False
                    return True
                self._profiles_drag_start = None
                self._profiles_dragging = False
                return False
            if et == QEvent.Type.TouchCancel:
                self._profiles_drag_start = None
                self._profiles_dragging = False
                return False
        if isinstance(obj, QWidget) and hasattr(obj, "property") and obj.property("mixerSwipeBar"):
            if self._current_profile_mode() != "mixer":
                return super().eventFilter(obj, ev)
            if not self._swipe_enabled:
                return super().eventFilter(obj, ev)
            et = ev.type()
            if et == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                self._mixer_zone_start_pos = ev.position()
                self._mixer_zone_consumed = False
                return True
            if et == QEvent.Type.MouseMove and self._mixer_zone_start_pos is not None:
                dx = ev.position().x() - self._mixer_zone_start_pos.x()
                dy = ev.position().y() - self._mixer_zone_start_pos.y()
                if abs(dx) >= self._swipe_threshold_px() and abs(dx) > abs(dy) * 1.2:
                    self._mixer_zone_consumed = True
                    self._mixer_zone_start_pos = None
                    self._try_swipe(dx, dy)
                    return True
                return True
            if et in (QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick):
                self._mixer_zone_start_pos = None
                return True
            if et in (
                QEvent.Type.TouchBegin,
                QEvent.Type.TouchUpdate,
                QEvent.Type.TouchEnd,
                QEvent.Type.TouchCancel,
            ):
                try:
                    points = ev.points()
                except Exception:
                    return True
                if not points:
                    return True
                pos = points[0].position()
                if et == QEvent.Type.TouchBegin:
                    self._mixer_zone_start_pos = pos
                    self._mixer_zone_consumed = False
                    return True
                if et == QEvent.Type.TouchUpdate and self._mixer_zone_start_pos is not None:
                    dx = pos.x() - self._mixer_zone_start_pos.x()
                    dy = pos.y() - self._mixer_zone_start_pos.y()
                    if abs(dx) >= self._swipe_threshold_px() and abs(dx) > abs(dy) * 1.2:
                        self._mixer_zone_consumed = True
                        self._mixer_zone_start_pos = None
                        self._try_swipe(dx, dy)
                        return True
                    return True
                if et in (QEvent.Type.TouchEnd, QEvent.Type.TouchCancel):
                    self._mixer_zone_start_pos = None
                    return True
            return True
        if isinstance(obj, MixerSlider) or (
            isinstance(obj, QWidget)
            and hasattr(obj, "property")
            and obj.property("mixerSlider")
        ):
            if self._current_profile_mode() != "mixer":
                return super().eventFilter(obj, ev)
            if not self._swipe_enabled:
                return super().eventFilter(obj, ev)
            et = ev.type()
            if et in (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd, QEvent.Type.TouchCancel):
                try:
                    points = ev.points()
                except Exception:
                    return False
                if not points:
                    return False
                pos = points[0].position()
                gpos = obj.mapToGlobal(QPoint(int(pos.x()), int(pos.y())))
                gposf = QPointF(gpos.x(), gpos.y())
                if et == QEvent.Type.TouchBegin:
                    self._mixer_swipe_start_pos = gposf
                    self._mixer_swipe_slider = obj
                    self._mixer_swipe_start_value = obj.value() if isinstance(obj, MixerSlider) else None
                    self._mixer_swipe_armed = False
                    self._mixer_swipe_consumed = False
                    return False
                if et == QEvent.Type.TouchUpdate and self._mixer_swipe_start_pos is not None and obj == self._mixer_swipe_slider:
                    dx = gposf.x() - self._mixer_swipe_start_pos.x()
                    dy = gposf.y() - self._mixer_swipe_start_pos.y()
                    if not self._mixer_swipe_armed:
                        if abs(dx) > self._tap_slop_px() or abs(dy) > self._tap_slop_px():
                            if abs(dx) >= abs(dy):
                                self._mixer_swipe_armed = True
                                if isinstance(obj, MixerSlider) and self._mixer_swipe_start_value is not None:
                                    obj.blockSignals(True)
                                    obj.setValue(int(self._mixer_swipe_start_value))
                                    obj.blockSignals(False)
                            else:
                                # vertical intent -> let slider handle
                                self._mixer_swipe_start_pos = None
                                self._mixer_swipe_slider = None
                                self._mixer_swipe_start_value = None
                                return False
                    if self._mixer_swipe_armed:
                        if abs(dx) >= self._swipe_threshold_px():
                            self._mixer_swipe_consumed = True
                            self._mixer_swipe_start_pos = None
                            self._mixer_swipe_slider = None
                            self._mixer_swipe_start_value = None
                            self._mixer_swipe_armed = False
                            self._try_swipe(dx, dy)
                        return True
                    return False
                if et in (QEvent.Type.TouchEnd, QEvent.Type.TouchCancel):
                    self._mixer_swipe_start_pos = None
                    self._mixer_swipe_slider = None
                    self._mixer_swipe_start_value = None
                    if self._mixer_swipe_armed or self._mixer_swipe_consumed:
                        self._mixer_swipe_armed = False
                        self._mixer_swipe_consumed = False
                        return True
                    return False
            if et == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                self._mixer_swipe_start_pos = ev.globalPosition()
                self._mixer_swipe_slider = obj
                self._mixer_swipe_start_value = obj.value() if isinstance(obj, MixerSlider) else None
                self._mixer_swipe_armed = False
                self._mixer_swipe_consumed = False
                return False
            if et == QEvent.Type.MouseMove and self._mixer_swipe_start_pos is not None and obj == self._mixer_swipe_slider:
                dx = ev.globalPosition().x() - self._mixer_swipe_start_pos.x()
                dy = ev.globalPosition().y() - self._mixer_swipe_start_pos.y()
                if not self._mixer_swipe_armed:
                    if abs(dx) > self._tap_slop_px() or abs(dy) > self._tap_slop_px():
                        if abs(dx) >= abs(dy):
                            self._mixer_swipe_armed = True
                            if isinstance(obj, MixerSlider) and self._mixer_swipe_start_value is not None:
                                obj.blockSignals(True)
                                obj.setValue(int(self._mixer_swipe_start_value))
                                obj.blockSignals(False)
                        else:
                            # vertical intent -> let slider handle
                            self._mixer_swipe_start_pos = None
                            self._mixer_swipe_slider = None
                            self._mixer_swipe_start_value = None
                            return False
                if self._mixer_swipe_armed:
                    if abs(dx) >= self._swipe_threshold_px():
                        self._mixer_swipe_consumed = True
                        self._mixer_swipe_start_pos = None
                        self._mixer_swipe_slider = None
                        self._mixer_swipe_start_value = None
                        self._mixer_swipe_armed = False
                        self._try_swipe(dx, dy)
                    return True
                return False
            if et == QEvent.Type.MouseButtonRelease and obj == self._mixer_swipe_slider:
                self._mixer_swipe_start_pos = None
                self._mixer_swipe_slider = None
                self._mixer_swipe_start_value = None
                if self._mixer_swipe_armed or self._mixer_swipe_consumed:
                    self._mixer_swipe_armed = False
                    self._mixer_swipe_consumed = False
                    return True
                return False
        if hasattr(self, "grid_buttons") and obj in self.grid_buttons.values():
            et = ev.type()
            if et == QEvent.Type.MouseMove:
                if self._grid_resize_drag_btn is obj and self._grid_resize_drag_start_global is not None:
                    self._update_grid_resize_drag(ev.globalPosition())
                    return True
                if obj.objectName() == self.selected_button_name and self._is_grid_resize_handle_hit(obj, ev.position()):
                    obj.setCursor(Qt.CursorShape.SizeFDiagCursor)
                else:
                    obj.unsetCursor()
            elif et == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                if self._is_grid_resize_handle_hit(obj, ev.position()):
                    if obj.objectName() != self.selected_button_name:
                        self.on_button_click(obj.objectName())
                    self._start_grid_resize_drag(obj, ev.globalPosition())
                    return True
            elif et == QEvent.Type.MouseButtonRelease:
                if self._grid_resize_drag_btn is obj:
                    self._finish_grid_resize_drag(ev.globalPosition())
                    return True
            elif et == QEvent.Type.Leave and self._grid_resize_drag_btn is None:
                obj.unsetCursor()

            if ev.type() in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseButtonDblClick,
            ):
                if time.monotonic() - self._last_touch_click_ts < 0.45:
                    return True
            if self._touch_block_clicks and ev.type() in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseMove,
                QEvent.Type.MouseButtonDblClick,
            ):
                return True
            if ev.type() == QEvent.Type.Enter:
                if not bool(obj.property("hovered")):
                    obj.setProperty("hovered", True)
                    self._animate_grid_hover(obj, entering=True)
                    try:
                        prof = self.profile_manager.profiles.get(self.profile_manager.current_profile, {})
                        self._apply_grid_button_style(obj, prof.get(obj.objectName(), {}))
                    except Exception:
                        pass
            elif ev.type() == QEvent.Type.Leave:
                if bool(obj.property("hovered")):
                    obj.setProperty("hovered", False)
                    self._animate_grid_hover(obj, entering=False)
                    try:
                        prof = self.profile_manager.profiles.get(self.profile_manager.current_profile, {})
                        self._apply_grid_button_style(obj, prof.get(obj.objectName(), {}))
                    except Exception:
                        pass
        return super().eventFilter(obj, ev)

    def event(self, ev):
        if ev.type() == QEvent.Type.Gesture and self._swipe_enabled:
            if self._handle_gesture_event(ev):
                return True
        if ev.type() in (
            QEvent.Type.TouchBegin,
            QEvent.Type.TouchUpdate,
            QEvent.Type.TouchEnd,
            QEvent.Type.TouchCancel,
        ):
            if self._handle_touch_event(ev):
                return True
        return super().event(ev)

    def _handle_touch_event(self, ev) -> bool:
        # Swipe/tap handling only for grid mode to avoid breaking mixer/media widgets.
        if self._current_profile_mode() != "grid":
            return False
        try:
            points = ev.points()
        except Exception:
            return False
        if not points:
            return False
        pos = points[0].position()
        if ev.type() == QEvent.Type.TouchBegin:
            if not self._is_pos_in_grid(pos):
                return False
            self._touch_start_pos = pos
            self._touch_press_target = self._grid_button_at(pos)
            self._touch_block_clicks = True
            return True
        if ev.type() == QEvent.Type.TouchUpdate:
            if self._touch_start_pos is None:
                return True
            return True
        if ev.type() == QEvent.Type.TouchCancel:
            self._touch_start_pos = None
            self._touch_press_target = None
            self._touch_block_clicks = False
            return True
        if ev.type() == QEvent.Type.TouchEnd:
            start = self._touch_start_pos
            self._touch_start_pos = None
            target = self._touch_press_target
            self._touch_press_target = None
            self._touch_block_clicks = False
            if target is None:
                return False
            # Maximalna citlivost: ak sa dotyk zacal na tlacidle, vykonaj klik.
            now = time.monotonic()
            btn_name = target.objectName()
            if (
                btn_name
                and btn_name == self._last_touch_click_btn
                and now - self._last_touch_click_ts < self._touch_click_min_interval
            ):
                return True
            self._last_touch_click_btn = btn_name
            self._last_touch_click_ts = now
            target.click()
            return True
        return False

    def _handle_gesture_event(self, ev) -> bool:
        if not self._swipe_enabled:
            return False
        swipe = ev.gesture(Qt.GestureType.SwipeGesture)
        if swipe is None:
            return False
        return self._handle_swipe_gesture(swipe)

    def _handle_swipe_gesture(self, swipe) -> bool:
        if swipe.state() != Qt.GestureState.GestureFinished:
            return True
        if not hasattr(self, "listProfiles"):
            return True
        if self.listProfiles.count() < 2:
            return True
        direction = swipe.horizontalDirection()
        if direction == QSwipeGesture.SwipeDirection.Left:
            self.execute_special_action("NextProfile")
            return True
        if direction == QSwipeGesture.SwipeDirection.Right:
            self.execute_special_action("PreviousProfile")
            return True
        return False

    def mousePressEvent(self, ev):
        # ripple efekt len pre grid tlačidlá
        obj = self.childAt(ev.pos())
        if isinstance(obj, QPushButton) and hasattr(obj, "property") and obj.property("gridButton"):
            self._start_ripple(obj, ev.globalPosition().toPoint())
        if self._swipe_enabled and ev.button() == Qt.MouseButton.LeftButton:
            self._swipe_start_pos = ev.position()
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._grid_resize_drag_btn is not None:
            self._finish_grid_resize_drag(ev.globalPosition())
            return
        if self._swipe_enabled and ev.button() == Qt.MouseButton.LeftButton and self._swipe_start_pos is not None:
            start = self._swipe_start_pos
            self._swipe_start_pos = None
            dx = ev.position().x() - start.x()
            dy = ev.position().y() - start.y()
            if self._try_swipe(dx, dy):
                return
        super().mouseReleaseEvent(ev)

    def _swipe_threshold_px(self) -> float:
        return max(80.0, min(160.0, self.width() * 0.15))

    def _try_swipe(self, dx: float, dy: float) -> bool:
        if not self._swipe_enabled:
            return False
        if not hasattr(self, "listProfiles"):
            return False
        if self.listProfiles.count() < 2:
            return False
        if abs(dx) < self._swipe_threshold_px():
            return False
        if abs(dx) <= abs(dy) * 1.2:
            return False
        if dx < 0:
            self.execute_special_action("NextProfile")
        else:
            self.execute_special_action("PreviousProfile")
        return True

    def _start_ripple(self, btn: QPushButton, pos_global):
        """Spustí krátky ripple efekt na grid tlačidle."""
        try:
            local = btn.mapFromGlobal(pos_global)
            if not hasattr(btn, "_ripple") or btn._ripple is None:
                ripple = QWidget(btn)
                ripple.setObjectName("Ripple")
                ripple.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                ripple.raise_()
                btn._ripple = ripple  # type: ignore[attr-defined]
            ripple = btn._ripple  # type: ignore[attr-defined]
            size = min(btn.width(), btn.height()) // 2
            ripple.resize(size, size)
            ripple.move(local.x() - size // 2, local.y() - size // 2)
            ripple.setStyleSheet("""
            QWidget#Ripple {
                background: rgba(255,255,255,0.12);
                border-radius: %dpx;
            }
            """ % (size // 2))

            anim = QPropertyAnimation(ripple, b"geometry", btn)
            anim.setDuration(240)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(QRect(ripple.x(), ripple.y(), size, size))
            anim.setEndValue(QRect(ripple.x() - size//2, ripple.y() - size//2, size*2, size*2))
            fade = QPropertyAnimation(ripple, b"windowOpacity", btn)
            fade.setDuration(240)
            fade.setStartValue(0.35)
            fade.setEndValue(0.0)

            grp = QParallelAnimationGroup(btn)
            grp.addAnimation(anim)
            grp.addAnimation(fade)
            ripple.show()
            grp.finished.connect(ripple.hide)
            grp.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        except Exception:
            pass

    def _grid_container_size(self) -> tuple[int, int]:
        cont = self._grid_outer_widget()
        if cont is None:
            cont = self.centralWidget() or self
        r = cont.contentsRect()
        return r.width(), r.height()

    def showEvent(self, ev):
        super().showEvent(ev)
        QTimer.singleShot(0, self._apply_cell_size_to_all)

    def _compute_cell_size(self, rows: int, cols: int) -> tuple[int, int]:
        gs = self._grid_settings()
        mode = gs.get("mode", "Square")
        gap = int(gs.get("gap", 8))
        pad = int(gs.get("padding", 8))
        cap = int(gs.get("cap_px", 120))
        cell_px = int(gs.get("cell_px", 120))

        if mode == "Fixed":
            return cell_px, cell_px

        avail_w, avail_h = self._grid_container_size()
        inner_w = max(0, avail_w - 2 * pad - (cols - 1) * gap)
        inner_h = max(0, avail_h - 2 * pad - (rows - 1) * gap)
        if cols <= 0 or rows <= 0 or inner_w <= 0 or inner_h <= 0:
            return 80, 80

        cell_w = inner_w // cols
        cell_h = inner_h // rows

        if mode == "Square":
            s = min(cell_w, cell_h)
            s = min(s, max(32, cap))
            return max(32, s), max(32, s)

        return max(32, cell_w), max(32, cell_h)
    
    def _apply_cell_size_to_all(self):
        """
        Pre grid profily nastaví veľkosť buniek a tlačidiel.
        Pre monitor/mixer/media profily len rozumne nastaví okraje,
        aby SystemMonitorWidget (alebo iný widget) nebol zdeformovaný.
        """
        if not hasattr(self, "gridLayout") or self.gridLayout is None:
            return

        cur = self.profile_manager.current_profile
        mode = self._current_profile_mode(cur)

        # --- nie sme v grid profile -> len reset margins/spacing a koniec ---
        if mode != "grid":
            # mierne okraje okolo monitor widgetu, žiadne centrovanie podľa buniek
            self.gridLayout.setContentsMargins(0, 0, 0, 0)
            self.gridLayout.setHorizontalSpacing(0)
            self.gridLayout.setVerticalSpacing(0)
            overlay = getattr(self, "gridFrameOverlay", None)
            if isinstance(overlay, QLabel):
                overlay.hide()
            self._hide_grid_resize_preview()
            self._grid_bg_rect = None
            display = self._grid_display_widget()
            if display:
                try:
                    display.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                except Exception:
                    pass
                avail_w, avail_h = self._grid_container_size()
                target_w = max(1, avail_w)
                target_h = max(1, avail_h)
                self._last_non_grid_size = (target_w, target_h)
                layout = getattr(self, "gridCanvasLayout", None)
                if layout:
                    try:
                        layout.setAlignment(display, Qt.AlignmentFlag.AlignCenter)
                        layout.setStretch(0, 0)
                    except Exception:
                        pass
                display.setFixedSize(target_w, target_h)
                # natiahni non-grid widget (monitor/media/mixer) na celú plochu
                try:
                    item = self.gridLayout.itemAtPosition(0, 0) or self.gridLayout.itemAt(0)
                except Exception:
                    item = self.gridLayout.itemAt(0)
                if item and isinstance(item.widget(), QWidget):
                    w = item.widget()
                    try:
                        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
                    except Exception:
                        pass
                    w.setMinimumSize(target_w, target_h)
                    w.setMaximumSize(target_w, target_h)
                    w.setFixedSize(target_w, target_h)
            self.gridLayout.update()
            self.update()
            self._apply_grid_background_style()
            return

        # --- GRID mód: pôvodná logika pre tlačidlá ---
        self._last_non_grid_size = None
        rows = self.profile_manager.profiles[cur]["rows"]
        cols = self.profile_manager.profiles[cur]["cols"]
        cell_w, cell_h = self._compute_cell_size(rows, cols)

        gs = self._grid_settings()
        gap = int(gs.get("gap", 8))
        pad = int(gs.get("padding", 8))

        total_w = cols * cell_w + (cols - 1) * gap
        total_h = rows * cell_h + (rows - 1) * gap

        display = self._grid_display_widget()
        disp_w = total_w + 2 * pad
        disp_h = total_h + 2 * pad
        if display:
            try:
                display.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            except Exception:
                pass
            layout = getattr(self, "gridCanvasLayout", None)
            if layout:
                try:
                    layout.setAlignment(display, Qt.AlignmentFlag.AlignCenter)
                except Exception:
                    pass
            cont_w, cont_h = self._grid_container_size()
            if cont_w > 0 and cont_h > 0:
                disp_w = cont_w
                disp_h = cont_h
            display.setFixedSize(disp_w, disp_h)

        extra_w = max(0, disp_w - total_w)
        extra_h = max(0, disp_h - total_h)
        margin_x = max(pad, extra_w // 2)
        margin_y = max(pad, extra_h // 2)
        extra = max(6, min(20, int(min(cell_w, cell_h) * 0.12)))
        bg_x = max(0, margin_x - extra)
        bg_y = max(0, margin_y - extra)
        bg_w = min(disp_w - bg_x, total_w + 2 * extra)
        bg_h = min(disp_h - bg_y, total_h + 2 * extra)
        self._grid_bg_rect = QRect(bg_x, bg_y, bg_w, bg_h)

        self.gridLayout.setHorizontalSpacing(gap)
        self.gridLayout.setVerticalSpacing(gap)
        self.gridLayout.setContentsMargins(margin_x, margin_y, margin_x, margin_y)

        prof = self.profile_manager.profiles.get(cur, {})

        for name, btn in self.grid_buttons.items():
            if not btn:
                continue
            span_rows = max(1, int(btn.property("gridRowSpan") or 1))
            span_cols = max(1, int(btn.property("gridColSpan") or 1))
            target_w = cell_w * span_cols + gap * (span_cols - 1)
            target_h = cell_h * span_rows + gap * (span_rows - 1)
            if btn.width() != target_w or btn.height() != target_h:
                btn.setMinimumSize(target_w, target_h)
                btn.setMaximumSize(target_w, target_h)
                btn.setFixedSize(target_w, target_h)
                self._apply_grid_button_mask(btn)

            btn_data = prof.get(name, {})
            action_key = self._normalize_action(str(btn_data.get("action", "") or ""))
            icon_path = (btn_data.get("icon") or "").strip()
            if action_key in {"WeatherWidget", "MetricWidget"}:
                # Recompute responsive widget layout after any resize.
                self._apply_grid_button_style(btn, btn_data)
            elif icon_path and os.path.exists(icon_path):
                bg_color = self._icon_bg_color_for_btn(btn_data)
                self._set_button_icon(btn, icon_path, target_w, target_h, bg_color=bg_color)
            else:
                btn.setIcon(QIcon())

        self.gridLayout.update()
        self.update()
        overlay = getattr(self, "gridFrameOverlay", None)
        if isinstance(overlay, QLabel) and display:
            overlay.setGeometry(0, 0, disp_w, disp_h)
            if disp_w > 4 and disp_h > 4:
                rect = QRectF(self._grid_bg_rect) if isinstance(self._grid_bg_rect, QRect) else QRectF(margin_x, margin_y, total_w, total_h)
                frame_key = (
                    int(disp_w),
                    int(disp_h),
                    int(round(rect.x())),
                    int(round(rect.y())),
                    int(round(rect.width())),
                    int(round(rect.height())),
                    20,
                )
                frame = self._grid_frame_cache.get(frame_key)
                if frame is None:
                    frame = QPixmap(disp_w, disp_h)
                    frame.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(frame)
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    border_col = QColor(255, 255, 255, 46)
                    painter.setPen(QPen(border_col, 2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    radius = 20
                    painter.drawRoundedRect(rect, radius, radius)
                    painter.end()
                    self._cache_put(self._grid_frame_cache, frame_key, frame, self._grid_frame_cache_limit)
                overlay.setPixmap(frame)
                overlay.show()
                if isinstance(self.displayBgLabel, QLabel):
                    self.displayBgLabel.lower()
                if self.grid_buttons:
                    first_btn = next(iter(self.grid_buttons.values()))
                    overlay.stackUnder(first_btn)
            else:
                overlay.hide()

        if self._grid_resize_drag_anchor:
            cand_rows, cand_cols = self._grid_resize_drag_candidate
            self._show_grid_resize_preview(self._grid_resize_drag_anchor, cand_rows, cand_cols)
        else:
            self._hide_grid_resize_preview()
        self._apply_grid_background_style()

    def _apply_resize_layout(self) -> None:
        if hasattr(self, "gridLayout") and self.gridLayout:
            self._apply_cell_size_to_all()
            self._apply_grid_background_style()


    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        timer = getattr(self, "_resize_apply_timer", None)
        if isinstance(timer, QTimer):
            if not timer.isActive():
                timer.start()
        elif hasattr(self, "gridLayout") and self.gridLayout:
            self._apply_cell_size_to_all()
            self._apply_grid_background_style()

    # ---------- Button actions ----------

    def execute_button_action(self, btn_data: dict[str, Any]):
        action = btn_data.get("action")
        path = btn_data.get("path")
        action_key = self._normalize_action(action)

        if action_key == "SwitchProfile":
            target = (path or "").strip()
            if not target:
                self.execute_special_action("NextProfile")
                return
            if target in self.profile_manager.profiles:
                self.profile_manager.profile_history.append(self.profile_manager.current_profile)
                self._set_selected_profile(target)
            else:
                print(f"SwitchProfile: profil '{target}' neexistuje")
            return

        elif action_key == "GoBack":

            self.handle_button_b()

        elif action_key == "OpenSubMenu":
            submenu_data = btn_data.get("submenu", {})
            if submenu_data:
                cur = self.profile_manager.current_profile
                self.profile_manager.profile_history.append((cur, self.profile_manager.profiles[cur]))
                self.profile_manager.profiles[cur] = submenu_data
                self.render_grid(submenu_data.get("rows", 3), submenu_data.get("cols", 3))

        elif action_key == "OpenApp" and path:
            try:
                # Prefer backend (supports command lines like `flatpak run ...`),
                # then keep desktop-opener fallback for plain files/URLs.
                self.media_backend.open_app(path)
            except Exception:
                try:
                    if not open_path_smart(path):
                        QMessageBox.warning(self, "Chyba", f"Nepodarilo sa otvoriť aplikáciu:\n{path}")
                except Exception as e:
                    QMessageBox.warning(self, "Chyba", f"Nepodarilo sa spustiť aplikáciu:\n{e}")

        elif action_key in {
            "PlayMusic", "Mute", "Next", "Previous",
            "MuteMic", "UnmuteMic",
            "SpotifyPlaylist",
            "OpenURL",
            "BrightnessUp", "BrightnessDown",
            "SendKeys",
            "MinimizeWindow", "MaximizeWindow",
            "CloseWindow", "SwitchWindow",
            "LockPC", "SleepPC", "ShutdownPC", "RestartPC",
            "PasteClipboard",
        }:
            self.handle_system_action(action_key, path or None)

        elif action_key == "HTTPRequest" and path:
            self._queue_http_request(path)

        elif action_key == "DiscordWebhook":
            self._trigger_discord_webhook(path or "", fallback_content=(btn_data.get("name") or "").strip())

        elif action_key == "WeatherWidget":
            self._queue_weather_sync(force=True)

        elif action_key == "MetricWidget":
            self._refresh_metric_preview_in_app()
            self._send_metric_widgets_to_esp(force=True)

        elif action_key == "CopyToClipboard":
            self._copy_to_clipboard(path or "")
        
        elif action_key == "SmartRelay1Toggle":
            self._send_smarthome_toggle(1)

        elif action_key == "SmartRelay2Toggle":
            self._send_smarthome_toggle(2)

        elif action_key == "SmartRelay3Toggle":
            self._send_smarthome_toggle(3)

        elif action_key == "SmartRelay4Toggle":
            self._send_smarthome_toggle(4)

    def on_button_click(self, btn_name: str):
        self._flush_button_autosave()
        btn_name = self._grid_cell_to_anchor.get(btn_name, btn_name)
        # najprv prepni highlight
        self._update_grid_selection_highlight(btn_name)

        cur = self.profile_manager.current_profile
        if cur not in self.profile_manager.profiles:
            return

        profile = self.profile_manager.profiles[cur]
        btn_data = profile.get(btn_name, {})
        self._button_ui_loading = True
        try:
            self.inputName.setText(btn_data.get("name", ""))
            self.inputPath.setText(btn_data.get("path", ""))
            self.lineIconPath.setText(btn_data.get("icon", ""))

            action = (btn_data.get("action") or "")
            fallback = self.comboActionType.itemText(0) if self.comboActionType.count() else ""
            self._set_combo_safe(self.comboActionType, action, fallback=fallback)

            self._update_action_fields(self.comboActionType.currentText())
            self._sync_widget_size_control(btn_name, btn_data if isinstance(btn_data, dict) else {})
            self._load_button_style_for_selected(btn_data)
        finally:
            self._button_ui_loading = False

    def _normalize_action(self, action: str) -> str:
        action = (action or "").strip()
        return ACTION_ALIASES.get(action, action)

    def _normalize_spotify_playlist_uri(self, raw: str) -> str | None:
        text = (raw or "").strip()
        if not text:
            return None

        low = text.lower()
        if low.startswith("spotify:playlist:"):
            return text.split("?", 1)[0]

        if "spotify:playlist:" in low:
            head = text.split("?", 1)[0]
            idx = head.lower().find("spotify:playlist:")
            if idx >= 0:
                return head[idx:]

        if "open.spotify.com/playlist/" in low:
            url = text
            if not low.startswith(("http://", "https://")):
                url = "https://" + text
            try:
                parsed = urlparse(url)
            except Exception:
                parsed = None
            if parsed:
                parts = parsed.path.strip("/").split("/")
                if len(parts) >= 2 and parts[0] == "playlist":
                    return f"spotify:playlist:{parts[1]}"

        if re.match(r"^[A-Za-z0-9]{10,}$", text):
            return f"spotify:playlist:{text}"

        return None

    def _find_spotify_player_name(self) -> str | None:
        if not shutil.which("playerctl"):
            return None
        try:
            res = subprocess.run(
                ["playerctl", "-l"],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as e:
            print(f"[Spotify] playerctl -l failed: {e}")
            return None

        for line in (res.stdout or "").splitlines():
            if "spotify" in line.lower():
                return line.strip()
        return None

    def _play_spotify_playlist(self, raw: str | None) -> None:
        uri = self._normalize_spotify_playlist_uri(raw or "")
        if not uri:
            self.statusBar().showMessage(
                "Spotify Playlist: zadaj playlist ID, URL alebo spotify:playlist:ID",
                3000,
            )
            return

        if IS_LINUX:
            opened = self._run_linux_command_candidates(
                "SpotifyOpen",
                [
                    ["xdg-open", uri],
                    ["gio", "open", uri],
                ],
            )
            if not opened:
                self.statusBar().showMessage(
                    "Spotify Playlist: xdg-open/gio nie je dostupné",
                    3000,
                )
                return

            def _do_play() -> None:
                if not shutil.which("playerctl"):
                    self.statusBar().showMessage(
                        "Spotify Playlist: playerctl nenájdený",
                        3000,
                    )
                    return
                player = self._find_spotify_player_name()
                cmd = ["playerctl"]
                if player:
                    cmd += ["-p", player]
                cmd += ["play"]
                try:
                    subprocess.Popen(cmd)
                except Exception as e:
                    print(f"[Spotify] play failed: {e}")

            QTimer.singleShot(2000, _do_play)
            return

        try:
            self.media_backend.open_app(uri)
        except Exception as e:
            print(f"[Spotify] open_app failed: {e}")

        def _do_play_fallback() -> None:
            try:
                self.media_backend.play_pause()
            except Exception as e:
                print(f"[Spotify] play_pause failed: {e}")

        QTimer.singleShot(1500, _do_play_fallback)

    def _extract_command_head(self, raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return ""
        try:
            parts = shlex.split(text, posix=not IS_WINDOWS)
        except ValueError:
            return text
        return parts[0] if parts else text

    def _looks_like_path(self, text: str) -> bool:
        if not text:
            return False
        if IS_WINDOWS and len(text) >= 2 and text[1] == ":":
            return True
        return ("/" in text) or ("\\" in text)

    def _validate_action_path(self, action: str, path: str, silent: bool = False) -> bool:
        action_key = self._normalize_action(action)
        if silent:
            return True

        if action_key == "OpenApp":
            if not path:
                QMessageBox.warning(self, "Chyba", "Pre OpenApp zadaj cestu alebo prikaz.")
                return False
            cmd = self._extract_command_head(path).strip().strip('"').strip("'")
            if self._looks_like_path(cmd):
                expanded = os.path.expanduser(os.path.expandvars(cmd))
                if not os.path.exists(expanded):
                    msg = f"Cesta pre OpenApp neexistuje:\n{cmd}\n\nUlozit aj tak?"
                    choice = QMessageBox.question(
                        self,
                        "Neplatna cesta",
                        msg,
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    return choice == QMessageBox.StandardButton.Yes
            return True

        if action_key == "HTTPRequest":
            if not path:
                QMessageBox.warning(self, "Chyba", "Pre HTTP Request zadaj URL alebo request špecifikáciu.")
                return False
            try:
                self._parse_http_request_spec(path)
            except ValueError as e:
                QMessageBox.warning(
                    self,
                    "Neplatný HTTP Request",
                    str(e),
                )
                return False
            return True

        if action_key == "DiscordWebhook":
            if not path:
                QMessageBox.warning(self, "Chyba", "Pre Discord Webhook zadaj webhook URL.")
                return False
            try:
                self._parse_discord_webhook_spec(path, fallback_content=self.inputName.text().strip())
            except ValueError as e:
                QMessageBox.warning(
                    self,
                    "Neplatný Discord Webhook",
                    str(e),
                )
                return False
            return True

        if action_key == "WeatherWidget":
            try:
                self._parse_weather_widget_spec(path)
            except ValueError as e:
                QMessageBox.warning(
                    self,
                    "Neplatný Weather Widget",
                    str(e),
                )
                return False
            return True

        if action_key == "MetricWidget":
            try:
                self._parse_metric_widget_spec(path)
            except ValueError as e:
                QMessageBox.warning(
                    self,
                    "Neplatný Metric Widget",
                    str(e),
                )
                return False
            return True

        if action_key == "OpenURL":
            if not path:
                QMessageBox.warning(self, "Chyba", "Pre Open URL zadaj URL.")
                return False
            parsed = urlparse(path)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                QMessageBox.warning(
                    self,
                    "Neplatna URL",
                    "URL musi zacinat http:// alebo https:// a obsahovat host.",
                )
                return False
            return True

        if action_key == "SpotifyPlaylist":
            if not path:
                QMessageBox.warning(
                    self,
                    "Chyba",
                    "Zadaj playlist ID, URL alebo spotify:playlist:ID.",
                )
                return False
            if not self._normalize_spotify_playlist_uri(path):
                QMessageBox.warning(
                    self,
                    "Neplatný playlist",
                    "Neviem rozpoznať playlist. Použi ID alebo URL/URI.",
                )
                return False
            return True

        return True



    def save_button_settings(self, silent: bool = False):
        if not self.selected_button_name:
            if silent:
                return
            first_key = next(iter(self.grid_buttons.keys()), None)
            if first_key:
                self.on_button_click(first_key)
            else:
                QMessageBox.information(self, "Informácia", "Najprv vyberte tlačidlo!")
                return

        name = self.inputName.text().strip()
        path = self.inputPath.text().strip()
        icon = self.lineIconPath.text().strip()
        action = (self.comboActionType.currentText() or "").strip()
        action_key = self._normalize_action(action)

        if icon.lower().endswith(".svg"):
            if QSvgRenderer is None:
                if not silent:
                    QMessageBox.warning(self, "SVG", "SVG nie je podporované (chýba QtSvg).")
                return
            conv = self._convert_svg_icon(icon, target_px=512)
            if not conv:
                if not silent:
                    QMessageBox.warning(self, "SVG", "Nepodarilo sa konvertovať SVG.")
                return
            icon = conv
            self.lineIconPath.setText(icon)

        if not name and not icon:
            if not silent:
                QMessageBox.warning(
                    self,
                    "Varovanie",
                    "Zadajte názov tlačidla alebo priraďte ikonu.",
                )
            return
        if not self._validate_action_path(action, path, silent=silent):
            return

        cur = self.profile_manager.current_profile
        profs = self.profile_manager.profiles
        selected_key = self._grid_cell_to_anchor.get(self.selected_button_name or "", self.selected_button_name or "")
        if not selected_key:
            return
        prof = profs.get(cur, {})
        existing = prof.get(selected_key, {})
        existing = existing if isinstance(existing, dict) else {}
        old_action_key = self._normalize_action(existing.get("action", "")) if isinstance(existing, dict) else ""
        rows = max(1, min(4, int(prof.get("rows", 3))))
        cols = max(1, min(4, int(prof.get("cols", 4))))
        coords = self._parse_btn_name(selected_key)
        if coords is None:
            span_rows = 1
            span_cols = 1
        else:
            span_rows, span_cols = self._get_button_span(existing, rows, cols, coords[0], coords[1])
            if getattr(self, "comboWidgetSize", None):
                ui_rows, ui_cols = self._parse_widget_size_text(self.comboWidgetSize.currentText())
                span_rows, span_cols = ui_rows, ui_cols
            span_rows, span_cols = self._fit_span_for_anchor(
                prof,
                rows,
                cols,
                selected_key,
                span_rows,
                span_cols,
            )

        defaults = self._default_button_style()
        style = getattr(self, "_current_button_style", None) or defaults
        norm_style = {
            "bg_color": self._normalize_hex_color(str(style.get("bg_color", defaults["bg_color"])), defaults["bg_color"]),
            "fg_color": self._normalize_hex_color(str(style.get("fg_color", defaults["fg_color"])), defaults["fg_color"]),
            "font": str(style.get("font", defaults.get("font", "")) or ""),
            "text_size": float(style.get("text_size", defaults["text_size"])),
        }
        norm_defaults = {
            "bg_color": self._normalize_hex_color(str(defaults.get("bg_color", "#000000")), "#000000"),
            "fg_color": self._normalize_hex_color(str(defaults.get("fg_color", "#FFFFFF")), "#FFFFFF"),
            "font": str(defaults.get("font", "") or ""),
            "text_size": float(defaults.get("text_size", 1.1)),
        }
        if (
            norm_style["bg_color"] == norm_defaults["bg_color"]
            and norm_style["fg_color"] == norm_defaults["fg_color"]
            and (norm_style.get("font") or "") == (norm_defaults.get("font") or "")
            and abs(norm_style["text_size"] - norm_defaults["text_size"]) < 0.01
        ):
            style = {}
        else:
            style = norm_style
        btn_data = {"name": name, "icon": icon, "action": action, "path": path, "style": style}
        if span_rows > 1:
            btn_data["span_rows"] = int(span_rows)
        if span_cols > 1:
            btn_data["span_cols"] = int(span_cols)
        if isinstance(existing, dict) and existing == btn_data:
            return
        profs[cur][selected_key] = btn_data

        btn = self.grid_buttons.get(selected_key)
        if btn:
            if icon and os.path.exists(icon):
                btn.setText(name if name else "")
                bg_color = self._icon_bg_color_for_btn(btn_data)
                self._set_button_icon(btn, icon, btn.width(), btn.height(), bg_color=bg_color)
            else:
                btn.setIcon(QIcon())
                btn.setText(name)
            self._apply_grid_button_style(btn, btn_data)

        self._request_save()
        if action_key in {"WeatherWidget", "MetricWidget"} or old_action_key in {"WeatherWidget", "MetricWidget"}:
            self._refresh_weather_sync_state()
            self._schedule_weather_sync(delay_ms=500, force=True)
            self._refresh_metric_preview_in_app()
            self._send_metric_widgets_to_esp(force=True)
        if not silent:
            self.statusBar().showMessage(f"Nastavenia pre '{name or 'tlačidlo'}' uložené")

    # ---------- profil manažment ----------

    def add_profile(self):
        text, ok = QInputDialog.getText(self, "Pridať profil", "Zadajte názov nového profilu:")
        if ok and text:
            try:
                name = self.profile_manager.add_profile(text.strip())
                self.listProfiles.addItem(name)
                self._set_selected_profile(name)
                self.profile_manager.load_profile(name)
                self._request_save()
                self.statusBar().showMessage(f"Profil '{name}' pridaný")
            except ValueError as e:
                QMessageBox.warning(self, "Varovanie", str(e))

    def rename_profile(self):
        current = self._current_profile_name()
        if not current:
            return
        text, ok = QInputDialog.getText(self, "Premenovať profil", "Zadajte nový názov:", text=current)
        if ok and text:
            try:
                new_name = self.profile_manager.rename_profile(current, text.strip())
                it = self.listProfiles.currentItem()
                if it:
                    it.setText(new_name)
                self._set_selected_profile(new_name)
                self.profile_manager.load_profile(new_name)
                self._request_save()
                self.statusBar().showMessage(f"Profil premenovaný na '{new_name}'")
            except ValueError as e:
                QMessageBox.warning(self, "Varovanie", str(e))

    def delete_profile(self):
        current = self._current_profile_name()
        if not current:
            return
        confirm = QMessageBox.question(
            self,
            "Potvrdiť vymazanie",
            f"Naozaj chcete vymazať profil '{current}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            try:
                self.profile_manager.delete_profile(current)
                row = self.listProfiles.currentRow()
                it = self.listProfiles.takeItem(row)
                del it
                new_row = max(0, row - 1)
                self.listProfiles.setCurrentRow(new_row)

                new_item = self.listProfiles.item(new_row)
                if new_item:
                    self.profile_manager.load_profile(new_item.text())
                else:
                    self.profile_manager.current_profile = None

                self._request_save()
                self._refresh_weather_sync_state()
                self._schedule_weather_sync(delay_ms=220, force=True)
                self._refresh_metric_preview_in_app()
                self._send_metric_widgets_to_esp(force=True)
                self.statusBar().showMessage(f"Profil '{current}' vymazaný")
            except ValueError as e:
                QMessageBox.warning(self, "Varovanie", str(e))

    # ---------- výber ikon ----------

    def _get_current_profile_and_cell(self):
        """
        Vráti (prof, cell_key, btn_dict) pre aktuálne vybrané tlačidlo v aktuálnom profile.
        Vyhodí RuntimeError, ak nie je profil alebo žiadne tlačidlo.
        """
        cur = self.profile_manager.current_profile
        if not cur:
            raise RuntimeError("Nie je vybraný žiadny profil.")

        prof = self.profile_manager.profiles.setdefault(cur, {})
        cell_key = self.selected_button_name
        if cell_key:
            cell_key = self._grid_cell_to_anchor.get(cell_key, cell_key)

        # fallback – ak ešte nebolo kliknuté na nič, skús prvé tlačidlo v gride
        if not cell_key:
            if not self.grid_buttons:
                raise RuntimeError("Nie je vybrané tlačidlo a grid je prázdny.")
            cell_key = next(iter(self.grid_buttons.keys()))
            self.selected_button_name = cell_key

        btn = prof.setdefault(cell_key, {})
        return prof, cell_key, btn



    def choose_icon(self):
        if not self.selected_button_name:
            QMessageBox.information(self, "Informácia", "Najprv vyberte tlačidlo!")
            return
        selected_key = self._grid_cell_to_anchor.get(self.selected_button_name, self.selected_button_name)
        if not selected_key:
            QMessageBox.information(self, "Informácia", "Najprv vyberte tlačidlo!")
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Vyberte ikonu",
            "",
            "Obrázky/GIF (*.png *.jpg *.jpeg *.bmp *.webp *.ico *.svg *.gif);;All files (*.*)",
        )
        if not path:
            return

        icons_dir = os.path.join(os.path.dirname(__file__), "icons")
        os.makedirs(icons_dir, exist_ok=True)
        ext = Path(path).suffix.lower()
        if ext == ".svg":
            if QSvgRenderer is None:
                QMessageBox.warning(self, "SVG", "SVG nie je podporované (chýba QtSvg).")
                return
            dest_path = self._convert_svg_icon(path, target_px=512)
            if not dest_path:
                QMessageBox.warning(self, "SVG", "Nepodarilo sa konvertovať SVG.")
                return
            icon_name = os.path.basename(dest_path)
        else:
            icon_name = os.path.basename(path)
            dest_path = os.path.join(icons_dir, icon_name)

        try:
            if ext == ".gif":
                # Keep GIF file as-is; codegen can consume it and extract the first frame.
                if os.path.abspath(path) != os.path.abspath(dest_path):
                    shutil.copy2(path, dest_path)
            elif ext != ".svg":
                img = Image.open(path)
                img = ImageOps.exif_transpose(img)
                img.save(dest_path, optimize=True)

            cur = self.profile_manager.current_profile
            btn_data = self.profile_manager.profiles[cur].setdefault(selected_key, {})
            btn_data.setdefault("name", self.inputName.text())
            btn_data["icon"] = dest_path

            self.lineIconPath.setText(dest_path)

            btn = self.grid_buttons.get(selected_key)
            if btn:
                w, h = btn.width(), btn.height()
                if not self.inputName.text().strip():
                    btn.setText("")
                bg_color = self._icon_bg_color_for_btn(btn_data)
                self._set_button_icon(btn, dest_path, w, h, bg_color=bg_color)
            self._request_save()
            self._schedule_button_autosave(delay_ms=0)
            self.statusBar().showMessage(f"Ikona '{icon_name}' pridaná")
        except Exception as e:
            QMessageBox.critical(self, "Chyba", f"Nepodarilo sa pridať ikonu: {e}")

    def _path_dialog_start_dir(self, raw: str) -> str:
        cmd = self._extract_command_head(raw).strip().strip('"').strip("'")
        if cmd:
            expanded = os.path.expanduser(os.path.expandvars(cmd))
            if os.path.isfile(expanded):
                return str(Path(expanded).parent)
            if os.path.isdir(expanded):
                return expanded
        return str(Path.home())

    def choose_app_path(self):
        action = (self.comboActionType.currentText() if hasattr(self, "comboActionType") else "").strip()
        if action != "OpenApp":
            QMessageBox.information(
                self,
                "Výber aplikácie",
                "Pre výber aplikácie nastav Action na OpenApp.",
            )
            return

        start_dir = self._path_dialog_start_dir(self.inputPath.text() if hasattr(self, "inputPath") else "")
        if IS_WINDOWS:
            filters = "Executables (*.exe *.bat *.cmd *.ps1);;All files (*.*)"
        else:
            filters = "All files (*)"

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Vyber aplikáciu",
            start_dir,
            filters,
        )
        if not path:
            return

        path = os.path.expanduser(os.path.expandvars(path))
        if " " in path and not (path.startswith('"') and path.endswith('"')):
            path = f"\"{path}\""
        self.inputPath.setText(path)
        self._schedule_button_autosave(delay_ms=0)

    # ---------- špeciálne hardvérové tlačidlá ----------

    def handle_button_a(self):
        action = self._combo_selected_value(self.comboBtnA)
        self.execute_special_action(action)

    def handle_button_b(self):
        action = self._combo_selected_value(self.comboBtnB)
        self.execute_special_action(action)

    def execute_special_action(self, action: str):
        action = self._normalize_action((action or "").strip())

        if action == "NextProfile":
            row = self.listProfiles.currentRow()
            total = self.listProfiles.count()
            if total:
                self.listProfiles.setCurrentRow((row + 1) % total)
            return

        if action == "PreviousProfile":
            row = self.listProfiles.currentRow()
            total = self.listProfiles.count()
            if total:
                self.listProfiles.setCurrentRow((row - 1) % total)
            return

        if action == "CustomAction":
            QMessageBox.information(self, "Custom", "Custom action executed!")
            return

        if action in {
            "PlayMusic", "Mute", "Next", "Previous",
            "MuteMic", "UnmuteMic",
            "OpenURL",
            "BrightnessUp", "BrightnessDown",
            "MinimizeWindow", "MaximizeWindow",
            "CloseWindow", "SwitchWindow",
            "LockPC", "SleepPC", "ShutdownPC", "RestartPC",
            "CopyToClipboard", "PasteClipboard",
        }:
            self.handle_system_action(action)
            return
        
        if action == "SmartRelay1Toggle":
            self._send_smarthome_toggle(1)
            return

        if action == "SmartRelay2Toggle":
            self._send_smarthome_toggle(2)
            return

        if action == "SmartRelay3Toggle":
            self._send_smarthome_toggle(3)
            return

        if action == "SmartRelay4Toggle":
            self._send_smarthome_toggle(4)
            return


    # ---------- export / codegen ----------

    def export_config_to_json(self):
        export_path, _ = QFileDialog.getSaveFileName(
            self, "Exportovať JSON", "", "JSON súbory (*.json)"
        )
        if not export_path:
            return
        try:
            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(self.profile_manager.profiles, f, indent=4, ensure_ascii=False)
            QMessageBox.information(self, "Úspech", f"Konfigurácia bola uložená do:\n{export_path}")
        except Exception as e:
            QMessageBox.critical(self, "Chyba", f"Export zlyhal: {e}")

    def upload_to_esp32(self, sketch_folder: str, progress=None):
        def _progress(msg: str) -> None:
            if callable(progress):
                progress(msg)

        if not self.arduino_cli:
            raise RuntimeError("arduino-cli nenájdené. Skontroluj inštaláciu alebo PATH.")

        port = self.serial_monitor.serial_port or self.serial_monitor.detect_esp32_port()
        if not port:
            raise RuntimeError("ESP32 port nebol nájdený.")

        build_path = os.path.abspath(os.path.join(sketch_folder, "_build"))
        os.makedirs(build_path, exist_ok=True)

        fqbn = "esp32:esp32:esp32s3:PSRAM=opi,FlashSize=16M"
        cli = self.arduino_cli
        env = self._arduino_env()
        compile_props = [
            "build.partitions=default_16MB",
            "upload.maximum_size=6553600",
            # Zapni USB CDC na ESP32-S3, aby Serial išiel po /dev/ttyACM*
            "build.usb_cdc_on_boot=1",
        ]

        print(f"[Upload] Používam arduino-cli: {cli}")
        print(f"[Upload] Port: {port}")
        print(f"[Upload] Sketch folder: {sketch_folder}")
        print(f"[Upload] Build path: {build_path}")
        compile_signature = self._compute_compile_signature(sketch_folder, fqbn, compile_props)
        cached_meta = self._load_build_meta(build_path)
        can_skip_compile = bool(cached_meta == compile_signature and self._build_artifacts_ready(build_path))

        if can_skip_compile:
            _progress("Compiling... skipped (no changes)")
            print("[Upload] compile skipped (cache hit).")
        else:
            jobs = max(1, min(12, int(os.cpu_count() or 4)))
            compile_cmd = [cli, "compile", "--fqbn", fqbn]
            for prop in compile_props:
                compile_cmd += ["--build-property", prop]
            compile_cmd += [
                "--build-path",
                build_path,
                "--jobs",
                str(jobs),
                sketch_folder,
            ]
            _progress("Compiling...")
            comp = self._arduino_cli_run(
                compile_cmd,
                env=env,
            )

            print("[Upload] compile returncode:", comp.returncode)
            print("[Upload] compile stdout:\n", comp.stdout)
            print("[Upload] compile stderr:\n", comp.stderr)

            if comp.returncode != 0:
                raise RuntimeError(
                    f"Kompilácia zlyhala.\nSTDOUT:\n{comp.stdout}\n\nSTDERR:\n{comp.stderr}"
                )
            self._save_build_meta(build_path, compile_signature)

        # reset DTR/RTS pre ESP (ak je dostupné)
        try:
            with serial.Serial(port, 1200, timeout=0.2) as s:
                s.dtr = False
                s.rts = True
                time.sleep(0.05)
                s.rts = False
        except Exception:
            pass

        # upload môže byť na niektorých kusoch ESP32-S3 nestabilný pri 921600 baud,
        # preto po chybe skúšame nižšie rýchlosti a fallback bez stubu
        upload_speeds = ["921600", None, "460800", "115200", "57600", "38400"]
        upload_variants = [
            {"label": "default", "props": {}},
            {"label": "no-stub", "props": {"upload.flags": "--no-stub"}},
        ]
        preferred_speed = getattr(self, "_last_upload_speed", None)
        if preferred_speed in upload_speeds:
            upload_speeds = [preferred_speed] + [s for s in upload_speeds if s != preferred_speed]

        preferred_variant = getattr(self, "_last_upload_variant", None)
        if preferred_variant:
            upload_variants = (
                [v for v in upload_variants if v["label"] == preferred_variant]
                + [v for v in upload_variants if v["label"] != preferred_variant]
            )
        last_stdout = ""
        last_stderr = ""
        last_code: int | None = None

        for speed in upload_speeds:
            for variant in upload_variants:
                cmd = [
                    cli,
                    "upload",
                    "-p",
                    port,
                    "--fqbn",
                    fqbn,
                    "--input-dir",
                    build_path,
                ]
                if speed:
                    cmd += ["--upload-property", f"upload.speed={speed}"]
                for key, value in variant["props"].items():
                    cmd += ["--upload-property", f"{key}={value}"]

                speed_label = speed or "default"
                variant_label = variant["label"]
                _progress(f"Uploading ({speed_label}, {variant_label})...")
                print(
                    f"[Upload] Spúšťam upload (speed={speed_label}, mode={variant_label})"
                    f" -> {' '.join(cmd)}"
                )

                try:
                    up = self._arduino_cli_run(
                        cmd,
                        timeout=240,
                        env=env,
                    )
                except Exception as exc:
                    last_code = -1
                    last_stdout = ""
                    last_stderr = (
                        f"Výnimka pri uploade (speed={speed_label}, mode={variant_label}): {exc}"
                    )
                    print(last_stderr)
                    continue

                print("[Upload] upload returncode:", up.returncode)
                print("[Upload] upload stdout:\n", up.stdout)
                print("[Upload] upload stderr:\n", up.stderr)

                if up.returncode == 0:
                    _progress("Upload done.")
                    self._last_upload_speed = speed
                    self._last_upload_variant = variant_label
                    # Po úspešnom uploade jemne "kopneme" DTR/RTS, nech sa ESP rovno spustí
                    try:
                        with serial.Serial(port, 115200, timeout=0.2) as s:
                            s.dtr = False
                            s.rts = False
                            time.sleep(0.05)
                    except Exception:
                        pass
                    return

                last_code = up.returncode
                last_stdout = up.stdout
                last_stderr = up.stderr
                # skúšame ďalšiu (nižšiu) rýchlosť alebo režim

        raise RuntimeError(
            f"Upload zlyhal na porte {port} aj po opakovaní (returncode {last_code})."
            f"\nSkúšané rýchlosti: {', '.join(s or 'default' for s in upload_speeds)}"
            f"\nRežimy: {', '.join(v['label'] for v in upload_variants)}"
            f"\nPosledný pokus STDOUT:\n{last_stdout}\n\nSTDERR:\n{last_stderr}"
        )

    def _on_upload_error(self, msg: str):
        self.statusBar().showMessage("Nahrávanie zlyhalo")
        self._set_upload_busy(False)
        QMessageBox.critical(self, "Chyba", msg)
        try:
            t = getattr(self, "upload_thread", None)
            if isinstance(t, QThread) and t.isRunning():
                t.quit()
                t.wait(1000)
        except Exception as e:
            print(f"_on_upload_error cleanup: {e}")
        self.restart_serial_monitor()

    def _on_main_esp_upload_done(self):
        self._app_flags["esp_setup_complete"] = True
        self._request_save()
        self._set_upload_busy(False)

    def handle_upload_to_esp(self):
        """Spracovanie uploadu na ESP"""
        if not self.arduino_cli:
            QMessageBox.warning(self, "Nahrávanie", "arduino-cli nenájdené. Nainštaluj ho alebo pridaj do PATH.")
            return
        if not self._esp32_core_installed():
            self._ensure_esp32_core_async(on_ready=self.handle_upload_to_esp)
            return
        if not self._lovyangfx_ready():
            self._ensure_lovyangfx_async(on_ready=self.handle_upload_to_esp)
            return
        # zastav sériový monitor pred uploadom
        if hasattr(self, "serial_monitor"):
            self.serial_monitor.stop()
        self._pause_port_refresh()

        try:
            sketch_dir = _appdata_dir() / "GeneratedSketch"
            sketch_dir.mkdir(parents=True, exist_ok=True)
            ino_path = sketch_dir / "GeneratedSketch.ino"
            # správne volanie generate_main_ino z core.codegen
            generate_main_ino(self.profile_manager.profiles, str(ino_path), self._display_settings)
        except Exception as e:
            QMessageBox.critical(self, "Chyba", f"Chyba pri generovaní kódu: {e}")
            # reštart serial monitora aj pri chybe
            if hasattr(self, "serial_monitor"):
                self.serial_monitor.start()
            self._resume_port_refresh()
            return

        self.statusBar().showMessage("Nahrávanie...")
        self._set_upload_busy(True)

        self.upload_thread = QThread()
        self.uploader = UploaderWorker(sketch_dir, self.upload_to_esp32)
        self.uploader.moveToThread(self.upload_thread)

        self.upload_thread.started.connect(self.uploader.run)
        self.uploader.finished.connect(self.upload_thread.quit)
        self.uploader.finished.connect(self.uploader.deleteLater)
        self.upload_thread.finished.connect(self.upload_thread.deleteLater)

        self.uploader.error.connect(self._on_upload_error)
        self.uploader.progress.connect(self.statusBar().showMessage)
        self.uploader.finished.connect(lambda: self.statusBar().showMessage("Nahrávanie dokončené"))
        self.uploader.finished.connect(self._on_main_esp_upload_done)
        self.uploader.finished.connect(self.restart_serial_monitor)

        self.upload_thread.start()


    # ---------- SmartHome ESP2 – codegen ----------

    def generate_smarthome_ino(
        self,
        wifi_ssid: str,
        wifi_pass: str,
        r1: str,
        r2: str,
        r3: str,
        r4: str,
    ) -> Path:
        """
        Vygeneruje SmartHomeSketch/SmartHomeSketch.ino podľa SMART_HOME_TEMPLATE
        a vráti cestu k priečinku sketchu (nie k .ino súboru).
        """
        return generate_smarthome_sketch(
            project_root=self.project_root,
            wifi_ssid=wifi_ssid,
            wifi_pass=wifi_pass,
            relay_names=[r1, r2, r3, r4],
            write_text_if_changed=self._write_text_if_changed,
        )

    # ================= SMART HOME PERSISTENCE =================

    def _setup_smart_home_persistence(self):
        """Naviaže zmeny v Smart Home sekcii tak, aby sa automaticky ukladali."""
        widgets = [
            self.lineSmartSSID,
            self.lineSmartPass,
            self.lineSmartBaseUrl,
            self.lineSmartRelay1,
            self.lineSmartRelay2,
            self.lineSmartRelay3,
            self.lineSmartRelay4,
        ]

        for w in widgets:
            if w is not None:
                w.textChanged.connect(self._trigger_smart_home_save)

    def _init_status_widgets(self):
        """Permanentné widgety v status bare (SmartHome IP)."""
        try:
            lbl = QLabel()
            lbl.setTextFormat(Qt.TextFormat.PlainText)
            lbl.setStyleSheet("color:#9ca3af; padding-left:8px;")
            self.smart_home_status_label = lbl
            self.statusBar().addPermanentWidget(lbl, 0)
            self._refresh_smart_home_status_label()
        except Exception as e:
            print(f"Status widget init error: {e}")

    def _refresh_smart_home_status_label(self):
        """Aktualizuje text v status bare podľa smart_home_base_url."""
        lbl = getattr(self, "smart_home_status_label", None)
        base = getattr(self, "smart_home_base_url", SMART_HOME_BASE_URL)
        if lbl:
            lbl.setText(f"SmartHome: {base}")

    def _set_smart_home_base_url(self, url: str) -> None:
        """Nastaví a zaktualizuje Base URL v UI aj status bare (bez automatického save)."""
        base = normalize_smart_home_base_url(url)
        self.smart_home_base_url = base
        if self.lineSmartBaseUrl:
            self.lineSmartBaseUrl.blockSignals(True)
            self.lineSmartBaseUrl.setText(base)
            self.lineSmartBaseUrl.blockSignals(False)
        self._refresh_smart_home_status_label()

    def save_smart_home_base_url(self):
        """Manuálne uloženie IP/Base URL z tlačidla."""
        base = self.lineSmartBaseUrl.text().strip() if self.lineSmartBaseUrl else ""
        self._set_smart_home_base_url(base)
        self._save_smart_home_state()
        self.statusBar().showMessage("SmartHome IP uložená", 3000)

    def _trigger_smart_home_save(self):
        """Spustí debounce timer pre uloženie Smart Home nastavení."""
        if hasattr(self, "smartHomeSaveTimer") and self.smartHomeSaveTimer is not None:
            self.smartHomeSaveTimer.start()



    def _collect_smart_home_state(self) -> dict:
        """Načíta aktuálne hodnoty z polí."""
        return {
            "wifi_ssid": self.lineSmartSSID.text().strip(),
            "wifi_pass": self.lineSmartPass.text(),
            "base_url": self.lineSmartBaseUrl.text().strip(),
            "relay1_name": self.lineSmartRelay1.text().strip(),
            "relay2_name": self.lineSmartRelay2.text().strip(),
            "relay3_name": self.lineSmartRelay3.text().strip(),
            "relay4_name": self.lineSmartRelay4.text().strip(),
        }


    def _apply_smart_home_state(self, data: dict):
        """Nastaví UI podľa uložených hodnôt."""
        try:
            self.lineSmartSSID.blockSignals(True)
            self.lineSmartPass.blockSignals(True)
            self.lineSmartBaseUrl.blockSignals(True)
            self.lineSmartRelay1.blockSignals(True)
            self.lineSmartRelay2.blockSignals(True)
            self.lineSmartRelay3.blockSignals(True)
            self.lineSmartRelay4.blockSignals(True)

            self.lineSmartSSID.setText(data.get("wifi_ssid", ""))
            self.lineSmartPass.setText(data.get("wifi_pass", ""))
            self.lineSmartBaseUrl.setText(data.get("base_url", ""))
            self.lineSmartRelay1.setText(data.get("relay1_name", ""))
            self.lineSmartRelay2.setText(data.get("relay2_name", ""))
            self.lineSmartRelay3.setText(data.get("relay3_name", ""))
            self.lineSmartRelay4.setText(data.get("relay4_name", ""))

        finally:
            self.lineSmartSSID.blockSignals(False)
            self.lineSmartPass.blockSignals(False)
            self.lineSmartBaseUrl.blockSignals(False)
            self.lineSmartRelay1.blockSignals(False)
            self.lineSmartRelay2.blockSignals(False)
            self.lineSmartRelay3.blockSignals(False)
            self.lineSmartRelay4.blockSignals(False)
        self._refresh_smart_home_status_label()


    def _save_smart_home_state(self):
        """Uloží Smart Home dáta do JSON."""
        try:
            data = self._collect_smart_home_state()
            data["base_url"] = normalize_smart_home_base_url(data.get("base_url", ""))
            self.smart_home_base_url = data["base_url"]
            self._refresh_smart_home_status_label()
            save_smart_home_state(data)
        except Exception as e:
            print(f"[SMART_HOME] ERROR saving: {e}")


    def _load_smart_home_state(self):
        """Načíta posledný uložený stav."""
        try:
            data = load_smart_home_state()
            if data:
                self._apply_smart_home_state(data)
                self.smart_home_base_url = normalize_smart_home_base_url(data.get("base_url", ""))
                self._refresh_smart_home_status_label()
        except Exception as e:
            print(f"[SMART_HOME] ERROR loading: {e}")


    def handle_upload_smarthome_esp(self):
        """
        Vygeneruje SmartHome sketch podľa vstupov v tabu,
        skompiluje a nahrá ho na ESP (rovnakým mechanizmom ako hlavný sketch).
        """
        if not self.arduino_cli:
            QMessageBox.warning(self, "SmartHome", "arduino-cli nenájdené. Nainštaluj ho alebo pridaj do PATH.")
            return
        if not self._esp32_core_installed():
            self._ensure_esp32_core_async(on_ready=self.handle_upload_smarthome_esp)
            return
        if not self._lovyangfx_ready():
            self._ensure_lovyangfx_async(on_ready=self.handle_upload_smarthome_esp)
            return
        # 1) zastav sériový monitor (aby port nebol zamknutý)
        if hasattr(self, "serial_monitor"):
            self.serial_monitor.stop()
        self._pause_port_refresh()

        # 2) načítaj hodnoty z UI
        wifi_ssid = (self.lineSmartSSID.text() if self.lineSmartSSID else "").strip()
        wifi_pass = (self.lineSmartPass.text() if self.lineSmartPass else "").strip()
        self.smart_home_base_url = (self.lineSmartBaseUrl.text() if self.lineSmartBaseUrl else "").strip() or SMART_HOME_BASE_URL

        r1 = (self.lineSmartRelay1.text() if self.lineSmartRelay1 else "").strip() or "Relé 1"
        r2 = (self.lineSmartRelay2.text() if self.lineSmartRelay2 else "").strip() or "Relé 2"
        r3 = (self.lineSmartRelay3.text() if self.lineSmartRelay3 else "").strip() or "Relé 3"
        r4 = (self.lineSmartRelay4.text() if self.lineSmartRelay4 else "").strip() or "Relé 4"

        # základná kontrola – prázdne SSID nedáva zmysel
        if not wifi_ssid:
            QMessageBox.warning(self, "SmartHome", "Zadaj aspoň Wi-Fi SSID.")
            if hasattr(self, "serial_monitor"):
                self.serial_monitor.start()
            self._resume_port_refresh()
            return

        try:
            # 3) vygeneruj .ino a dostaň priečinok sketchu
            sketch_dir = self.generate_smarthome_ino(
                wifi_ssid, wifi_pass,
                r1, r2, r3, r4,
            )
        except Exception as e:
            QMessageBox.critical(self, "SmartHome", f"Chyba pri generovaní kódu:\n{e}")
            if hasattr(self, "serial_monitor"):
                self.serial_monitor.start()
            self._resume_port_refresh()
            return

        self.statusBar().showMessage("Nahrávam SmartHome ESP...")
        self._set_upload_busy(True)

        # 4) použijeme rovnaký worker a upload_to_esp32 ako pri hlavnom sketchi
        self.upload_thread = QThread()
        self.uploader = UploaderWorker(str(sketch_dir), self.upload_to_esp32)
        self.uploader.moveToThread(self.upload_thread)

        self.upload_thread.started.connect(self.uploader.run)
        self.uploader.finished.connect(self.upload_thread.quit)
        self.uploader.finished.connect(self.uploader.deleteLater)
        self.upload_thread.finished.connect(self.upload_thread.deleteLater)

        self.uploader.error.connect(self._on_upload_error)
        self.uploader.progress.connect(self.statusBar().showMessage)
        self.uploader.finished.connect(
            lambda: self.statusBar().showMessage("SmartHome ESP – nahrávanie dokončené", 4000)
        )
        self.uploader.finished.connect(lambda: self._set_upload_busy(False))
        self.uploader.finished.connect(self.restart_serial_monitor)

        self.upload_thread.start()


    def restart_serial_monitor(self):
        """Reštart sériového monitora po uploade"""
        if hasattr(self, "serial_monitor"):
            self.serial_monitor.start()
        self._resume_port_refresh()

    def _shutdown_cleanup(self):
        try:
            if hasattr(self, "port_timer"):
                self.port_timer.stop()
            if hasattr(self, "serial_monitor"):
                self.serial_monitor.stop()
            if hasattr(self, "media_status") and self.media_status is not None:
                try:
                    self.media_status.stop()
                    self.media_status.wait(1000)
                except Exception as e:
                    print(f"MediaStatusProvider stop error: {e}")
            if hasattr(self, "system_stats") and self.system_stats is not None:
                try:
                    self.system_stats.stop()
                except Exception as e:
                    print(f"SystemStatsProvider stop error: {e}")
            if hasattr(self, "_media_progress_timer") and self._media_progress_timer is not None:
                self._media_progress_timer.stop()
            if hasattr(self, "media") and self.media is not None:
                try:
                    self.media.cleanup()
                except Exception as e:
                    print(f"Media cleanup error: {e}")


            t = getattr(self, "upload_thread", None)
            if isinstance(t, QThread) and t.isRunning():
                t.quit()
                t.wait(1000)
        except Exception as e:
            print(f"Chyba pri zatváraní: {e}")
        finally:
            if self._tray_icon is not None:
                self._tray_icon.hide()
                self._tray_icon.deleteLater()
                self._tray_icon = None

        # uloženie geometrie okna
        try:
            fp = _appdata_dir() / "window.bin"
            with open(fp, "wb") as f:
                f.write(self.saveGeometry())
        except Exception as e:
            print(f"Save window geometry failed: {e}")

        # bezpečný final save
        try:
            if self._save_timer.isActive():
                self._save_timer.stop()
            self._save_state_now()
        except Exception as e:
            print(f"Final save failed: {e}")

    def closeEvent(self, event):
        if (
            not self._is_quitting
            and self._tray_icon is not None
            and self._tray_icon.isVisible()
        ):
            event.ignore()
            self.hide()
            self._tray_icon.showMessage(
                "MacroTouch",
                "MacroTouch is still running in the background.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
            return

        self._shutdown_cleanup()
        event.accept()


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(add_help=False)
    arg_parser.add_argument("--background", action="store_true")
    args, qt_args = arg_parser.parse_known_args(sys.argv[1:])

    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    if hasattr(Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("MacroTouch")
    app.setApplicationVersion("1.0.0")
    app.setQuitOnLastWindowClosed(False)
    window = MacroTouchApp(start_hidden=args.background)
    exit_code = app.exec()
    if getattr(window, "media", None):
        try:
            window.media.cleanup()
        except Exception:
            pass
    sys.exit(exit_code)

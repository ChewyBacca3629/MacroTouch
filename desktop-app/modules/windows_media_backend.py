# core/windows_media_backend.py
from __future__ import annotations

import subprocess
from typing import Optional

from ctypes import cast, POINTER

_PYCAW_ERROR = None
try:
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore
    from comtypes import CLSCTX_ALL  # type: ignore
    _HAS_PYCAW = True
except Exception as e:
    AudioUtilities = None
    IAudioEndpointVolume = None
    CLSCTX_ALL = None
    _HAS_PYCAW = False
    _PYCAW_ERROR = e

from .media_backend import IMediaBackend
from .media_controls import (
    WindowsMediaController,  # toto už v projekte máš
    VK,
    minimize_active_window as _minimize_active_window,
    toggle_maximize_active_window as _toggle_maximize_active_window,
)


class WindowsMediaBackend(IMediaBackend):
    """Media backend for Windows using pycaw and custom media controller."""
    def __init__(self) -> None:
        """Initialize audio endpoint and media controller."""
        # Init audio endpoint (pycaw optional)
        self._volume = None
        if _HAS_PYCAW:
            try:
                devices = AudioUtilities.GetSpeakers()
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)  # type: ignore
                self._volume = cast(POINTER(IAudioEndpointVolume), interface)
            except Exception as e:
                print(f"[WindowsMediaBackend] Audio init failed: {e}")
                self._volume = None
        else:
            print(f"[WindowsMediaBackend] pycaw unavailable: {_PYCAW_ERROR}")
        self._media = WindowsMediaController()

    # ---------- HLASITOSŤ ----------

    def set_volume(self, percent: int) -> None:
        """Set master volume to given percent."""
        vol = max(0, min(100, percent)) / 100.0
        if self._volume:
            self._volume.SetMasterVolumeLevelScalar(vol, None)
            return
        try:
            self._media.set_volume(vol)
        except Exception as e:
            print(f"[WindowsMediaBackend] set_volume fallback failed: {e}")

    def change_volume(self, delta_percent: int) -> None:
        """Adjust volume by delta percent."""
        if self._volume:
            current = self._volume.GetMasterVolumeLevelScalar() * 100.0
            self.set_volume(int(current + delta_percent))
            return
        try:
            self._media.change_volume(delta_percent / 100.0)
        except Exception as e:
            print(f"[WindowsMediaBackend] change_volume fallback failed: {e}")

    def toggle_mute(self) -> None:
        """Toggle system mute."""
        if self._volume:
            mute = self._volume.GetMute()
            self._volume.SetMute(not mute, None)
            return
        try:
            self._media.toggle_mute()
        except Exception as e:
            print(f"[WindowsMediaBackend] toggle_mute fallback failed: {e}")

    # ---------- MEDIA ----------

    def play_pause(self) -> None:
        """Toggle play/pause via media controller."""
        self._media.play_pause()

    def next_track(self) -> None:
        """Skip to next track."""
        self._media.next()

    def prev_track(self) -> None:
        """Skip to previous track."""
        self._media.previous()

    # ---------- APLIKÁCIE / OKNÁ ----------

    def open_app(self, path: str) -> None:
        """Launch application/path using shell."""
        # môžeš používať nircmd alebo normálne Popen
        subprocess.Popen(path, shell=True)

    def send_keys(self, keys: str) -> None:
        """Send hotkey sequence, preferring pyautogui if available."""
        seq = (keys or "").strip()
        if not seq:
            return

        # Prefer pyautogui, lebo je jednoduché na hotkeys typu ctrl+alt+k
        try:
            import pyautogui  # type: ignore

            tokens = [t for t in seq.replace(" ", "").split("+") if t]
            if tokens:
                pyautogui.hotkey(*tokens)
                return
        except ImportError:
            pass
        except Exception as e:
            print(f"[WindowsMediaBackend] pyautogui hotkey failed: {e}")

        # Fallback cez WScript.Shell SendKeys (nepotrebuje ďalšie moduly)
        try:
            ps_seq = seq.replace("'", "''")
            ps_script = (
                "$wshell = New-Object -ComObject wscript.shell; "
                f"$wshell.SendKeys('{ps_seq}')"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                check=False,
            )
        except Exception as e:
            print(f"[WindowsMediaBackend] send_keys fallback failed: {e}")

    def minimize_active_window(self) -> None:
        """Minimalize active window (delegated)."""
        _minimize_active_window()

    def toggle_maximize_active_window(self) -> None:
        """Maximize/restore active window (delegated)."""
        _toggle_maximize_active_window()

    # ---------- JAS ----------

    def set_brightness(self, percent: int) -> None:
        """Set brightness via media controller."""
        self._media.set_brightness(int(percent))

    def change_brightness(self, delta_percent: int) -> None:
        """Change brightness via media controller."""
        self._media.change_brightness(int(delta_percent))

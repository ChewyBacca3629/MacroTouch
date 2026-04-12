# core/media_status.py
from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from typing import Optional, Dict, Any

from PyQt6.QtCore import QThread, pyqtSignal

from .logging import get_logger
from .platform_env import IS_WINDOWS, IS_LINUX

logger = get_logger(__name__)

try:
    # Global System Media Transport Controls (SMTC)
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as SMTC,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )

    _HAS_WINSDK = True
except Exception as e:  # ImportError + prípadné iné problémy
    logger.info("[MediaStatusProvider] winsdk/windows.media.control nie je dostupné: %s", e)
    _HAS_WINSDK = False


# --- Linux / playerctl podpora ---
def _find_playerctl_cmd() -> list[str] | None:
    """Prefer direct playerctl; if sandboxed, try flatpak-spawn --host."""
    direct = shutil.which("playerctl")
    if direct:
        return [direct]

    spawn = shutil.which("flatpak-spawn")
    if spawn:
        # Flatpak host call
        return [spawn, "--host", "playerctl"]
    return None


_PLAYERCTL_CMD = _find_playerctl_cmd() if IS_LINUX else None


def _fetch_now_playing_linux() -> Optional[Dict[str, Any]]:
    """
    Prečíta aktuálnu MPRIS session cez playerctl (alebo flatpak-spawn --host playerctl).
    """
    if not _PLAYERCTL_CMD:
        return None

    fmt = "{{playerName}}|{{status}}|{{title}}|{{artist}}|{{album}}|{{mpris:length}}"
    try:
        proc = subprocess.run(
            _PLAYERCTL_CMD + ["metadata", "--format", fmt],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except Exception as e:
        logger.info("[MediaStatusProvider] playerctl zlyhal: %s", e)
        return None

    if proc.returncode != 0:
        return None

    line = proc.stdout.strip()
    if not line:
        return None

    parts = line.split("|")
    if len(parts) != 6:
        return None

    player, status, title, artist, album, length_raw = parts

    try:
        duration = float(length_raw) / 1_000_000.0  # mpris:length je v mikrosekundách
    except Exception:
        duration = 0.0

    # Získaj aktuálnu pozíciu – samostatný príkaz, lebo metadata ju nemusia obsahovať
    try:
        pos_proc = subprocess.run(
            _PLAYERCTL_CMD + ["position"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if pos_proc.returncode == 0:
            position = float((pos_proc.stdout or "0").strip())
        else:
            position = 0.0
    except Exception:
        position = 0.0

    is_playing = status.lower() == "playing"

    # Pozícia nepýtame (playerctl position by spúšťalo ďalší proces), dáme 0
    return {
        "source": player or "PLAYER",
        "source_app": player or "",
        "title": title or "",
        "artist": artist or "",
        "album": album or "",
        "position": position,
        "duration": duration,
        "is_playing": is_playing,
    }


async def _fetch_now_playing() -> Optional[Dict[str, Any]]:
    """
    Jedno asynchrónne čítanie aktuálnej media session zo SMTC.

    Vracia dict:
      {
        "source": "Spotify" / "Browser" / "Unknown",
        "source_app": "Spotify.exe" / "...",
        "title": "...",
        "artist": "...",
        "album": "...",
        "position": float_seconds,
        "duration": float_seconds,
        "is_playing": bool,
      }
    alebo None, ak nič nehrá.
    """
    manager = await SMTC.request_async()
    session = manager.get_current_session()
    if not session:
        return None

    try:
        props = await session.try_get_media_properties_async()
    except Exception as e:
        logger.info("[MediaStatusProvider] Chyba pri čítaní media properties: %s", e)
        return None

    timeline = session.get_timeline_properties()
    playback = session.get_playback_info()

    def _to_seconds(td) -> float:
        """Bezpečne konvertuje timedelta-like na sekundy."""
        try:
            return float(td.total_seconds())
        except Exception:
            return 0.0

    title = props.title or ""
    artist = props.artist or ""
    album = props.album_title or ""

    position = _to_seconds(timeline.position)
    duration = _to_seconds(timeline.end_time)

    status = playback.playback_status
    is_playing = status == PlaybackStatus.PLAYING

    # identifikácia zdroja podľa app id
    app_id = (session.source_app_user_model_id or "").strip()
    app_id_lower = app_id.lower()

    if "spotify" in app_id_lower:
        source = "Spotify"
    elif "chrome" in app_id_lower or "msedge" in app_id_lower or "firefox" in app_id_lower:
        source = "Browser"
    elif "vlc" in app_id_lower:
        source = "VLC"
    else:
        source = "Unknown"

    return {
        "source": source,
        "source_app": app_id,
        "title": title,
        "artist": artist,
        "album": album,
        "position": position,
        "duration": duration,
        "is_playing": is_playing,
    }


class MediaStatusProvider(QThread):
    """
    QThread, ktorý periodicky číta "now playing" zo SMTC (winsdk) a emitne dict.

    Signál:
      media_updated: dict
        - kľúče: source, source_app, title, artist, album, position, duration, is_playing

    V main.py ho už máš integrovaný:
      self.media_status = MediaStatusProvider(self, interval_ms=1000)
      self.media_status.media_updated.connect(self._on_media_status)
      self.media_status.start()
    """

    media_updated = pyqtSignal(dict)

    def __init__(self, parent=None, interval_ms: int = 1000):
        """Inicializuje interval a stav pre vlákno monitorovania médií."""
        super().__init__(parent)
        self._interval = max(200, int(interval_ms)) / 1000.0
        self._running = False

    def run(self) -> None:
        """Hlavná slučka: Linux playerctl alebo Windows SMTC podľa platformy."""
        if IS_LINUX:
            self._run_linux()
            return

        if not _HAS_WINSDK or not IS_WINDOWS:
            # winsdk nie je k dispozícii – vlákno hneď skončí
            logger.info("[MediaStatusProvider] winsdk nie je dostupné, media monitoring vypnutý.")
            return

        self._running = True

        # vlastný event loop v tomto vlákne
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        last_payload: Optional[Dict[str, Any]] = None

        try:
            while self._running:
                try:
                    data = loop.run_until_complete(_fetch_now_playing())
                    # emituj len keď sa niečo zmenilo alebo niečo hrá
                    if data is not None:
                        if data != last_payload:
                            self.media_updated.emit(data)
                            last_payload = data
                    else:
                        # nič nehrá – aby UI vedelo, môžeš emitnúť "prázdnu" info
                        empty = {
                            "source": "",
                            "source_app": "",
                            "title": "",
                            "artist": "",
                            "album": "",
                            "position": 0.0,
                            "duration": 0.0,
                            "is_playing": False,
                        }
                        if empty != last_payload:
                            self.media_updated.emit(empty)
                            last_payload = empty

                except Exception as e:
                    logger.exception("[MediaStatusProvider] Chyba v hlavnom loop-e")

                time.sleep(self._interval)
        finally:
            try:
                loop.close()
            except Exception:
                pass
            self._running = False

    def _run_linux(self) -> None:
        """Linux loop čítajúci playerctl metadata a emitujúci zmeny."""
        self._running = True
        last_payload: Optional[Dict[str, Any]] = None

        if not _PLAYERCTL_CMD:
            print("[MediaStatusProvider] playerctl/flatpak-spawn nenájdené – media monitoring na Linuxe vypínam.")
            self._running = False
            return

        while self._running:
            try:
                data = _fetch_now_playing_linux()
                if data is not None:
                    if data != last_payload:
                        self.media_updated.emit(data)
                        last_payload = data
                else:
                    empty = {
                        "source": "",
                        "source_app": "",
                        "title": "",
                        "artist": "",
                        "album": "",
                        "position": 0.0,
                        "duration": 0.0,
                        "is_playing": False,
                    }
                    if empty != last_payload:
                        self.media_updated.emit(empty)
                        last_payload = empty
            except Exception as e:
                print(f"[MediaStatusProvider] Chyba v Linux slučke: {e}")

            time.sleep(self._interval)
        self._running = False

    def stop(self) -> None:
        """Zastaví slučku monitorovania."""
        self._running = False

    def is_running(self) -> bool:
        """
        Používa sa v main.py v _check_media_status().
        True = vlákno beží, False = neběží.
        """
        return self.isRunning()

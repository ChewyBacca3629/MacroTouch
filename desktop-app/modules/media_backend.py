# core/media_backend.py
from __future__ import annotations

from typing import Protocol, Optional
from .platform_env import IS_WINDOWS, IS_LINUX
from .logging import get_logger


logger = get_logger(__name__)


class IMediaBackend(Protocol):
    """Rozhranie, ktoré bude používať zvyšok aplikácie – OS-nezávislé."""

    # HLASITOSŤ
    def set_volume(self, percent: int) -> None:
        """Nastaví absolútnu hlasitosť v % (0–100)."""
        ...

    def change_volume(self, delta_percent: int) -> None:
        """Pridá/uberie hlasitosť o dané % (+/-)."""
        ...

    def toggle_mute(self) -> None:
        """Prepne mute/unmute."""
        ...

    # MEDIA OVládanie
    def play_pause(self) -> None:
        """Prepne play/pause."""
        ...

    def next_track(self) -> None:
        """Preskočí na ďalšiu skladbu."""
        ...

    def prev_track(self) -> None:
        """Preskočí na predchádzajúcu skladbu."""
        ...

    # APLIKÁCIE / OKNÁ
    def open_app(self, path: str) -> None:
        """Spustí aplikáciu alebo cestu."""
        ...

    def send_keys(self, keys: str) -> None:
        """Odošle klávesové skratky."""
        ...

    def minimize_active_window(self) -> None:
        """Minimalizuje aktuálne okno."""
        ...

    def toggle_maximize_active_window(self) -> None:
        """Maximalizuje/obnoví aktívne okno."""
        ...

    # JAS
    def set_brightness(self, percent: int) -> None:
        """Nastaví jas v percentách."""
        ...

    def change_brightness(self, delta_percent: int) -> None:
        """Pridá/uberie jas o percentá."""
        ...


# ---------- Stub backend (fallback) ----------

class StubMediaBackend(IMediaBackend):
    """Použije sa tam, kde nemáme implementáciu (napr. prvá verzia na Linuxe)."""
    def _log(self, msg: str) -> None:
        """Zapíše stub správu do loggera."""
        logger.info("[StubMediaBackend] %s", msg)

    def set_volume(self, percent: int) -> None:
        """Stub: nastaví hlasitosť (len log)."""
        self._log(f"set_volume({percent})")

    def change_volume(self, delta_percent: int) -> None:
        """Stub: zmení hlasitosť (len log)."""
        self._log(f"change_volume({delta_percent})")

    def toggle_mute(self) -> None:
        """Stub: prepne mute (len log)."""
        self._log("toggle_mute()")

    def play_pause(self) -> None:
        """Stub: play/pause (len log)."""
        self._log("play_pause()")

    def next_track(self) -> None:
        """Stub: ďalšia skladba (len log)."""
        self._log("next_track()")

    def prev_track(self) -> None:
        """Stub: predchádzajúca skladba (len log)."""
        self._log("prev_track()")

    def open_app(self, path: str) -> None:
        """Stub: spustenie aplikácie (len log)."""
        self._log(f"open_app({path!r})")

    def send_keys(self, keys: str) -> None:
        """Stub: poslanie kláves (len log)."""
        self._log(f"send_keys({keys!r})")

    def minimize_active_window(self) -> None:
        """Stub: minimize okna (len log)."""
        self._log("minimize_active_window()")

    def toggle_maximize_active_window(self) -> None:
        """Stub: maximize/restore okna (len log)."""
        self._log("toggle_maximize_active_window()")

    def set_brightness(self, percent: int) -> None:
        """Stub: nastaví jas (len log)."""
        self._log(f"set_brightness({percent})")

    def change_brightness(self, delta_percent: int) -> None:
        """Stub: zmení jas (len log)."""
        self._log(f"change_brightness({delta_percent})")


# ---------- Výber backendu podľa OS ----------

def get_media_backend() -> IMediaBackend:
    """Return OS-specific media backend, falling back to stub on errors."""
    if IS_WINDOWS:
        try:
            from .windows_media_backend import WindowsMediaBackend
            return WindowsMediaBackend()
        except Exception as e:
            logger.exception("[MediaBackend] Windows backend zlyhal, používam Stub.")
            return StubMediaBackend()

    if IS_LINUX:
        try:
            from .linux_media_backend import LinuxMediaBackend
            return LinuxMediaBackend()
        except Exception as e:
            logger.exception("[MediaBackend] Linux backend zlyhal, používam Stub.")
            return StubMediaBackend()

    # iné OS → stub
    return StubMediaBackend()

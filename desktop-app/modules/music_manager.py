# core/music_manager.py
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .music_state import MusicState


class MusicManager(QObject):
    """
    Jediný zdroj pravdy pre hudobný stav.

    - desktop zbiera dáta (MediaStatusProvider, Spotify, system media session),
    - MusicManager z toho spraví MusicState,
    - signalom state_changed to posiela:
        * MediaWidget (náhľad v appke)
        * hlavné okno (ak potrebuje ESP packet atď.).
    """

    state_changed: pyqtSignal = pyqtSignal(MusicState)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        """Initialize with empty MusicState; emit via state_changed."""
        super().__init__(parent)
        self._state = MusicState()

    @property
    def state(self) -> MusicState:
        """Aktuálny MusicState."""
        return self._state

    def _emit_if_changed(self, new_state: MusicState) -> None:
        """Emit signal only when state differs."""
        if new_state == self._state:
            return
        self._state = new_state
        self.state_changed.emit(self._state)

    def update_metadata(
        self,
        *,
        source: str,
        title: str,
        artist: str,
        pos_s: float | int,
        dur_s: float | int,
        is_playing: bool,
        volume_pct: int | None = None,
    ) -> None:
        """
        Hlavný vstup: volaj z _on_media_status v main.py.
        """

        # normalizácia
        src = (source or "DEFAULT").upper()
        ttl = (title or "").strip()
        art = (artist or "").strip()
        pos = max(0, int(pos_s or 0))
        dur = max(1, int(dur_s or 1))
        is_play = bool(is_playing)

        if volume_pct is None:
            vol = self._state.volume_pct  # necháme poslednú známu hodnotu
        else:
            v = int(volume_pct)
            vol = v if 0 <= v <= 100 else -1

        new_state = MusicState(
            source=src,
            title=ttl,
            artist=art,
            pos_s=pos,
            dur_s=dur,
            is_playing=is_play,
            volume_pct=vol,
        )
        self._emit_if_changed(new_state)

    def update_position_only(self, pos_s: float | int) -> None:
        """
        Ak by si niekedy chcel rýchlejšie updatovať len progress bez zmeny ostatných polí.
        Zatiaľ to nepotrebuješ, ale nech je pripravené.
        """
        pos = max(0, int(pos_s or 0))
        if pos == self._state.pos_s:
            return
        new_state = MusicState(
            source=self._state.source,
            title=self._state.title,
            artist=self._state.artist,
            pos_s=pos,
            dur_s=self._state.dur_s,
            is_playing=self._state.is_playing,
            volume_pct=self._state.volume_pct,
        )
        self._emit_if_changed(new_state)

    def set_volume(self, volume_pct: int) -> None:
        """Update only volume field in MusicState."""
        v = int(volume_pct)
        if not (0 <= v <= 100):
            v = -1
        if v == self._state.volume_pct:
            return
        new_state = MusicState(
            source=self._state.source,
            title=self._state.title,
            artist=self._state.artist,
            pos_s=self._state.pos_s,
            dur_s=self._state.dur_s,
            is_playing=self._state.is_playing,
            volume_pct=v,
        )
        self._emit_if_changed(new_state)

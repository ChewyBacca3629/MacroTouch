# core/music_state.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(eq=True)
class MusicState:
    """
    Centrálna reprezentácia media stavu pre MacroTouch.

    - source: "SPOTIFY", "YOUTUBE", "VLC", "DEFAULT" ...
    - title: názov skladby / videa
    - artist: interpret alebo fallback (napr. názov appky)
    - pos_s: aktuálna pozícia v sekundách
    - dur_s: dĺžka v sekundách (min 1)
    - is_playing: prehráva / pauza
    - volume_pct: 0–100 alebo -1 ak nevieme
    """
    source: str = "DEFAULT"
    title: str = "—"
    artist: str = ""
    pos_s: int = 0
    dur_s: int = 1
    is_playing: bool = False
    volume_pct: int = -1

    @property
    def display_source_label(self) -> str:
        """Human label for source code."""
        s = (self.source or "").upper()
        if "SPOTIFY" in s:
            return "Spotify"
        if "YOUTUBE" in s or "BROWSER" in s:
            return "YouTube"
        if "VLC" in s:
            return "VLC Player"
        return "Media"

    @property
    def display_track(self) -> str:
        """
        To isté ako na ESP:
        - ak máme title aj artist -> "Title – Artist"
        - ak len title -> title
        - inak "—"
        """
        title = (self.title or "").strip()
        artist = (self.artist or "").strip()
        if title and artist:
            return f"{title} – {artist}"
        if title:
            return title
        return "—"

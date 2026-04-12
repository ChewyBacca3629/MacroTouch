# core/media_widget.py
from __future__ import annotations

from typing import Optional
from pathlib import Path

import time

from PyQt6.QtCore import Qt, pyqtSignal, QRect, QPointF, QTimer
from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetrics, QFontDatabase, QPen

from .music_state import MusicState


class MediaWidget(QWidget):
    """
    Vizualizácia media profilu 1:1 s ESP:
      - Spotify-inšpirované pozadie
      - header s logom + "Now Playing"
      - názov tracku (max 2 riadky) + artist
      - progress bar + časy
      - tri ikony ⏮ ⏯ ⏭ uprostred dole
    """

    prevClicked = pyqtSignal()
    playPauseClicked = pyqtSignal()
    nextClicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Initialize media widget state and clickable regions."""
        super().__init__(parent)
        self.setObjectName("MediaWidget")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(456, 296)
        self._ensure_inter_loaded()

        # centrálne uložený stav (z MusicManagera)
        self._state = MusicState()

        # "rozbalený" stav pre kreslenie
        self._source = "GENERIC"     # SPOTIFY / YOUTUBE / VLC / GENERIC
        self._track = "No track"
        self._is_playing = False
        self._pos_s = 0.0
        self._dur_s = 1.0
        self._volume_pct = -1

        # klikacie oblasti
        self._prevRect: Optional[QRect] = None
        self._playRect: Optional[QRect] = None
        self._nextRect: Optional[QRect] = None

        self._marquee_active = False
        self._marquee_text = ""
        self._marquee_width = 0
        self._marquee_offset = 0
        self._marquee_gap = 30
        self._marquee_last_ts = time.monotonic()
        self._marquee_timer = QTimer(self)
        self._marquee_timer.setInterval(40)
        self._marquee_timer.timeout.connect(self._tick_marquee)

    # ---------- API používané z main.py ----------

    def apply_state(self, state: MusicState) -> None:
        """Update internal state from MusicState and trigger repaint."""
        # ak je to úplne rovnaký stav, nerieš
        if (
            state.source == self._state.source
            and state.display_track == self._state.display_track
            and int(state.pos_s or 0) == int(self._state.pos_s or 0)
            and int(state.dur_s or 0) == int(self._state.dur_s or 0)
            and (state.volume_pct or -1) == (self._state.volume_pct or -1)
            and bool(state.is_playing) == bool(self._state.is_playing)
        ):
            return

        self._state = state

        src = (state.source or "").upper() or "GENERIC"
        self._source = src
        self._track = state.display_track or "No track"
        self._is_playing = bool(state.is_playing)
        self._pos_s = max(0.0, float(state.pos_s or 0.0))
        self._dur_s = max(1.0, float(state.dur_s or 1.0))

        vol = state.volume_pct
        if vol is None or vol < 0:
            self._volume_pct = -1
        else:
            self._volume_pct = max(-1, min(100, int(vol)))

        self.update()


    # ---------- farby ako na ESP ----------

    def _bg_color(self) -> QColor:
        """Background color based on source."""
        src = self._source
        if src == "SPOTIFY":
            return QColor(30, 215, 96)
        if src == "YOUTUBE":
            return QColor(127, 29, 29)
        if src == "VLC":
            return QColor(120, 53, 15)
        return QColor(17, 24, 39)

    @staticmethod
    def _ensure_inter_loaded() -> None:
        """Load bundled Inter font for preview if available."""
        if getattr(MediaWidget, "_inter_loaded", False):
            return
        font_path = Path(__file__).resolve().parents[1] / "fonts" / "ttf" / "InterVariable.ttf"
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
        MediaWidget._inter_loaded = True

    def _accent_color(self) -> QColor:
        """Accent color based on source."""
        src = self._source
        if src == "SPOTIFY":
            return QColor(34, 197, 94)
        if src == "YOUTUBE":
            return QColor(248, 113, 113)
        if src == "VLC":
            return QColor(252, 211, 77)
        return QColor(239, 239, 239)

    def _inactive_color(self) -> QColor:
        """Inactive accent color based on source."""
        src = self._source
        if src == "SPOTIFY":
            return QColor(20, 120, 60)
        if src == "YOUTUBE":
            return QColor(160, 80, 80)
        if src == "VLC":
            return QColor(200, 160, 60)
        return QColor(60, 70, 80)

    def _header_color(self) -> QColor:
        """Slightly darker header than background."""
        return self._bg_color().darker(115)

    # ---------- kreslenie ----------

    def paintEvent(self, event) -> None:  # type: ignore[override]
        """Custom paint: background, texts, progress bar, and control icons."""
        W = self.width()
        H = self.height()

        # škálovanie z "virtuálneho" 480x320
        def sx(px: int) -> int:
            """Scale X from virtual 480 width to widget width."""
            return int(px * W / 480)

        def sy(py: int) -> int:
            """Scale Y from virtual 320 height to widget height."""
            return int(py * H / 320)

        marginX = sx(20)
        headerH = sy(36)
        infoTop = headerH + sy(16)
        btnSize = min(sx(54), sy(54))
        controlsY = H - btnSize - sy(10)
        barH = sy(8)
        barY = controlsY - sy(22)
        barX = marginX
        barW = W - 2 * marginX

        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
        )

        playing = bool(self._is_playing)
        bgScreen = self._bg_color()
        if not playing:
            bgScreen = bgScreen.darker(110)
        accent = self._accent_color() if playing else self._accent_color().darker(125)
        headerBg = self._header_color()
        headerText = QColor(240, 255, 245)
        subText = QColor(210, 230, 220)
        iconCol = QColor(255, 255, 255) if playing else QColor(200, 210, 220)

        # pozadie
        painter.fillRect(self.rect(), bgScreen)

        # header bar
        painter.fillRect(0, 0, W, headerH, headerBg)
        painter.setFont(QFont("Inter", sy(12), QFont.Weight.Medium))
        painter.setPen(headerText)
        headerRect = QRect(marginX, 0, W - marginX * 2, headerH)
        painter.drawText(
            headerRect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "Now Playing",
        )

        # --- track + artist ---
        title, artist = self._split_title_artist(self._track)
        if not title:
            title = "—"

        title_font = QFont("Inter", sy(20), QFont.Weight.DemiBold)
        painter.setFont(title_font)
        title_fm = QFontMetrics(painter.font())
        maxTrackW = W - 2 * marginX
        line1, line2, overflow = self._wrap_two_lines(title, title_fm, maxTrackW)

        use_marquee = overflow
        if use_marquee:
            self._set_marquee(title, title_fm, maxTrackW)
        else:
            self._set_marquee("")

        trackY = infoTop
        line_h = title_fm.height()
        line_ascent = title_fm.ascent()
        painter.setPen(QColor(255, 255, 255))
        if not use_marquee:
            if line1:
                painter.drawText(marginX, trackY + line_ascent, line1)
            if line2:
                painter.drawText(marginX, trackY + line_h + sy(2) + line_ascent, line2)
        else:
            painter.save()
            painter.setClipRect(marginX, trackY, maxTrackW, line_h + sy(2))
            x1 = marginX - self._marquee_offset
            painter.drawText(x1, trackY + line_ascent, title)
            painter.drawText(x1 + self._marquee_width + self._marquee_gap, trackY + line_ascent, title)
            painter.restore()

        if artist:
            artistY = trackY + line_h * (2 if line2 else 1) + sy(10)
            painter.setFont(QFont("Inter", sy(12), QFont.Weight.Normal))
            painter.setPen(subText)
            fm_artist = QFontMetrics(painter.font())
            artistText = self._ellipsize(artist, fm_artist, maxTrackW)
            if artistY + fm_artist.height() < barY - sy(6):
                painter.drawText(marginX, artistY + fm_artist.ascent(), artistText)

        # --- progress bar + časy ---
        barBg = self._inactive_color()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(barBg)
        radius = barH  # pill
        painter.drawRoundedRect(barX, barY, barW, barH, radius, radius)

        ratio = max(0.0, min(1.0, float(self._pos_s) / float(self._dur_s or 1)))
        fillW = int(barW * ratio)
        painter.setBrush(accent)
        painter.drawRoundedRect(barX, barY, fillW, barH, radius, radius)

        # časy
        timeY = barY + barH + sy(14)
        painter.setFont(QFont("Inter", sy(10), QFont.Weight.Medium))
        painter.setPen(subText)

        curMin = int(self._pos_s) // 60
        curSec = int(self._pos_s) % 60
        totMin = int(self._dur_s) // 60
        totSec = int(self._dur_s) % 60

        curTxt = f"{curMin}:{curSec:02d}"
        totTxt = f"{totMin}:{totSec:02d}"

        painter.drawText(barX, timeY, curTxt)
        totW = QFontMetrics(painter.font()).horizontalAdvance(totTxt)
        painter.drawText(barX + barW - totW, timeY, totTxt)

        # --- ovládacie ikony ---
        cx_center = W // 2
        prevX = cx_center - btnSize - sx(40)
        playX = cx_center - (btnSize // 2)
        nextX = cx_center + sx(40)

        self._prevRect = QRect(prevX, controlsY, btnSize, btnSize)
        self._playRect = QRect(playX, controlsY, btnSize, btnSize)
        self._nextRect = QRect(nextX, controlsY, btnSize, btnSize)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(iconCol)

        self._draw_prev_icon(painter, self._prevRect)
        self._draw_playpause_icon(painter, self._playRect, self._is_playing)
        self._draw_next_icon(painter, self._nextRect)

        # frame
        border_col = QColor(255, 255, 255, 70)
        border_w = max(2, sx(3))
        painter.setPen(QPen(border_col, border_w))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        radius = sy(16)
        frame_rect = self.rect().adjusted(1, 1, -1, -1)
        painter.drawRoundedRect(frame_rect, radius, radius)

        painter.end()

    # ---------- helper kreslenie ----------

    @staticmethod
    def _ellipsize(text: str, fm: QFontMetrics, max_w: int) -> str:
        """Trim text with ellipsis to fit width."""
        if fm.horizontalAdvance(text) <= max_w:
            return text
        base = text
        suffix = "..."
        while base and fm.horizontalAdvance(base + suffix) > max_w:
            base = base[:-1]
        return base + suffix

    @staticmethod
    def _split_title_artist(track: str) -> tuple[str, str]:
        title = (track or "").strip()
        artist = ""
        for sep in (" - ", " – "):
            if sep in title:
                parts = title.split(sep, 1)
                title = parts[0].strip()
                artist = parts[1].strip()
                break
        return title, artist

    @staticmethod
    def _wrap_two_lines(text: str, fm: QFontMetrics, max_w: int) -> tuple[str, str, bool]:
        line1 = ""
        line2 = ""
        words = [w for w in (text or "").split(" ") if w]
        line = 0
        for word in words:
            if fm.horizontalAdvance(word) > max_w:
                return "", "", True
            target = line1 if line == 0 else line2
            candidate = f"{target} {word}".strip()
            if fm.horizontalAdvance(candidate) <= max_w:
                if line == 0:
                    line1 = candidate
                else:
                    line2 = candidate
            else:
                if line == 0:
                    line = 1
                    line2 = word
                else:
                    return line1, line2, True
        return line1, line2, False

    def _set_marquee(self, text: str, fm: QFontMetrics | None = None, max_w: int = 0) -> None:
        if not text:
            self._marquee_active = False
            self._marquee_text = ""
            self._marquee_offset = 0
            if self._marquee_timer.isActive():
                self._marquee_timer.stop()
            return
        if text != self._marquee_text and fm is not None:
            self._marquee_text = text
            self._marquee_width = fm.horizontalAdvance(text)
            self._marquee_offset = 0
            self._marquee_last_ts = time.monotonic()
        self._marquee_active = True
        if not self._marquee_timer.isActive():
            self._marquee_timer.start()

    def _tick_marquee(self) -> None:
        if not self._marquee_active or self._marquee_width <= 0:
            return
        now = time.monotonic()
        delta = now - self._marquee_last_ts
        if delta <= 0:
            return
        speed = 30.0  # px/s
        step = int(speed * delta)
        if step > 0:
            cycle = self._marquee_width + self._marquee_gap
            self._marquee_offset = (self._marquee_offset + step) % max(1, cycle)
            self._marquee_last_ts = now
            self.update()

    def _draw_spotify_glyph(self, p: QPainter, cx: int, cy: int) -> None:
        """Render Spotify circle+wave glyph."""
        circleBg = QColor(0, 0, 0)
        waveColor = QColor(30, 215, 96)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(circleBg)
        p.drawEllipse(cx - 16, cy - 16, 32, 32)

        p.setPen(waveColor)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for r in (11, 9, 7):
            rect = QRect(cx - r, cy - r, 2 * r, 2 * r)
            p.drawArc(rect, int(210 * 16), int(120 * 16))

    def _draw_youtube_logo(self, p: QPainter, x: int, y: int, w: int, h: int) -> None:
        """Render simple YouTube play logo."""
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(239, 68, 68))
        p.drawRoundedRect(x + 4, y + 5, w - 8, h - 10, 4, 4)

        p.setBrush(QColor(255, 255, 255))
        tx = x + 4 + 5
        ty = y + 5 + 3
        points = [
            QPointF(tx, ty),
            QPointF(tx, ty + (h - 10) - 6),
            QPointF(tx + 9, ty + (h - 10) / 2),
        ]
        p.drawPolygon(points)

    def _draw_generic_logo(self, p: QPainter, x: int, y: int) -> None:
        """Render generic circle/triangle logo."""
        cx = x + 14
        cy = y + 14
        p.setPen(QColor(255, 255, 255))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - 11, cy - 11, 22, 22)
        p.setBrush(QColor(255, 255, 255))
        tri = [
            QPointF(cx - 3, cy - 6),
            QPointF(cx - 3, cy + 6),
            QPointF(cx + 6, cy),
        ]
        p.drawPolygon(tri)

    def _draw_prev_icon(self, p: QPainter, rect: QRect) -> None:
        """Draw previous control icon."""
        cx = rect.center().x()
        cy = rect.center().y()
        scale = rect.height() / 44.0

        barW = int(4 * scale)
        barH = int(18 * scale)
        p.drawRect(cx - int(12 * scale), cy - barH // 2, barW, barH)

        tri1 = [
            QPointF(cx - int(6 * scale), cy),
            QPointF(cx + int(2 * scale), cy - int(9 * scale)),
            QPointF(cx + int(2 * scale), cy + int(9 * scale)),
        ]
        tri2 = [
            QPointF(cx + int(4 * scale), cy),
            QPointF(cx + int(12 * scale), cy - int(9 * scale)),
            QPointF(cx + int(12 * scale), cy + int(9 * scale)),
        ]
        p.drawPolygon(tri1)
        p.drawPolygon(tri2)

    def _draw_playpause_icon(self, p: QPainter, rect: QRect, playing: bool) -> None:
        """Draw play or pause control icon."""
        cx = rect.center().x()
        cy = rect.center().y()
        scale = rect.height() / 44.0

        if playing:
            barW = int(6 * scale)
            barH = int(22 * scale)
            p.drawRect(cx - int(8 * scale), cy - barH // 2, barW, barH)
            p.drawRect(cx + int(2 * scale), cy - barH // 2, barW, barH)
        else:
            h = int(22 * scale)
            tri = [
                QPointF(cx - int(6 * scale), cy - h / 2),
                QPointF(cx - int(6 * scale), cy + h / 2),
                QPointF(cx + int(10 * scale), cy),
            ]
            p.drawPolygon(tri)

    def _draw_next_icon(self, p: QPainter, rect: QRect) -> None:
        """Draw next control icon."""
        cx = rect.center().x()
        cy = rect.center().y()
        scale = rect.height() / 44.0

        barW = int(4 * scale)
        barH = int(18 * scale)

        tri1 = [
            QPointF(cx - int(12 * scale), cy - int(9 * scale)),
            QPointF(cx - int(12 * scale), cy + int(9 * scale)),
            QPointF(cx - int(4 * scale), cy),
        ]
        tri2 = [
            QPointF(cx - int(2 * scale), cy - int(9 * scale)),
            QPointF(cx - int(2 * scale), cy + int(9 * scale)),
            QPointF(cx + int(6 * scale), cy),
        ]
        p.drawPolygon(tri1)
        p.drawPolygon(tri2)
        p.drawRect(cx + int(8 * scale), cy - barH // 2, barW, barH)

    # ---------- kliknutia ----------

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        """Emit click signals when icons are pressed; otherwise default."""
        pos = event.position().toPoint()
        if self._prevRect and self._prevRect.contains(pos):
            self.prevClicked.emit()
        elif self._playRect and self._playRect.contains(pos):
            self.playPauseClicked.emit()
        elif self._nextRect and self._nextRect.contains(pos):
            self.nextClicked.emit()
        else:
            super().mousePressEvent(event)

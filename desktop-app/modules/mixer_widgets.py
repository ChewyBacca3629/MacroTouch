from __future__ import annotations

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QSlider, QWidget


class MixerWidget(QWidget):
    """Placeholder widget for the mixer UI."""

    pass


class MixerSlider(QSlider):
    def __init__(self, accent: QColor | None = None, parent: QWidget | None = None):
        super().__init__(Qt.Orientation.Vertical, parent)
        self._accent = QColor(accent) if accent is not None else QColor(71, 214, 127)
        self._active = True
        self.setRange(0, 100)
        self.setSingleStep(1)
        self.setPageStep(5)
        self.setTracking(True)

    def setAccentColor(self, color: QColor) -> None:
        self._accent = QColor(color)
        self.update()

    def setActive(self, active: bool) -> None:
        self._active = bool(active)
        self.update()

    def _track_rect(self) -> QRectF:
        rect = self.rect()
        track_w = min(18.0, max(10.0, rect.width() - 8.0))
        track_h = max(40.0, rect.height() - 16.0)
        x = (rect.width() - track_w) / 2.0
        y = 8.0
        return QRectF(x, y, track_w, track_h)

    def _value_from_pos(self, y: float) -> int:
        track = self._track_rect()
        if track.height() <= 0:
            return self.value()
        y = max(track.top(), min(track.bottom(), y))
        ratio = (track.bottom() - y) / track.height()
        return int(round(ratio * 100))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._value_from_pos(event.position().y()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._value_from_pos(event.position().y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        track = self._track_rect()
        track_border = QColor(50, 59, 74)
        track_grad = QLinearGradient(track.topLeft(), track.bottomLeft())
        track_grad.setColorAt(0.0, QColor(20, 26, 40))
        track_grad.setColorAt(1.0, QColor(12, 16, 26))
        painter.setBrush(track_grad)
        painter.setPen(QPen(track_border, 1.0))
        painter.drawRoundedRect(track, 8.0, 8.0)

        dot_col = QColor(100, 116, 139)
        dot_x = track.center().x()
        y = track.top() + 10.0
        while y < track.bottom() - 10.0:
            painter.setBrush(dot_col)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(dot_x - 2.0, y - 2.0, 4.0, 4.0))
            y += 12.0

        if self._active:
            value = max(0, min(100, int(self.value())))
            fill_h = (track.height() * value) / 100.0
            if fill_h > 2.0:
                fill_y = track.bottom() - fill_h
                fill_rect = QRectF(track.left() + 2.0, fill_y + 2.0, track.width() - 4.0, fill_h - 2.0)
                painter.save()
                painter.setOpacity(0.18)
                glow = fill_rect.adjusted(-2.0, -2.0, 2.0, 2.0)
                painter.setBrush(self._accent)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(glow, 9.0, 9.0)
                painter.restore()

                fill_grad = QLinearGradient(fill_rect.topLeft(), fill_rect.bottomLeft())
                fill_grad.setColorAt(0.0, self._accent.lighter(135))
                fill_grad.setColorAt(1.0, self._accent.darker(170))
                painter.setBrush(fill_grad)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(fill_rect, 8.0, 8.0)
                painter.setPen(QPen(self._accent.lighter(150), 1.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(fill_rect, 8.0, 8.0)
        else:
            dash = QPen(QColor(96, 108, 128), 1.0, Qt.PenStyle.DashLine)
            dash.setDashPattern([4.0, 4.0])
            painter.setPen(dash)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inner = track.adjusted(6.0, 6.0, -6.0, -6.0)
            painter.drawRoundedRect(inner, 6.0, 6.0)

        if self._active and self.isEnabled():
            handle_y = track.bottom() - (track.height() * self.value() / 100.0)
            handle_size = 14.0
            handle_rect = QRectF(
                track.center().x() - handle_size / 2.0,
                handle_y - handle_size / 2.0,
                handle_size,
                handle_size,
            )
            painter.setBrush(QColor(248, 250, 252))
            painter.setPen(QPen(self._accent.lighter(150), 2.0))
            painter.drawEllipse(handle_rect)

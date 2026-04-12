# core/system_monitor_widget.py
from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QSizePolicy
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve


class StatCard(QFrame):
    """
    Jedna kartička v system monitore (CPU / GPU / RAM / FPS)
    s interným usage barom a dvomi textami: primary + secondary.
    """
    def __init__(self, title: str, parent: QWidget | None = None):
        """Create a stat card with title, primary/secondary labels and bar."""
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumSize(160, 130)
        self.setMaximumHeight(160)

        # Title
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("StatTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # PRIMARY value (percentá / hlavná hodnota)
        self.primary_label = QLabel("N/A", self)
        self.primary_label.setObjectName("StatPrimary")
        self.primary_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # SECONDARY value (GHz, GB, cores…)
        self.secondary_label = QLabel("", self)
        self.secondary_label.setObjectName("StatSecondary")
        self.secondary_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Usage bar – kontajner + výplň
        self.bar_container = QFrame(self)
        self.bar_container.setObjectName("UsageBarContainer")

        # parent na bar_container (nie na root)
        self.bar_fill = QFrame(self.bar_container)
        self.bar_fill.setObjectName("UsageBarFill")
        self.bar_fill.setMinimumWidth(0)
        self.bar_fill.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        bar_layout = QHBoxLayout(self.bar_container)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(0)
        bar_layout.addWidget(self.bar_fill)

        # animácia šírky bar_fill pri update percent
        self._bar_anim = QPropertyAnimation(self.bar_fill, b"maximumWidth", self)
        self._bar_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._bar_anim.setDuration(140)
        self._target_fill_w = 0
        self._bar_anim.finished.connect(self._on_bar_anim_finished)

        # Root layout karty
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(self.title_label)
        layout.addWidget(self.bar_container)
        layout.addWidget(self.primary_label)
        layout.addWidget(self.secondary_label)

        self._percent: float = 0.0

    def _update_bar_width(self) -> None:
        """Prepočíta šírku výplne podľa percent – použité z set_values aj resizeEvent."""
        total = self.bar_container.width()
        if total <= 0:
            # layout ešte nemusí byť dopočítaný – necháme to na neskorší resize
            return
        fill_w = int(total * (self._percent / 100.0))
        # animovať prechod do novej šírky
        try:
            self._bar_anim.stop()
            self._target_fill_w = fill_w
            self._bar_anim.setStartValue(self.bar_fill.width())
            self._bar_anim.setEndValue(fill_w)
            self._bar_anim.start()
        except Exception:
            self.bar_fill.setFixedWidth(fill_w)

    def _update_usage_level_property(self) -> None:
        """
        Nastaví Qt property podľa zaťaženia:
          low / medium / high
        aby sa dala použiť v QSS.
        """
        p = self._percent
        if p < 40:
            level = "low"
        elif p < 80:
            level = "medium"
        else:
            level = "high"

        self.bar_fill.setProperty("usageLevel", level)
        # refresh štýlu po zmene property
        self.bar_fill.style().unpolish(self.bar_fill)
        self.bar_fill.style().polish(self.bar_fill)
        self.bar_fill.update()

    def set_values(self, percent: float, primary: str, secondary: str) -> None:
        """
        Aktualizuje texty a usage bar.
        percent: 0–100
        """
        # texty
        self.primary_label.setText(primary)
        self.secondary_label.setText(secondary)

        # percentá clamp
        self._percent = max(0.0, min(100.0, float(percent) if percent is not None else 0.0))

        # aktualizuj property pre farbu
        self._update_usage_level_property()

        # nastavenie šírky podľa aktuálnej šírky kontajnera
        self._update_bar_width()

    def resizeEvent(self, ev):
        """Pri resize prepočítať šírku bar_fill podľa uloženého percenta."""
        super().resizeEvent(ev)
        self._update_bar_width()

    def _on_bar_anim_finished(self) -> None:
        """Po animácii zafixuje bar na cieľovú šírku, aby sa layout nezbláznil."""
        try:
            self.bar_fill.setMinimumWidth(self._target_fill_w)
            self.bar_fill.setMaximumWidth(self._target_fill_w)
        except Exception:
            pass

class SystemMonitorWidget(QWidget):
    """
    Hlavný panel SYSTEM MONITOR – CPU, GPU, RAM, DISK, NET, FPS.
    """
    def __init__(self, parent: QWidget | None = None):
        """Assemble stat cards and layout for system monitor panel."""
        super().__init__(parent)
        self.setObjectName("SystemMonitorRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(456, 296)

        # nadpis
        self.title_label = QLabel("SYSTEM MONITOR", self)
        self.title_label.setObjectName("SystemMonitorTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)

        # kartičky
        self.cpu_card = StatCard("CPU", self)
        self.gpu_card = StatCard("GPU", self)
        self.ram_card = StatCard("RAM", self)
        self.disk_card = StatCard("DISK", self)
        self.net_card = StatCard("NET", self)
        self.fps_card = StatCard("FPS", self)

        # horný riadok: CPU / GPU / RAM
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(18)
        row1.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        row1.addWidget(self.cpu_card)
        row1.addWidget(self.gpu_card)
        row1.addWidget(self.ram_card)

        # spodný riadok: DISK / NET / FPS
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(18)
        row2.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        row2.addWidget(self.disk_card)
        row2.addWidget(self.net_card)
        row2.addWidget(self.fps_card)

        # content layout (centered block)
        content = QWidget(self)
        content.setObjectName("SystemMonitorContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        content_layout.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignHCenter)
        content_layout.addLayout(row1)
        content_layout.addLayout(row2)

        # root layout
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(0)
        root.addStretch(1)
        root.addWidget(content, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        root.addStretch(1)

    # voliteľné helper metódy, keby si ich niekde chcel používať
    def set_cpu(self, percent: float, primary: str, secondary: str) -> None:
        """Update CPU card values."""
        self.cpu_card.set_values(percent, primary, secondary)

    def set_ram(self, percent: float, primary: str, secondary: str) -> None:
        """Update RAM card values."""
        self.ram_card.set_values(percent, primary, secondary)

    def set_gpu(self, percent: float, primary: str, secondary: str) -> None:
        """GPU karta – bar podľa percent, texty ako reťazce."""
        self.gpu_card.set_values(percent, primary, secondary)

    def set_fps(self, primary: str, secondary: str = "") -> None:
        # FPS bar nemá zmysel, takže percent=0
        """Update FPS card text."""
        self.fps_card.set_values(0.0, primary, secondary)

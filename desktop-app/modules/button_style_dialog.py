from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFontDatabase
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QColorDialog,
    QWidget,
)


class ButtonStyleDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        initial_style: dict[str, Any],
        defaults: dict[str, Any],
        label_text: str,
        normalize_fn,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Button Style")
        self._normalize = normalize_fn
        self._defaults = defaults
        self._label_text = label_text or "Button"

        self._style = {
            "bg_color": self._normalize(initial_style.get("bg_color", defaults["bg_color"]), defaults["bg_color"]),
            "fg_color": self._normalize(initial_style.get("fg_color", defaults["fg_color"]), defaults["fg_color"]),
            "text_size": float(initial_style.get("text_size", defaults["text_size"])),
            "font": str(initial_style.get("font", defaults.get("font", "")) or ""),
        }

        root = QVBoxLayout(self)
        grid = QGridLayout()

        grid.addWidget(QLabel("Text color"), 0, 0)
        self.lineTextColor = QLineEdit(self._style["fg_color"])
        self.btnTextPick = QPushButton("Pick")
        grid.addWidget(self.lineTextColor, 0, 1)
        grid.addWidget(self.btnTextPick, 0, 2)

        grid.addWidget(QLabel("Button bg"), 1, 0)
        self.lineBgColor = QLineEdit(self._style["bg_color"])
        self.btnBgPick = QPushButton("Pick")
        grid.addWidget(self.lineBgColor, 1, 1)
        grid.addWidget(self.btnBgPick, 1, 2)

        grid.addWidget(QLabel("Text size"), 2, 0)
        self.spinTextSize = QDoubleSpinBox()
        self.spinTextSize.setRange(0.6, 2.5)
        self.spinTextSize.setSingleStep(0.1)
        self.spinTextSize.setDecimals(1)
        self.spinTextSize.setValue(float(self._style["text_size"]))
        grid.addWidget(self.spinTextSize, 2, 1)

        grid.addWidget(QLabel("Font"), 3, 0)
        self.comboFont = QComboBox()
        self.comboFont.addItem("Default")
        for family in QFontDatabase.families():
            self.comboFont.addItem(family)
        initial_font = self._style.get("font", "")
        if initial_font:
            idx = self.comboFont.findText(initial_font, Qt.MatchFlag.MatchExactly)
            if idx >= 0:
                self.comboFont.setCurrentIndex(idx)
            else:
                self.comboFont.setCurrentIndex(0)
        else:
            self.comboFont.setCurrentIndex(0)
        grid.addWidget(self.comboFont, 3, 1)

        root.addLayout(grid)

        self.framePreview = QFrame()
        self.framePreview.setObjectName("frameBtnPreviewDialog")
        self.framePreview.setMinimumSize(180, 110)
        self.framePreview.setMaximumSize(260, 160)
        previewLay = QVBoxLayout(self.framePreview)
        self.lblPreview = QLabel(self._label_text)
        self.lblPreview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        previewLay.addWidget(self.lblPreview)
        root.addWidget(self.framePreview)

        btnRow = QHBoxLayout()
        self.btnReset = QPushButton("Reset")
        btnRow.addWidget(self.btnReset)
        btnRow.addStretch(1)
        self.btnBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btnRow.addWidget(self.btnBox)
        root.addLayout(btnRow)

        self.btnTextPick.clicked.connect(lambda: self._pick_color(self.lineTextColor))
        self.btnBgPick.clicked.connect(lambda: self._pick_color(self.lineBgColor))
        self.btnReset.clicked.connect(self._reset_defaults)
        self.btnBox.accepted.connect(self.accept)
        self.btnBox.rejected.connect(self.reject)

        self.lineTextColor.editingFinished.connect(self._update_preview)
        self.lineBgColor.editingFinished.connect(self._update_preview)
        self.spinTextSize.valueChanged.connect(self._update_preview)
        self.comboFont.currentTextChanged.connect(self._update_preview)

        self.setStyleSheet(
            "QDialog { background-color: #0b1220; }"
            "QLabel { color: #e2e8f0; font-size: 11px; }"
            "QLineEdit, QDoubleSpinBox, QComboBox {"
            "  background-color: #1f2937;"
            "  color: #e5e7eb;"
            "  border: 1px solid #374151;"
            "  border-radius: 8px;"
            "  padding: 6px;"
            "}"
            "QPushButton {"
            "  background-color: #2b313c;"
            "  color: #e5e7eb;"
            "  border: 1px solid #3a3d45;"
            "  border-radius: 10px;"
            "  padding: 6px 10px;"
            "}"
            "QPushButton:hover { background-color: #394150; }"
            "QFrame#frameBtnPreviewDialog { border: 1px solid #2f3845; }"
        )

        self._update_preview()

    def _pick_color(self, line_edit: QLineEdit) -> None:
        current = self._normalize(line_edit.text(), "#000000")
        color = QColor(current)
        picked = QColorDialog.getColor(color, self, "Pick color")
        if not picked.isValid():
            return
        line_edit.setText(picked.name().upper())
        self._update_preview()

    def _reset_defaults(self) -> None:
        self.lineTextColor.setText(str(self._defaults["fg_color"]))
        self.lineBgColor.setText(str(self._defaults["bg_color"]))
        self.spinTextSize.setValue(float(self._defaults["text_size"]))
        default_font = str(self._defaults.get("font", "") or "")
        if default_font:
            idx = self.comboFont.findText(default_font, Qt.MatchFlag.MatchExactly)
            self.comboFont.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            self.comboFont.setCurrentIndex(0)
        self._update_preview()

    def _update_preview(self) -> None:
        fg = self._normalize(self.lineTextColor.text(), self._defaults["fg_color"])
        bg = self._normalize(self.lineBgColor.text(), self._defaults["bg_color"])
        size = float(self.spinTextSize.value())
        size = max(0.6, min(2.5, size))
        font_name = (self.comboFont.currentText() or "").strip()
        if font_name == "Default":
            font_name = ""

        self._style = {"bg_color": bg, "fg_color": fg, "text_size": size, "font": font_name}
        self.lblPreview.setText(self._label_text)
        try:
            font = self.lblPreview.font()
            if font_name:
                font.setFamily(font_name)
            else:
                font.setFamily(self.font().family())
            font.setPointSizeF(max(6.0, 11.0 * size))
            self.lblPreview.setFont(font)
        except Exception:
            pass
        self.lblPreview.setStyleSheet(f"color: {fg};")
        self.framePreview.setStyleSheet(
            "QFrame#frameBtnPreviewDialog {"
            f" background-color: {bg};"
            " border-radius: 12px;"
            " border: 1px solid #3a3d45;"
            "}"
        )

    def get_style(self) -> dict[str, Any]:
        return dict(self._style)

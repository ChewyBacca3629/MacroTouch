from pathlib import Path
from typing import Literal

from PyQt6 import uic
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QComboBox,
    QLabel,
)
from PyQt6.QtCore import Qt


ProfileType = Literal["grid", "monitor", "mixer", "media"]


class NewProfileDialog(QDialog):
    """
    Dialóg na vytvorenie nového profilu.
    Zoberie app_root (koreň projektu), načíta ui/new_profile_dialog.ui
    a vráti názov + typ profilu.
    """

    def __init__(self, app_root: Path, parent=None):
        """Load UI and wire signals for the new profile dialog."""
        super().__init__(parent)

        ui_file = app_root / "ui" / "new_profile_dialog.ui"
        uic.loadUi(str(ui_file), self)

        # widgety z .ui
        self.lineProfileName: QLineEdit = self.findChild(QLineEdit, "lineProfileName")
        self.comboProfileType: QComboBox = self.findChild(QComboBox, "comboProfileType")
        self.labelTypeDescription: QLabel = self.findChild(QLabel, "labelTypeDescription")
        self.buttonBox: QDialogButtonBox = self.findChild(QDialogButtonBox, "buttonBox")

        # mapovanie index → interný kód typu
        self._type_map: list[ProfileType] = ["grid", "monitor", "mixer", "media"]

        # nastavenie okna
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        # text popisu podľa aktuálneho typu
        self._update_description(self.current_type)

        # signály
        self.comboProfileType.currentIndexChanged.connect(self._on_type_changed)
        self.lineProfileName.textChanged.connect(self._on_name_changed)

        self.buttonBox.accepted.connect(self._on_accept)
        self.buttonBox.rejected.connect(self.reject)

        # OK tlačidlo zakázať, kým nie je názov
        ok_btn = self.buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setEnabled(False)

        # focus na názov
        self.lineProfileName.setFocus()

    # --- vlastnosti ---

    @property
    def profile_name(self) -> str:
        """Aktuálny text z názvu profilu."""
        return self.lineProfileName.text().strip()

    @property
    def current_type(self) -> ProfileType:
        """Aktuálne vybraný typ profilu ako internal kód."""
        idx = self.comboProfileType.currentIndex()
        if 0 <= idx < len(self._type_map):
            return self._type_map[idx]
        return "grid"

    # --- handlery ---

    def _on_type_changed(self, index: int):
        """Prepíše popis pri zmene typu."""
        self._update_description(self.current_type)

    def _on_name_changed(self, text: str):
        """Povolí OK tlačidlo len keď je názov neprázdny."""
        ok_btn = self.buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setEnabled(bool(text.strip()))

    def _on_accept(self):
        """Akceptuje dialog iba ak je názov zadaný."""
        if not self.profile_name:
            return
        self.accept()

    # --- helper ---

    def _update_description(self, ptype: ProfileType):
        """Nastaví textový popis podľa vybraného typu profilu."""
        if ptype == "grid":
            text = "Klasický profil s tlačidlami, ikonami a akciami."
        elif ptype == "monitor":
            text = "System monitor – zobrazenie CPU, GPU, RAM, disku, siete a FPS."
        elif ptype == "mixer":
            text = "Audio mixer – fadery na ovládanie hlasitosti aplikácií."
        elif ptype == "media":
            text = "Media control – ovládanie prehrávania (Spotify / systém)."
        else:
            text = "Vyberte typ nového profilu."

        if self.labelTypeDescription is not None:
            self.labelTypeDescription.setText(text)

    # --- API pre hlavné okno ---

    def get_values(self) -> tuple[str, ProfileType]:
        """Jednoduchý helper, aby sa dalo spraviť: name, ptype = dlg.get_values()."""
        return self.profile_name, self.current_type

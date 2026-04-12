from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .platform_env import IS_LINUX


@dataclass
class RuntimeUpdateResult:
    success: bool
    updated: bool
    message: str
    restart_required: bool = False


class RuntimeManager:
    def __init__(self, app_root: Path, app_name: str = "MacroTouch") -> None:
        self.app_root = Path(app_root)
        self.app_name = app_name

    def _desktop_file_path(self) -> Path:
        return Path.home() / ".config" / "autostart" / "macrotouch.desktop"

    def _desktop_exec(self) -> str:
        if getattr(sys, "frozen", False):
            executable = Path(sys.executable)
            parts = [str(executable), "--background"]
        else:
            script_path = self.app_root / "main.py"
            parts = [sys.executable, str(script_path), "--background"]

        escaped: list[str] = []
        for part in parts:
            token = part.replace("\\", "\\\\").replace('"', '\\"')
            if any(ch.isspace() for ch in token):
                token = f'"{token}"'
            escaped.append(token)
        return " ".join(escaped)

    def is_autostart_enabled(self) -> bool:
        if not IS_LINUX:
            return False
        return self._desktop_file_path().is_file()

    def set_autostart(self, enabled: bool) -> tuple[bool, str]:
        if not IS_LINUX:
            return False, "Autostart je momentalne implementovany len pre Linux."

        desktop_file = self._desktop_file_path()
        if enabled:
            desktop_file.parent.mkdir(parents=True, exist_ok=True)
            icon_path = self.app_root / "icons" / "MacroTouch.ico"
            icon_line = f"Icon={icon_path}\n" if icon_path.exists() else ""
            content = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                f"Name={self.app_name}\n"
                "Comment=MacroTouch background service\n"
                f"Exec={self._desktop_exec()}\n"
                f"{icon_line}"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n"
            )
            desktop_file.write_text(content, encoding="utf-8")
            return True, f"Autostart zapnuty: {desktop_file}"

        try:
            if desktop_file.exists():
                desktop_file.unlink()
            return True, "Autostart vypnuty."
        except Exception as e:
            return False, f"Nepodarilo sa vypnut autostart: {e}"

    def update_from_source(self) -> RuntimeUpdateResult:
        git_dir = self.app_root / ".git"
        if not git_dir.exists():
            return RuntimeUpdateResult(
                success=False,
                updated=False,
                message="Aktualizacia je dostupna pre git checkout (repo s .git).",
            )

        pull = subprocess.run(
            ["git", "-C", str(self.app_root), "pull", "--ff-only"],
            capture_output=True,
            text=True,
            check=False,
        )
        output = "\n".join(
            part.strip() for part in ((pull.stdout or ""), (pull.stderr or "")) if part.strip()
        ).strip()

        if pull.returncode != 0:
            return RuntimeUpdateResult(
                success=False,
                updated=False,
                message=output or "git pull zlyhal.",
            )

        low = output.lower()
        updated = "already up to date" not in low and "uz je aktualny" not in low

        req_file = self.app_root / "requirements.txt"
        pip_note = ""
        if updated and req_file.exists():
            pip_run = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                capture_output=True,
                text=True,
                check=False,
            )
            if pip_run.returncode != 0:
                pip_err = (pip_run.stderr or pip_run.stdout or "").strip()
                pip_note = f"\nZavislosti sa nepodarilo doinstalovat: {pip_err}"

        if updated:
            msg = (output or "Aplikacia bola aktualizovana.") + pip_note
            return RuntimeUpdateResult(
                success=True,
                updated=True,
                message=msg,
                restart_required=True,
            )

        return RuntimeUpdateResult(
            success=True,
            updated=False,
            message=output or "Aplikacia je uz aktualna.",
            restart_required=False,
        )

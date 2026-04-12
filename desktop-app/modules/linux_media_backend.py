# core/linux_media_backend.py
from __future__ import annotations

import os
import re
import shutil
import subprocess
from urllib.parse import urlparse

from .media_backend import IMediaBackend


_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE_VALUES


def _is_flatpak_runtime() -> bool:
    return bool(os.environ.get("FLATPAK_ID")) or os.path.exists("/.flatpak-info")


def _is_wayland_session() -> bool:
    return os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"


IN_FLATPAK = _is_flatpak_runtime()
IN_WAYLAND = _is_wayland_session()
ALLOW_WAYLAND_XTOOLS = _env_flag("MACROTOUCH_ALLOW_WAYLAND_XTOOLS")
try:
    CMD_TIMEOUT_SEC = max(
        0.5,
        float(os.environ.get("MACROTOUCH_CMD_TIMEOUT_SEC", "1.6")),
    )
except Exception:
    CMD_TIMEOUT_SEC = 1.6


def _find_cmd_with_flatpak(cmd: str) -> list[str] | None:
    """Find command directly or via flatpak-spawn --host fallback."""
    direct = shutil.which(cmd)
    if direct:
        return [direct]
    if IN_FLATPAK:
        spawn = shutil.which("flatpak-spawn")
        if spawn:
            try:
                probe = subprocess.run(
                    [spawn, "--host", "which", cmd],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                probe = None
            if probe and probe.returncode == 0:
                return [spawn, "--host", cmd]
    return None


def _find_playerctl_cmd() -> list[str] | None:
    """
    Prefer priamy playerctl; ak bežíme vo Flatpaku, skús hosta cez flatpak-spawn.
    """
    return _find_cmd_with_flatpak("playerctl")


PLAYERCTL_CMD = _find_playerctl_cmd()
XDOTOOL_CMD = _find_cmd_with_flatpak("xdotool")
YDO_TOOL_CMD = _find_cmd_with_flatpak("ydotool")
WMCTRL_CMD = _find_cmd_with_flatpak("wmctrl")
XDG_OPEN_CMD = _find_cmd_with_flatpak("xdg-open")
BRIGHTNESSCTL_CMD = _find_cmd_with_flatpak("brightnessctl")
XBACKLIGHT_CMD = _find_cmd_with_flatpak("xbacklight")


_YDOTOOL_KEYCODES: dict[str, int] = {
    "esc": 1,
    "escape": 1,
    "1": 2,
    "2": 3,
    "3": 4,
    "4": 5,
    "5": 6,
    "6": 7,
    "7": 8,
    "8": 9,
    "9": 10,
    "0": 11,
    "backspace": 14,
    "tab": 15,
    "q": 16,
    "w": 17,
    "e": 18,
    "r": 19,
    "t": 20,
    "y": 21,
    "u": 22,
    "i": 23,
    "o": 24,
    "p": 25,
    "enter": 28,
    "return": 28,
    "ctrl": 29,
    "control": 29,
    "leftctrl": 29,
    "lctrl": 29,
    "ctrlleft": 29,
    "a": 30,
    "s": 31,
    "d": 32,
    "f": 33,
    "g": 34,
    "h": 35,
    "j": 36,
    "k": 37,
    "l": 38,
    "shift": 42,
    "leftshift": 42,
    "lshift": 42,
    "z": 44,
    "x": 45,
    "c": 46,
    "v": 47,
    "b": 48,
    "n": 49,
    "m": 50,
    "rightshift": 54,
    "rshift": 54,
    "alt": 56,
    "leftalt": 56,
    "lalt": 56,
    "space": 57,
    "capslock": 58,
    "f1": 59,
    "f2": 60,
    "f3": 61,
    "f4": 62,
    "f5": 63,
    "f6": 64,
    "f7": 65,
    "f8": 66,
    "f9": 67,
    "f10": 68,
    "f11": 87,
    "f12": 88,
    "rightctrl": 97,
    "rctrl": 97,
    "rightalt": 100,
    "ralt": 100,
    "home": 102,
    "up": 103,
    "pgup": 104,
    "pageup": 104,
    "left": 105,
    "right": 106,
    "end": 107,
    "down": 108,
    "pgdn": 109,
    "pagedown": 109,
    "insert": 110,
    "delete": 111,
    "del": 111,
    "meta": 125,
    "super": 125,
    "win": 125,
    "leftmeta": 125,
    "leftsuper": 125,
}


def _tool_name_from_cmd(cmd: list[str] | None) -> str:
    if not cmd:
        return ""
    head = os.path.basename(cmd[0])
    if head == "flatpak-spawn":
        for token in reversed(cmd[1:]):
            if token and not token.startswith("-"):
                return os.path.basename(token)
        return ""
    return head


def _wayland_flatpak_xtools_blocked(cmd: list[str] | None) -> bool:
    # On Wayland, xdotool/wmctrl commonly trigger the Remote Desktop portal
    # or simply do not work. Keep X11 tools opt-in via env override.
    tool = _tool_name_from_cmd(cmd)
    return bool(
        IN_WAYLAND
        and not ALLOW_WAYLAND_XTOOLS
        and tool in {"xdotool", "wmctrl"}
    )


def _run(cmd: list[str]) -> bool:
    """Run command and return success; print stderr on failure."""
    try:
        res = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=CMD_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[LinuxMediaBackend] CMD {cmd} timed out after {CMD_TIMEOUT_SEC:.1f}s"
        )
        return False
    except Exception as e:
        print(f"[LinuxMediaBackend] CMD {cmd} failed: {e}")
        return False
    if res.returncode == 0:
        return True
    err = (res.stderr or "").strip()
    out = (res.stdout or "").strip()
    detail = err or out or f"exit={res.returncode}"
    print(f"[LinuxMediaBackend] CMD {cmd} failed: {detail}")
    return False


def _run_ok(cmd: list[str]) -> bool:
    try:
        subprocess.Popen(cmd)
        return True
    except Exception as e:
        print(f"[LinuxMediaBackend] CMD {cmd} failed: {e}")
        return False


def _run_playerctl(args: list[str]) -> None:
    """Execute playerctl if available, otherwise log missing dependency."""
    if not PLAYERCTL_CMD:
        print("[LinuxMediaBackend] playerctl nenájdený – nainštaluj ho alebo povoľ flatpak-spawn.")
        return
    _run(PLAYERCTL_CMD + args)


def _ydotool_key_sequence(keys: str) -> list[str] | None:
    text = (keys or "").strip()
    if not text:
        return None

    if re.fullmatch(r"\d+:[01](?:\s+\d+:[01])*", text):
        return text.split()

    if "+" in text:
        raw = [p.strip().lower() for p in text.split("+") if p.strip()]
    else:
        raw = [text.strip().lower()]
    if not raw:
        return None

    codes: list[int] = []
    for token in raw:
        code = _YDOTOOL_KEYCODES.get(token)
        if code is None:
            return None
        codes.append(code)

    if len(codes) == 1:
        c = codes[0]
        return [f"{c}:1", f"{c}:0"]

    modifiers = codes[:-1]
    main = codes[-1]
    seq = [f"{c}:1" for c in modifiers]
    seq.extend([f"{main}:1", f"{main}:0"])
    seq.extend(f"{c}:0" for c in reversed(modifiers))
    return seq


def _run_ydotool_keys(keys: str) -> bool:
    if not YDO_TOOL_CMD:
        return False
    seq = _ydotool_key_sequence(keys)
    if not seq:
        return False
    return _run(YDO_TOOL_CMD + ["key", *seq])


class LinuxMediaBackend(IMediaBackend):
    """Media backend using playerctl/pactl/wmctrl/brightnessctl on Linux."""
    # ---------- HLASITOSŤ ----------

    def set_volume(self, percent: int) -> None:
        """Nastaví absolútnu hlasitosť cez pactl."""
        # nastavenie absolútnej hlasitosti na percentá
        # PipeWire/Pulse – percentá sú relatívne, takže to riešime priamo
        _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"])

    def change_volume(self, delta_percent: int) -> None:
        """Relatívna zmena hlasitosti cez pactl."""
        # relatívna zmena
        sign = "+" if delta_percent >= 0 else ""
        _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}{delta_percent}%"])

    def toggle_mute(self) -> None:
        """Prepne mute na default sink."""
        _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])

    # ---------- MEDIA ----------

    def play_pause(self) -> None:
        """Play/pause aktuálneho MPRIS prehrávača."""
        _run_playerctl(["play-pause"])

    def next_track(self) -> None:
        """Ďalšia skladba cez playerctl."""
        _run_playerctl(["next"])

    def prev_track(self) -> None:
        """Predchádzajúca skladba cez playerctl."""
        _run_playerctl(["previous"])

    # ---------- APLIKÁCIE / OKNÁ / KLÁVESY ----------

    def open_app(self, path: str) -> None:
        """Spustí aplikáciu; vo Flatpaku skúsi hosta cez flatpak-spawn."""
        # spawn príkazu; ak bežíme vo flatpaku, použijeme hosta
        import shlex

        cmd_raw = path.strip()
        if not cmd_raw:
            return

        try:
            argv_raw = shlex.split(cmd_raw)
        except Exception:
            argv_raw = [cmd_raw]

        if not argv_raw:
            return

        # Jednoslovný vstup (napr. "com.spotify.Client") skús cez flatpak export.
        # Viacslovný príkaz (napr. "flatpak run com.spotify.Client") nechaj bez úprav.
        if len(argv_raw) == 1 and "/" not in argv_raw[0]:
            token = argv_raw[0]
            host_candidates = [
                f"/var/lib/flatpak/exports/bin/{token}",
                os.path.expanduser(f"~/.local/share/flatpak/exports/bin/{token}"),
                token,
            ]
            cmd_selected = next((c for c in host_candidates if os.path.exists(c)), token)
            argv = [cmd_selected]
        else:
            argv = argv_raw

        fp_spawn = shutil.which("flatpak-spawn")
        if IN_FLATPAK and fp_spawn and _run_ok([fp_spawn, "--host"] + argv):
            return

        # fallback: priamo v sandboxe
        if _run_ok(argv):
            return

        # posledný fallback na asociovaný opener pre súbory/URL
        target = argv[0] if argv else cmd_raw
        parsed = urlparse(target)
        looks_like_url = bool(parsed.scheme and parsed.netloc)
        expanded = os.path.expanduser(os.path.expandvars(target))
        if XDG_OPEN_CMD and (looks_like_url or os.path.exists(expanded)):
            if _run_ok(XDG_OPEN_CMD + [target]):
                return

        print(f"[LinuxMediaBackend] open_app({path!r}) failed")

    def send_keys(self, keys: str) -> None:
        """Pošle klávesy cez xdotool/ydotool ak sú k dispozícii."""
        if IN_WAYLAND and _run_ydotool_keys(keys):
            return

        if IN_WAYLAND and YDO_TOOL_CMD and not ALLOW_WAYLAND_XTOOLS:
            print(
                f"[LinuxMediaBackend] send_keys({keys!r}) unsupported for ydotool format "
                "(set MACROTOUCH_ALLOW_WAYLAND_XTOOLS=1 to force xdotool)."
            )
            return

        if XDOTOOL_CMD:
            if _wayland_flatpak_xtools_blocked(XDOTOOL_CMD):
                print(
                    "[LinuxMediaBackend] send_keys blocked on Wayland "
                    "(set MACROTOUCH_ALLOW_WAYLAND_XTOOLS=1 to force xdotool)."
                )
                return
            if _run(XDOTOOL_CMD + ["key", "--clearmodifiers", keys]):
                return
        if _run_ydotool_keys(keys):
            return
        print(f"[LinuxMediaBackend] send_keys({keys!r}) – xdotool/ydotool nenájdené")

    def minimize_active_window(self) -> None:
        """Minimalizuje aktívne okno cez wmctrl."""
        if WMCTRL_CMD:
            if _wayland_flatpak_xtools_blocked(WMCTRL_CMD):
                print(
                    "[LinuxMediaBackend] minimize_active_window blocked on "
                    "Wayland (set MACROTOUCH_ALLOW_WAYLAND_XTOOLS=1)."
                )
                return
            else:
                if _run(WMCTRL_CMD + ["-r", ":ACTIVE:", "-b", "add,hidden"]):
                    return
        if XDOTOOL_CMD:
            if _wayland_flatpak_xtools_blocked(XDOTOOL_CMD):
                print(
                    "[LinuxMediaBackend] minimize_active_window blocked on "
                    "Wayland (set MACROTOUCH_ALLOW_WAYLAND_XTOOLS=1)."
                )
                return
            if _run(XDOTOOL_CMD + ["getactivewindow", "windowminimize"]):
                return
        print("[LinuxMediaBackend] minimize_active_window() – wmctrl/xdotool nenájdené")

    def toggle_maximize_active_window(self) -> None:
        """Maximalizuje/obnoví aktívne okno cez wmctrl."""
        if WMCTRL_CMD:
            if _wayland_flatpak_xtools_blocked(WMCTRL_CMD):
                print(
                    "[LinuxMediaBackend] toggle_maximize_active_window blocked on "
                    "Wayland (set MACROTOUCH_ALLOW_WAYLAND_XTOOLS=1)."
                )
                return
            else:
                if _run(WMCTRL_CMD + ["-r", ":ACTIVE:", "-b", "toggle,maximized_vert,maximized_horz"]):
                    return
        if XDOTOOL_CMD:
            if _wayland_flatpak_xtools_blocked(XDOTOOL_CMD):
                print(
                    "[LinuxMediaBackend] toggle_maximize_active_window blocked on "
                    "Wayland (set MACROTOUCH_ALLOW_WAYLAND_XTOOLS=1)."
                )
                return
            if _run(
                XDOTOOL_CMD
                + [
                    "getactivewindow",
                    "windowstate",
                    "--toggle",
                    "MAXIMIZED_VERT",
                    "--toggle",
                    "MAXIMIZED_HORZ",
                ]
            ):
                return
        print("[LinuxMediaBackend] toggle_maximize_active_window() – wmctrl/xdotool nenájdené")

    # ---------- JAS ----------

    def set_brightness(self, percent: int) -> None:
        """Nastaví jas cez brightnessctl/xbacklight, podľa dostupnosti."""
        pct = max(0, min(100, int(percent)))
        if BRIGHTNESSCTL_CMD:
            _run(BRIGHTNESSCTL_CMD + ["set", f"{pct}%"])
            return
        if XBACKLIGHT_CMD:
            _run(XBACKLIGHT_CMD + ["-set", str(pct)])
            return
        print(f"[LinuxMediaBackend] set_brightness({pct}) – brightnessctl/xbacklight nenájdené")

    def change_brightness(self, delta_percent: int) -> None:
        """Relatívna zmena jasu."""
        delta = int(delta_percent)
        if delta == 0:
            return
        if BRIGHTNESSCTL_CMD:
            if delta > 0:
                val = f"+{delta}%"
            else:
                val = f"{abs(delta)}%-"  # brightnessctl používa „5%-“ pre pokles
            _run(BRIGHTNESSCTL_CMD + ["set", val])
            return
        if XBACKLIGHT_CMD:
            if delta > 0:
                _run(XBACKLIGHT_CMD + ["-inc", str(abs(delta))])
            else:
                _run(XBACKLIGHT_CMD + ["-dec", str(abs(delta))])
            return
        print(f"[LinuxMediaBackend] change_brightness({delta}) – brightnessctl/xbacklight nenájdené")

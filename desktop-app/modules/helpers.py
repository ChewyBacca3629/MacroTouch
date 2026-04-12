# core/helpers.py
import os
import re
import subprocess
import sys
from typing import Dict


_c_ident_re = re.compile(r'[^0-9A-Za-z_]+')

def c_ident_from_filename(path_or_name: str) -> str:
    """Create a C-friendly identifier based on filename (used for icon symbols)."""
    base = os.path.splitext(os.path.basename(path_or_name))[0]
    base = _c_ident_re.sub('_', base)
    if base and base[0].isdigit():
        base = f'icon_{base}'
    base = re.sub(r'__+', '_', base).strip('_')
    return f'epd_bitmap_{base}'


def open_path_smart(path: str) -> bool:
    """Open a file/app using native launcher on Windows, otherwise xdg-open/open."""
    if not path:
        return False
    p = path.strip().strip('"')

    is_windows = os.name == "nt"

    # --- Windows cesta: preferujeme natívne spustenie ---
    if is_windows:
        try:
            os.startfile(p)  # type: ignore[attr-defined]
            return True
        except OSError:
            pass

        if p.lower().endswith((".bat", ".cmd")):
            try:
                subprocess.Popen(["cmd", "/c", p], shell=True)
                return True
            except Exception:
                pass

        if p.lower().endswith(".ps1"):
            try:
                subprocess.Popen([
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    p
                ])
                return True
            except Exception:
                pass

        try:
            subprocess.Popen(["cmd", "/c", "start", "", p], shell=True)
            return True
        except Exception as e:
            print(f"OpenApp error: {e}")
            return False

    # --- Linux / macOS: použijeme asociovaný opener ---
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, p])
        return True
    except Exception as e:
        print(f"OpenApp error ({opener}): {e}")
        return False



def _debounced(ts_dict: Dict[str, float], key: str, now: float, min_interval: float = 0.30) -> bool:
    """
    Generický debounce helper.

    ts_dict: dict s poslednými časmi príkazov
    key:     meno príkazu
    now:     aktuálny čas (time.monotonic())
    """
    last = ts_dict.get(key, 0.0)
    if now - last < min_interval:
        return False
    ts_dict[key] = now
    return True

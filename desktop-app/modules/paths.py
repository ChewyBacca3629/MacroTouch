import os
import sys
from pathlib import Path

def _appdata_dir() -> Path:
    """Return (and create) the per-user MacroTouch config directory."""
    if os.name == "nt":
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")

    p = Path(base) / "MacroTouch"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_file() -> Path:
    """Path to the main persisted state JSON."""
    return _appdata_dir() / "state.json"


SMART_HOME_STATE_FILE = _appdata_dir() / "smart_home_state.json"

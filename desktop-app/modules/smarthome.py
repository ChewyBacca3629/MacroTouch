"""Helpers for SmartHome state persistence and sketch generation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .paths import SMART_HOME_STATE_FILE
from .smarthome_template import SMART_HOME_TEMPLATE


DEFAULT_SMART_HOME_BASE_URL = "http://192.168.169.172"


def normalize_smart_home_base_url(url: str | None) -> str:
    """Return a non-empty SmartHome base URL."""
    return (url or "").strip() or DEFAULT_SMART_HOME_BASE_URL


def load_smart_home_state(state_file: Path = SMART_HOME_STATE_FILE) -> dict[str, str]:
    """Load SmartHome state JSON, returning an empty dict if missing."""
    if not state_file.exists():
        return {}
    with open(state_file, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def save_smart_home_state(
    data: Mapping[str, str],
    state_file: Path = SMART_HOME_STATE_FILE,
) -> dict[str, str]:
    """Persist SmartHome state JSON and return the normalized payload."""
    payload = {str(key): str(value) for key, value in dict(data).items()}
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return payload


def render_smarthome_sketch(
    wifi_ssid: str,
    wifi_pass: str,
    relay_names: list[str] | tuple[str, str, str, str],
) -> str:
    """Fill the SmartHome sketch template with runtime values."""
    r1, r2, r3, r4 = relay_names

    def esc(value: str) -> str:
        return str(value).replace('"', '\\"')

    code = SMART_HOME_TEMPLATE
    code = code.replace("__WIFI_SSID__", esc(wifi_ssid))
    code = code.replace("__WIFI_PASS__", esc(wifi_pass))
    code = code.replace("__R1__", esc(r1))
    code = code.replace("__R2__", esc(r2))
    code = code.replace("__R3__", esc(r3))
    code = code.replace("__R4__", esc(r4))
    return code


def generate_smarthome_sketch(
    project_root: Path,
    wifi_ssid: str,
    wifi_pass: str,
    relay_names: list[str] | tuple[str, str, str, str],
    write_text_if_changed,
) -> Path:
    """Generate SmartHomeSketch.ino and return its sketch directory."""
    project_root.mkdir(parents=True, exist_ok=True)
    sketch_dir = project_root / "SmartHomeSketch"
    sketch_dir.mkdir(parents=True, exist_ok=True)
    ino_path = sketch_dir / "SmartHomeSketch.ino"
    code = render_smarthome_sketch(wifi_ssid, wifi_pass, relay_names)
    write_text_if_changed(ino_path, code, encoding="utf-8")
    return sketch_dir

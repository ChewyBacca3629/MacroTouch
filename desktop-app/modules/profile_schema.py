"""Shared profile normalization and defaulting helpers."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, MutableMapping

from .errors import ProfileValidationError


PROFILE_MODES = {"grid", "media", "monitor", "mixer"}

DEFAULT_PROFILE: dict[str, Any] = {
    "rows": 3,
    "cols": 4,
    "mode": "grid",
    "btnA_action": "None",
    "btnB_action": "None",
    "pot_action": "None",
}


def new_default_profile() -> dict[str, Any]:
    """Return a fresh default profile payload."""
    return deepcopy(DEFAULT_PROFILE)


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _normalize_mode(value: Any) -> str:
    mode = str(value or "grid")
    if mode not in PROFILE_MODES:
        return "grid"
    return mode


def normalize_profile(profile: object) -> dict[str, Any]:
    """Validate and normalize a profile while preserving unknown keys."""
    if not isinstance(profile, dict):
        raise ProfileValidationError("Profile must be a dictionary")

    normalized = dict(profile)
    normalized["rows"] = _clamp_int(profile.get("rows", 3), default=3, minimum=1, maximum=4)
    normalized["cols"] = _clamp_int(profile.get("cols", 4), default=4, minimum=1, maximum=4)
    normalized["mode"] = _normalize_mode(profile.get("mode", "grid"))
    normalized.setdefault("btnA_action", "None")
    normalized.setdefault("btnB_action", "None")
    normalized.setdefault("pot_action", "None")
    return normalized


def apply_profile_mode_defaults(
    profile: MutableMapping[str, Any],
    mode: str | None = None,
) -> dict[str, Any]:
    """Apply normalized mode-specific defaults and return a plain dict."""
    normalized = normalize_profile(profile)
    resolved_mode = _normalize_mode(mode or normalized.get("mode", "grid"))
    normalized["mode"] = resolved_mode

    if resolved_mode == "monitor":
        monitor = normalized.get("monitor")
        if not isinstance(monitor, dict):
            monitor = {}
        monitor.setdefault("update_interval_ms", 500)
        monitor.setdefault("order", ["CPU", "GPU", "RAM", "DISK", "NET", "FPS"])
        monitor.setdefault("layout", "2x3")
        normalized["monitor"] = monitor
    elif resolved_mode == "mixer":
        mixer = normalized.get("mixer")
        if not isinstance(mixer, dict):
            mixer = {}
        mixer.setdefault("device", "")
        mixer.setdefault("apps", [])
        mixer.setdefault("layout", "auto")
        normalized["mixer"] = mixer
    elif resolved_mode == "media":
        media = normalized.get("media")
        if not isinstance(media, dict):
            media = {}
        media.setdefault("provider", "spotify")
        media.setdefault("show_art", True)
        media.setdefault("show_progress", True)
        normalized["media"] = media

    return normalized

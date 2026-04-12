"""State persistence and validation for MacroTouch."""
from copy import deepcopy
import json
import os
from pathlib import Path

from .paths import _state_file
from .errors import ProfileValidationError, StatePersistenceError
from .logging import get_logger
from .profile_schema import apply_profile_mode_defaults, new_default_profile


DEFAULT_STATE = {
    "schema_version": 1,
    "current_profile": "Default",
    "profiles": {"Default": new_default_profile()},
    "app_flags": {},
    "display_settings": {},
}


class StateManager:
    def __init__(self, state_file: Path | None = None):
        self._state_file = state_file or _state_file()
        self.logger = get_logger(__name__)

    def load_state(self) -> dict:
        if not self._state_file.exists():
            self.logger.debug("State file does not exist, returning default state")
            return deepcopy(DEFAULT_STATE)

        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            self.logger.exception("Failed to read state file")
            raise StatePersistenceError("Failed to read state file") from exc

        return self.validate_state(data)

    def validate_state(self, data: object) -> dict:
        if not isinstance(data, dict):
            raise StatePersistenceError("State content is not a JSON object")

        profiles = data.get("profiles")
        if not isinstance(profiles, dict) or not profiles:
            raise ProfileValidationError("profiles must be a non-empty object")

        normalized_profiles = {}
        for name, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            normalized_profiles[name] = apply_profile_mode_defaults(profile.copy())

        if not normalized_profiles:
            raise ProfileValidationError("profiles must contain at least one valid profile")

        current_profile = data.get("current_profile", "Default")
        if current_profile not in normalized_profiles:
            current_profile = next(iter(normalized_profiles.keys()))

        app_flags = data.get("app_flags", {})
        if not isinstance(app_flags, dict):
            app_flags = {}

        display_settings = data.get("display_settings", {})
        if not isinstance(display_settings, dict):
            display_settings = {}

        return {
            "schema_version": 1,
            "current_profile": current_profile,
            "profiles": normalized_profiles,
            "app_flags": app_flags,
            "display_settings": display_settings,
        }

    def save_state(self, state: dict) -> None:
        validated = self.validate_state(state)

        fp = self._state_file
        fp.parent.mkdir(parents=True, exist_ok=True)

        tmp = fp.with_suffix(".tmp")
        bak = fp.with_suffix(".bak")

        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(validated, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            if fp.exists():
                try:
                    os.replace(fp, bak)
                except Exception:
                    pass

            os.replace(tmp, fp)
            self.logger.info("State saved to %s", fp)
        except Exception as exc:
            self.logger.exception("Failed to save state")
            raise StatePersistenceError("Failed to save state") from exc

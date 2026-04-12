# core/profiles.py
from typing import Dict, Any, Callable

from .errors import ProfileValidationError
from .profile_schema import (
    DEFAULT_PROFILE,
    apply_profile_mode_defaults,
    new_default_profile,
    normalize_profile,
)


class ProfileManager:
    """Trieda pre správu profilov (data-only)."""

    def __init__(self) -> None:
        self.profiles: Dict[str, Dict[str, Any]] = {"Default": new_default_profile()}
        self.current_profile: str = "Default"
        self.profile_history: list[str] = []
        self._profile_loaded_callbacks: list[Callable[[str, Dict[str, Any]], None]] = []

    def on_profile_loaded(self, callback: Callable[[str, Dict[str, Any]], None]) -> None:
        """Register a callback executed whenever a profile is successfully loaded."""
        if callback not in self._profile_loaded_callbacks:
            self._profile_loaded_callbacks.append(callback)

    def _emit_profile_loaded(self, name: str, profile: Dict[str, Any]) -> None:
        for cb in self._profile_loaded_callbacks:
            try:
                cb(name, profile)
            except Exception:
                pass

    def _normalize_profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        return normalize_profile(profile)

    def get_profile(self, name: str) -> Dict[str, Any] | None:
        return self.profiles.get(name)

    def set_current_profile(self, name: str) -> None:
        if name not in self.profiles:
            raise ProfileValidationError(f"Profile '{name}' not found")
        self.current_profile = name
        self.profile_history.append(name)

    def add_profile(self, name: str, from_profile: str | None = None) -> str:
        if not name:
            raise ProfileValidationError("Profile name cannot be empty")
        if name in self.profiles:
            raise ProfileValidationError("Profile already exists")

        if from_profile is None:
            source = new_default_profile()
        else:
            source = self.profiles.get(from_profile, DEFAULT_PROFILE)
            if not isinstance(source, dict):
                source = DEFAULT_PROFILE

        profile = apply_profile_mode_defaults(source.copy())
        self.profiles[name] = profile

        return name

    def rename_profile(self, old_name: str, new_name: str) -> str:
        if not new_name:
            raise ProfileValidationError("Profile name cannot be empty")
        if old_name not in self.profiles:
            raise ProfileValidationError("Profile does not exist")
        if new_name in self.profiles and new_name != old_name:
            raise ProfileValidationError("Profile name is already taken")

        self.profiles[new_name] = self.profiles.pop(old_name)
        if self.current_profile == old_name:
            self.current_profile = new_name

        return new_name

    def delete_profile(self, name: str) -> str:
        if name not in self.profiles:
            raise ProfileValidationError("Profile does not exist")
        if len(self.profiles) <= 1:
            raise ProfileValidationError("At least one profile must remain")

        del self.profiles[name]
        if self.current_profile == name:
            self.current_profile = next(iter(self.profiles))

        return name

    def load_profile(self, name: str) -> Dict[str, Any]:
        if name not in self.profiles:
            raise ProfileValidationError(f"Profile '{name}' not found")

        self.set_current_profile(name)
        profile = apply_profile_mode_defaults(self.profiles[name])
        self.profiles[name] = profile
        self._emit_profile_loaded(name, profile)
        return profile

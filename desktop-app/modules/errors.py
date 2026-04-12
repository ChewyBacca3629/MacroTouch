"""Custom exceptions for MacroTouch core modules."""

class ProfileValidationError(ValueError):
    """Profile data is invalid."""


class StatePersistenceError(IOError):
    """Error persisting or loading state file."""

"""Exceptions for Clip Discovery, Scoring, and Selection."""


class DiscoveryError(Exception):
    """Base exception for all clip discovery and scoring errors."""
    pass


class WindowGenerationError(DiscoveryError):
    """Raised when candidate window extraction fails."""
    pass


class ScoringError(DiscoveryError):
    """Raised when candidate scoring fails."""
    pass


class SelectionError(DiscoveryError):
    """Raised when candidate selection or ranking fails."""
    pass

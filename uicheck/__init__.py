"""UI Spec Checker: verify a dev screenshot against the design (spacing, fonts, colors)."""

from .models import Options
from .pipeline import run

__all__ = ["Options", "run"]

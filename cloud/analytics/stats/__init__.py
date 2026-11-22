"""Statistical analytics engine for urban heat island analysis (Issue #33, Spec 60)."""

from .core import (
    DEFAULT_VARIABLES,
    DEFAULT_REGRESSORS_S3,
    DEFAULT_REGRESSORS_S4,
    analyze_period_match,
)
from .runner import build_analytics_outputs

__all__ = [
    "DEFAULT_VARIABLES",
    "DEFAULT_REGRESSORS_S3",
    "DEFAULT_REGRESSORS_S4",
    "analyze_period_match",
    "build_analytics_outputs",
]

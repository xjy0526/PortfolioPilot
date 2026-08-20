"""Deprecated import shim for the governed evaluation API.

New code must import :mod:`app.api.evaluation`. This module remains until the
documented v3 compatibility boundary and contains no request-time logic.
"""
from __future__ import annotations

from app.api.evaluation import (
    get_evaluation_dashboard,
    get_evaluation_traces,
    router,
)


__all__ = ["get_evaluation_dashboard", "get_evaluation_traces", "router"]

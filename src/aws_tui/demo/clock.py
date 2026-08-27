"""Shared deterministic wall clock for all demo providers."""

from datetime import UTC, datetime

DEMO_NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)

__all__ = ["DEMO_NOW"]

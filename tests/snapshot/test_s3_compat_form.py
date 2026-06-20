"""Snapshot tests for S3CompatFormModal add, edit, and validation x 10 themes."""

from __future__ import annotations

import pytest

from tests.snapshot.apps.s3_compat_form import (
    S3FormAddApp,
    S3FormEditApp,
    S3FormValidationErrorsApp,
)

THEMES = [
    "carbon",
    "voidline",
    "lattice",
    "amber",
    "solarized-light",
    "github-light",
    "one-light",
    "nord",
    "dracula",
    "gruvbox-dark",
]
TERMINAL_SIZE = (80, 32)


@pytest.mark.parametrize("theme", THEMES)
def test_s3_compat_form_add_mode(theme: str, snap_compare) -> None:
    assert snap_compare(S3FormAddApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_s3_compat_form_edit_mode(theme: str, snap_compare) -> None:
    assert snap_compare(S3FormEditApp(theme=theme), terminal_size=TERMINAL_SIZE)


@pytest.mark.parametrize("theme", THEMES)
def test_s3_compat_form_validation_errors(theme: str, snap_compare) -> None:
    assert snap_compare(S3FormValidationErrorsApp(theme=theme), terminal_size=TERMINAL_SIZE)

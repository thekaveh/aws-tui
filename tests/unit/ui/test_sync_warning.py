"""A sync row shows both the transfer error and the cleanup error when both exist."""

from __future__ import annotations

from aws_tui.domain.upload_batch import UploadOutcome
from aws_tui.ui.widgets.sync import _row_warning


def _outcome(error: BaseException | None, cleanup_error: str | None) -> UploadOutcome:
    # Only the two fields the warning reads matter here; the rest take neutral values.
    return UploadOutcome(
        item=None,  # type: ignore[arg-type]
        destination=None,  # type: ignore[arg-type]
        status=None,  # type: ignore[arg-type]
        started=True,
        bytes_done=0,
        published=False,
        uncertain=False,
        error=error,
        cleanup_error=cleanup_error,
    )


def test_row_warning_keeps_a_transfer_error_that_a_cleanup_error_follows() -> None:
    """The row used to show only the cleanup error, hiding the transfer failure."""
    warning = _row_warning(_outcome(RuntimeError("upload dropped"), "could not remove temp"))

    assert "upload dropped" in warning
    assert "could not remove temp" in warning


def test_row_warning_shows_each_failure_alone() -> None:
    assert _row_warning(_outcome(RuntimeError("upload dropped"), None)) == "upload dropped"
    assert _row_warning(_outcome(None, "could not remove temp")) == "could not remove temp"
    assert _row_warning(_outcome(None, None)) == ""

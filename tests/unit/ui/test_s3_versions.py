"""Historical browser text preserves observed bucket state on empty history."""

from types import SimpleNamespace

import pytest

from aws_tui.domain.s3_versions import BucketVersioning, S3VersionListing
from aws_tui.ui.widgets.s3_versions import version_text


@pytest.mark.parametrize(
    ("state", "notice"),
    [
        (BucketVersioning.UNVERSIONED, "Bucket versioning has never been enabled."),
        (BucketVersioning.SUSPENDED, "Bucket versioning is suspended."),
        (BucketVersioning.UNAVAILABLE, "Bucket versioning state is unavailable: access denied."),
    ],
)
def test_empty_history_retains_bucket_state_notice(state, notice):
    model = SimpleNamespace(
        prefix_label="s3://bucket/prefix/",
        selected=None,
        error_text="",
        listing=S3VersionListing("bucket", "prefix/deleted", (), state, notice),
    )
    text = version_text(model)
    assert notice in text
    assert "No retained versions" in text
    assert "Enter an exact key or select a version" not in text

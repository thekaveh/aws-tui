from __future__ import annotations

from collections.abc import Iterator, Sequence

from aws_tui.infra.redaction import redact_text, redact_value, safe_endpoint_display


class _CustomSequence(Sequence[object]):
    def __init__(self, *values: object) -> None:
        self._values = values

    def __getitem__(self, index: int) -> object:
        return self._values[index]

    def __len__(self) -> int:
        return len(self._values)

    def __iter__(self) -> Iterator[object]:
        return iter(self._values)


def test_redact_value_recurses_through_tuples_and_generic_sequences() -> None:
    payload = (
        {"secret_access_key": "AKIASECRET"},
        _CustomSequence(
            "https://user:pass@example.com/bucket?X-Amz-Signature=sig",
            {"plain": "kept"},
        ),
    )

    redacted = redact_value(payload)

    assert redacted == (
        {"secret_access_key": "[REDACTED]"},
        ["https://[REDACTED]@example.com/bucket?[REDACTED]", {"plain": "kept"}],
    )


def test_redact_text_preserves_malformed_url_and_redacts_other_fields() -> None:
    text = redact_text("url=https://[::1/path token=abc123")

    assert "https://[::1/path" in text
    assert "abc123" not in text
    assert "token=[REDACTED]" in text


def test_redact_text_covers_common_secret_carriers() -> None:
    text = redact_text(
        "Authorization: Bearer SECRETBEARER api_key=SECRETAPI private_key=SECRETPRIVATE"
    )

    for leaked in ["SECRETBEARER", "SECRETAPI", "SECRETPRIVATE"]:
        assert leaked not in text
    assert "Authorization: Bearer [REDACTED]" in text
    assert "api_key=[REDACTED]" in text
    assert "private_key=[REDACTED]" in text


def test_redact_value_treats_structured_authorization_as_sensitive() -> None:
    assert redact_value("Bearer SECRETBEARER", key="Authorization") == "[REDACTED]"


def test_safe_endpoint_display_drops_userinfo_query_and_fragment() -> None:
    displayed = safe_endpoint_display(
        "https://user:pass@example.com/bucket?X-Amz-Signature=sig#frag"
    )

    assert displayed == "example.com/bucket"

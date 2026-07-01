from __future__ import annotations

from collections.abc import Iterator, Sequence

from aws_tui.infra.redaction import redact_text, redact_value


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

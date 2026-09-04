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


def test_redact_text_covers_authorization_values_without_a_scheme() -> None:
    """A schemeless or ``=``-separated Authorization value must not survive.

    The scheme-preserving header pass only matches ``Authorization: <scheme>
    <credential>``. Values in either other shape reach the key/value pass, which
    previously exempted every unquoted ``authorization`` key on the assumption
    the header pass had already handled it — so they were emitted verbatim into
    durable logs and crash dumps.
    """
    for text in (
        "Authorization: SECRETOPAQUE",
        "authorization=SECRETOPAQUE",
        "Authorization:SECRETOPAQUE",
        # A following line must not be read as this value's credential: the
        # header pattern once spanned the newline, preserved SECRETOPAQUE as a
        # "scheme", and consumed the next header's key name instead -- leaking
        # both secrets.
        "authorization: SECRETOPAQUE\nx-api-key: SECONDSECRET",
        # Same ambiguity on one line: an unrecognized leading token is the
        # credential, not a scheme.
        "Authorization: SECRETOPAQUE next_token: SECONDSECRET",
        "Authorization: SECRETOPAQUE api_key: SECONDSECRET",
    ):
        redacted = redact_text(text)
        assert "SECRETOPAQUE" not in redacted, text
        assert "SECONDSECRET" not in redacted, text


def test_redact_text_preserves_only_recognized_authorization_schemes() -> None:
    assert redact_text("Authorization: Bearer CRED") == "Authorization: Bearer [REDACTED]"
    assert redact_text("Authorization: Basic CRED") == "Authorization: Basic [REDACTED]"
    assert "AWS4-HMAC-SHA256" in redact_text("Authorization: AWS4-HMAC-SHA256 Credential=CRED")
    # An unknown token is treated as the credential, never preserved.
    assert "NotAScheme" not in redact_text("Authorization: NotAScheme CRED")


def test_redact_text_covers_single_quoted_mapping_representations() -> None:
    text = redact_text(
        "{'secret_access_key': 'SECRET', "
        "'Authorization': 'Bearer BEARER_SECRET', "
        '"api_token": "DOUBLE_QUOTED_SECRET", '
        "'safe': 'visible'}"
    )

    assert "SECRET" not in text
    assert "BEARER_SECRET" not in text
    assert "DOUBLE_QUOTED_SECRET" not in text
    assert "'safe': 'visible'" in text
    assert text.count("[REDACTED]") == 3


def test_redact_text_redacts_url_fragments() -> None:
    text = redact_text("failed https://user:pass@example.com/bucket?sig=x#opaqueBearerToken123")

    assert "user" not in text
    assert "pass" not in text
    assert "sig=x" not in text
    assert "opaqueBearerToken123" not in text
    assert text == "failed https://[REDACTED]@example.com/bucket?[REDACTED]#[REDACTED]"


def test_redact_text_redacts_bare_url_fragments() -> None:
    text = redact_text("failed https://example.com/bucket#SECRETFRAG")

    assert "SECRETFRAG" not in text
    assert text == "failed https://example.com/bucket#[REDACTED]"


def test_redact_value_treats_structured_authorization_as_sensitive() -> None:
    assert redact_value("Bearer SECRETBEARER", key="Authorization") == "[REDACTED]"


def test_safe_endpoint_display_drops_userinfo_query_and_fragment() -> None:
    displayed = safe_endpoint_display(
        "https://user:pass@example.com/bucket?X-Amz-Signature=sig#frag"
    )

    assert displayed == "example.com/bucket"

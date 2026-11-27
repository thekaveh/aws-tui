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


def test_redact_text_covers_whitespace_separated_credential_output() -> None:
    """A key/value pair separated by whitespace alone must still be redacted.

    botocore renders credential-process failures as
    ``CredentialRetrievalError: … command output: aws_secret_access_key wJalr…``
    with no ``:`` or ``=`` between the key and its value. The pattern required
    one of those delimiters, so that string passed through verbatim into the
    rotating log and into crash dumps. The only thing standing between a failing
    credential process and a durable plaintext secret was a single
    ``isinstance(exc, CredentialRetrievalError)`` branch in the Athena client —
    which no test pinned.
    """
    leaked = "wJalrXUtnFEMIK7MDENGbPxRfiCY"
    for text in (
        f"aws_secret_access_key {leaked}",
        f"secret_access_key {leaked}",
        f"aws_session_token {leaked}",
        f"command output: aws_secret_access_key {leaked}",
        "Error when retrieving credentials from custom-process: "
        f"command output: aws_secret_access_key {leaked}",
    ):
        redacted = redact_text(text)
        assert leaked not in redacted, text
        assert "[REDACTED]" in redacted, text


def test_redact_text_whitespace_separator_does_not_span_a_newline() -> None:
    """A trailing key must not consume the following line as its value.

    The horizontal-only class matters: plain ``\\s`` would let a key ending one
    line swallow the next line's content, which both hides ordinary log text and
    (as an earlier fix on this branch found) can leave a real secret on the
    following line untouched while reporting a redaction.
    """
    redacted = redact_text("api_key\nordinary log line\nsecret_access_key VALUE")

    assert "ordinary log line" in redacted
    assert "VALUE" not in redacted


def test_redact_text_does_not_redact_ordinary_prose_after_a_safe_word() -> None:
    """Positive control against over-redaction from the widened separator."""
    assert redact_text("connection established") == "connection established"
    assert redact_text("listing 5 objects") == "listing 5 objects"
    assert "us-east-1" in redact_text("region us-east-1 selected")


def test_redaction_hides_sensitive_components_in_malformed_and_uppercase_urls() -> None:
    urls = (
        "https://name:synthetic-password@[broken/?opaque=synthetic-query#synthetic-fragment",
        "HTTPS://name:synthetic-password@example.test/?opaque=synthetic-query#synthetic-fragment",
        "https://name:synthetic-password'tail@example.test/path",
        'https://name:synthetic-password"tail@[broken/?opaque=synthetic-query',
        "https://name:synthetic-password<tail@[broken/#synthetic-fragment",
    )
    for url in urls:
        for text in (redact_text(f"provider failed at {url}"), safe_endpoint_display(url)):
            assert text is not None
            for secret in ("synthetic-password", "synthetic-query", "synthetic-fragment"):
                assert secret not in text


def test_redaction_preserves_whitespace_separated_neighbor_of_quoted_url() -> None:
    text = redact_text(
        "url='https://name:synthetic-password@example.test/?opaque=synthetic-query' safe=visible"
    )
    assert "synthetic-password" not in text
    assert "synthetic-query" not in text
    assert "safe=visible" in text


def test_redact_text_redacts_a_value_that_is_itself_a_sensitive_key_name() -> None:
    """A value that looks like a key name must not expose the credential after it.

    ``password aws_secret_access_key wJalr...`` and ``password Password123``
    have the same shape: a key whose value is a keyword-bearing word, followed by
    another word. The scan therefore redacts the name and the token after it, so
    neither reading leaks. A token that starts its own key/value pair is scanned
    as a key, so its value is redacted.
    """
    leaked = "wJalrXUtnFEMIK7MDENGbPxRfiCY"
    assert (
        redact_text(f"password aws_secret_access_key {leaked}") == "password [REDACTED] [REDACTED]"
    )
    assert redact_text(f"secret password {leaked}") == "secret [REDACTED] [REDACTED]"
    assert redact_text("password secretpass") == "password [REDACTED]"
    assert redact_text("password secretpass token abc") == "password [REDACTED] token [REDACTED]"
    assert (
        redact_text('password secret "token": abc123') == 'password [REDACTED] "token": [REDACTED]'
    )
    assert "REPRSECRET" not in redact_text(
        "private_key=SECRETPRIVATE payload={'secret_access_key': 'REPRSECRET'} tail"
    )
    for text, secret in (
        ("invalid password Password123 for user bob", "Password123"),
        ("aws s3 --password MyToken1 --region us-east-1", "MyToken1"),
        ("password aws_secret_access_key: wJalr", "wJalr"),
    ):
        assert secret not in redact_text(text), text


def test_redact_text_redacts_an_authorization_scheme_without_a_delimiter() -> None:
    """``Authorization Bearer <token>`` has no ``:`` or ``=``, so it passed through.

    The credential follows a known scheme, so the header is matched by its scheme.
    Prose that mentions the header, with no scheme after it, is left unchanged.
    """
    assert redact_text("Authorization Bearer abc123") == "Authorization Bearer [REDACTED]"
    assert redact_text("Authorization Basic dXNlcjpwYXNz") == "Authorization Basic [REDACTED]"
    assert redact_text("Authorization header missing for user") == (
        "Authorization header missing for user"
    )


def test_redact_text_redacts_a_credential_scheme_after_a_sensitive_key() -> None:
    """A credential scheme after a sensitive key must not expose the credential.

    ``Authorization: token <PAT>`` is the GitHub form, and ``Token`` was missing from
    the scheme list. ``api_key: Bearer ...`` and ``headers.Authorization: Bearer ...``
    have a non-bare key, which the header pattern does not match. The scanner
    redacts the scheme and the credential after it. A bare ``Authorization`` header
    whose scheme the header pass already redacted keeps its existing output.
    """
    assert redact_text("Authorization: token ghp_abc123") == "Authorization: token [REDACTED]"
    assert redact_text("api_key: Bearer sk-live-abc123") == "api_key: [REDACTED] [REDACTED]"
    assert "abc123" not in redact_text("headers.Authorization: Bearer abc123")
    assert redact_text("Authorization: Bearer SECRET") == "Authorization: Bearer [REDACTED]"


def test_redact_text_redacts_a_quoted_authorization_value_as_one_unit() -> None:
    """A quoted Authorization value with a scheme leaked its credential.

    The unquoted pattern split the value on whitespace, so the base64 credential
    after the quoted scheme was printed. The whole quoted value is redacted now.
    """
    assert redact_text('Authorization: "Basic dXNlcjpwYXNz"') == "Authorization: [REDACTED]"
    assert "abc" not in redact_text('Authorization: "Bearer abc def"')
    assert "def" not in redact_text('Authorization: "Bearer abc def"')
    assert redact_text("Authorization: Bearer abc123") == "Authorization: Bearer [REDACTED]"


def test_redact_text_redacts_a_nested_credential_under_a_sensitive_key() -> None:
    """A sensitive key whose value is an object printed the object's secret.

    The unquoted value ran to the first space, which was inside "{'SecretAccessKey':", so the
    rest of the object was scanned as ordinary text and the secret was printed.
    """
    out = redact_text("{'Credentials': {'SecretAccessKey': 'wJalrSECRET'}}")

    assert "wJalrSECRET" not in out
    assert "SecretAccessKey" not in out


def test_redact_text_redacts_a_truncated_container_value_to_the_line_end() -> None:
    out = redact_text('token={"x": "SECRET3"\nnext line stays')

    assert "SECRET3" not in out
    assert "next line stays" in out

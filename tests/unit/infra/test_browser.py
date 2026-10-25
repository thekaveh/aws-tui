from __future__ import annotations

from unittest.mock import Mock

import pytest

from aws_tui.domain.emr_dashboard import validate_dashboard_url
from aws_tui.infra.browser import DisabledBrowser, NativeBrowser

URL = "https://dashboard.amazonaws.com/private?secret=token#fragment"


def test_native_dispatches_exact_validated_url(monkeypatch):
    native = Mock(return_value=True)
    monkeypatch.setattr("webbrowser.open", native)
    assert NativeBrowser(validate_dashboard_url).open(URL) is True
    native.assert_called_once_with(URL, new=2)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/x",
        "file:///tmp/x",
        URL + "\n",
        "https://amazonaws.com/x",
        "https://x.amazonaws.com:444/x",
        object(),
    ],
)
def test_native_refuses_invalid_url_without_dispatch(monkeypatch, url):
    native = Mock()
    monkeypatch.setattr("webbrowser.open", native)
    assert NativeBrowser(validate_dashboard_url).open(url) is False
    native.assert_not_called()


@pytest.mark.parametrize("result", [False, RuntimeError(URL)])
def test_native_failures_are_false_and_private(monkeypatch, result, caplog):
    native = (
        Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
    )
    monkeypatch.setattr("webbrowser.open", native)
    port = NativeBrowser(validate_dashboard_url)
    assert port.open(URL) is False
    assert URL not in repr(port) + caplog.text


def test_disabled_never_dispatches(monkeypatch):
    native = Mock()
    monkeypatch.setattr("webbrowser.open", native)
    assert DisabledBrowser().open(URL) is False
    native.assert_not_called()


def test_native_requires_validator_injection():
    with pytest.raises(TypeError):
        NativeBrowser()

"""Explicit browser dispatch with a strict, private URL boundary."""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from typing import Protocol


class BrowserPort(Protocol):
    """Synchronous dispatch; callers must offload blocking implementations."""

    def open(self, url: str) -> bool: ...


class NativeBrowser:
    """Report only whether the OS accepted an open request, never page load."""

    __slots__ = ("_validate",)

    def __init__(self, validate: Callable[[object], str]) -> None:
        self._validate = validate

    def open(self, url: str) -> bool:
        try:
            return webbrowser.open(self._validate(url), new=2) is True
        except Exception:
            # Browser failures can contain the full signed URL. Do not retain
            # or render the exception, even its class name from an injected port.
            return False
        finally:
            url = ""


class DisabledBrowser:
    """Demo/headless policy: no native dispatch."""

    __slots__ = ()

    def open(self, url: str) -> bool:
        return False


__all__ = ["BrowserPort", "DisabledBrowser", "NativeBrowser"]

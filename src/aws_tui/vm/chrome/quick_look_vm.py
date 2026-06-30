"""QuickLookVM — modal file-preview overlay.

The VM owns navigation state (``scroll_offset``, ``find_query``) and exposes
commands the view layer binds to. The actual streamed bytes live on
:class:`QuickLookContent`; consuming them is the view layer's job (so we
don't pull file-I/O concerns into the VM tier).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from vmx import (
    ComponentVM,
    Message,
    MessageHub,
    PropertyChangedMessage,
    RelayCommand,
    RelayCommandOf,
)
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher


@dataclass(frozen=True, slots=True)
class QuickLookContent:
    """Immutable preview payload.

    ``chunks`` is an async iterator over up-to-64-KB chunks the view layer
    consumes lazily on first render. We keep it ``None`` when the body is
    not yet known (e.g. a streaming preview that hasn't been kicked off).
    """

    title: str
    mime: str
    chunks: AsyncIterator[bytes] | None
    line_count_estimate: int | None


class QuickLookVM:
    """Reactive Quick Look viewmodel."""

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._hub: MessageHub[Message] = hub

        self._content: QuickLookContent | None = None
        self._is_open: bool = False
        self._scroll_offset: int = 0
        self._find_query: str = ""

        self._inner: ComponentVM = (
            ComponentVM.builder().name("quick_look").services(hub, dispatcher).build()
        )

        self._open_command: RelayCommandOf[QuickLookContent] = (
            RelayCommandOf[QuickLookContent]
            .builder()
            .predicate(lambda _content: True)
            .task(self._open)
            .build()
        )
        self._close_command: RelayCommand = (
            RelayCommand.builder().predicate(lambda: self._is_open).task(self._close).build()
        )
        self._scroll_command: RelayCommandOf[int] = (
            RelayCommandOf[int]
            .builder()
            .predicate(lambda _delta: self._is_open)
            .task(self._scroll)
            .build()
        )
        self._find_command: RelayCommandOf[str] = (
            RelayCommandOf[str]
            .builder()
            .predicate(lambda _q: self._is_open)
            .task(self._find)
            .build()
        )

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def content(self) -> QuickLookContent | None:
        return self._content

    @property
    def scroll_offset(self) -> int:
        return self._scroll_offset

    @property
    def find_query(self) -> str:
        return self._find_query

    @property
    def open_command(self) -> RelayCommandOf[QuickLookContent]:
        return self._open_command

    @property
    def close_command(self) -> RelayCommand:
        return self._close_command

    @property
    def scroll_command(self) -> RelayCommandOf[int]:
        return self._scroll_command

    @property
    def find_command(self) -> RelayCommandOf[str]:
        return self._find_command

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._open_command.dispose()
        self._close_command.dispose()
        self._scroll_command.dispose()
        self._find_command.dispose()
        self._inner.dispose()

    # ── Command implementations ────────────────────────────────────────────

    def _open(self, content: QuickLookContent | None) -> None:
        if content is None:
            return
        self._set_content(content)
        self._set_scroll(0)
        self._set_find_query("")
        self._set_open(True)

    def _close(self) -> None:
        self._set_open(False)
        self._set_content(None)
        self._set_scroll(0)
        self._set_find_query("")

    def _scroll(self, delta: int | None) -> None:
        if delta is None:
            return
        new_offset = self._scroll_offset + delta
        new_offset = max(0, new_offset)
        if self._content is not None and self._content.line_count_estimate is not None:
            new_offset = min(new_offset, self._content.line_count_estimate)
        self._set_scroll(new_offset)

    def _find(self, query: str | None) -> None:
        self._set_find_query(query or "")

    # ── State helpers ──────────────────────────────────────────────────────

    def _set_open(self, value: bool) -> None:
        if self._is_open == value:
            return
        self._is_open = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "is_open"))

    def _set_content(self, value: QuickLookContent | None) -> None:
        if self._content is value:
            return
        self._content = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "content"))

    def _set_scroll(self, value: int) -> None:
        if self._scroll_offset == value:
            return
        self._scroll_offset = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "scroll_offset"))

    def _set_find_query(self, value: str) -> None:
        if self._find_query == value:
            return
        self._find_query = value
        self._hub.send(PropertyChangedMessage.create(self, self.name, "find_query"))


__all__ = ["QuickLookContent", "QuickLookVM"]

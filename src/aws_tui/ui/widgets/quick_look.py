"""QuickLook modal screen bound to :class:`QuickLookVM`.

Streamed preview of a file's first ~64 KB. The actual byte stream comes
from :class:`QuickLookContent.chunks` (an async iterator); we consume it
on mount and append text to the body.
"""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.infra.redaction import redact_text
from aws_tui.vm.chrome.quick_look_vm import QuickLookVM


class QuickLook(ModalScreen[None]):
    """Quick Look modal."""

    BINDINGS = [  # noqa: RUF012
        ("escape", "close", "Close"),
        ("space", "close", "Close"),
    ]

    def __init__(
        self,
        vm: QuickLookVM,
        *,
        hub: MessageHub[Message],
    ) -> None:
        super().__init__()
        self._vm: QuickLookVM = vm
        self._hub: MessageHub[Message] = hub

    @property
    def vm(self) -> QuickLookVM:
        return self._vm

    def compose(self) -> ComposeResult:
        with Container():
            content = self._vm.content
            title = content.title if content else "(no preview)"
            # ``markup=False`` on both Statics: title is a path / S3
            # key (may legally contain ``[…]``), and the body Static
            # below is .update()'d in on_mount with raw file bytes
            # — JSON, source, logs, TOML all routinely contain
            # bracket sequences Rich would parse as style tags and
            # crash on. Static.update() re-runs the parent's markup
            # setting, so ``markup=False`` here also disables markup
            # on every subsequent update().
            yield Static(title, classes="quicklook-title", markup=False)
            with VerticalScroll(id="quicklook-body-scroll"):
                yield Static(
                    "loading...", id="quicklook-body", classes="quicklook-body", markup=False
                )

    def on_mount(self) -> None:
        self.run_worker(
            self._load_preview(),
            group="quick-look-preview",
            exclusive=True,
            exit_on_error=False,
        )

    async def _load_preview(self) -> None:
        content = self._vm.content
        if content is None or content.chunks is None:
            return
        body = self.query_one("#quicklook-body", Static)
        buf = bytearray()
        chunks = content.chunks
        try:
            async for chunk in chunks:
                buf.extend(chunk)
                if len(buf) >= 64 * 1024:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            body.update(f"preview unavailable: {redact_text(str(exc))}")
            return
        finally:
            # Close the underlying file handle / S3 stream deterministically
            # when we break out at the 64 KiB cap, rather than waiting for
            # the generator to be GC'd. ``FileSystemProvider.read_stream`` is
            # typed as ``AsyncIterator[bytes]`` (no formal aclose), but every
            # concrete impl returns an async generator that does.
            aclose = getattr(chunks, "aclose", None)
            if aclose is not None:
                await aclose()
        # ``bytes.decode("utf-8", errors="replace")`` substitutes
        # U+FFFD for any invalid sequence — it cannot raise. The
        # previous ``except Exception`` + ``repr(bytes(buf))`` fallback
        # was dead code.
        body.update(buf.decode("utf-8", errors="replace"))

    def on_unmount(self) -> None:
        self.workers.cancel_group(self, "quick-look-preview")

    def action_close(self) -> None:
        self._vm.close_command.execute()
        self.dismiss(None)


__all__ = ["QuickLook"]

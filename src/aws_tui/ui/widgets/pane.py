"""Pane widget — single Norton-Commander column bound to :class:`PaneVM`.

MVVM with one explicit exception: column widths are computed in the
view layer because they depend on the actual rendered Pane width
(an intrinsically view-side measurement). The VM still owns every
user-visible string — the view just decides how much horizontal space
to give each column at the current Pane size.

Flicker discipline: cursor moves do NOT re-mount the entry list. Each
:class:`EntryRow` subscribes to its own :class:`EntryVM` on the hub and
self-refreshes on ``is_selected`` / ``is_marked`` changes. The Pane only
re-renders the body when ``entries`` or ``state`` change.
"""

from __future__ import annotations

from rich.markup import escape as _markup_escape
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.events import Resize
from textual.widget import Widget
from textual.widgets import Static
from vmx import Message, MessageHub

from aws_tui.ui.widgets._subscriber import HubSubscriberMixin
from aws_tui.vm.file_manager.entry_vm import EntryVM
from aws_tui.vm.file_manager.pane_vm import PaneVM


def _truncate(text: str, max_width: int) -> str:
    """Right-trim with an ellipsis when ``text`` overflows ``max_width``.

    Duplicates the small helper in :mod:`entry_vm` so the view layer
    doesn't reach into a private symbol of the VM module."""
    if len(text) <= max_width:
        return text
    if max_width <= 1:
        return text[:max_width]
    return text[: max_width - 1] + "…"


# Fixed-width columns. NAME is adaptive (fills remaining space); these
# two stay constant because their content (formatted size string, ISO
# timestamp) has a known maximum.
_SIZE_COL_WIDTH = 10
_MODIFIED_COL_WIDTH = 16

# Non-name pixels per row: cursor(1) + mark(1) + sep(1) + sep(1) + 2
# extra spaces between size and modified = 6, plus size(10) + modified(16)
# = 32 total fixed cost. NAME gets whatever's left.
_FIXED_ROW_COST = 6 + _SIZE_COL_WIDTH + _MODIFIED_COL_WIDTH  # 32

# Width subtracted from Pane.size.width to account for the surrounding
# border (1 char each side) and the VerticalScroll's scrollbar gutter.
_PANE_CHROME_PADDING = 3

# Soft bounds on the NAME column so very narrow / very wide panes still
# look reasonable.
_MIN_NAME_WIDTH = 12
_MAX_NAME_WIDTH = 64

# Initial value before the first Resize event fires — covers a typical
# two-pane split on a ~120-col terminal.
_DEFAULT_NAME_WIDTH = 24


def _name_width_for(pane_width: int) -> int:
    """Compute the NAME column width that, together with the two fixed
    columns, fills the usable pane content area."""
    usable = max(0, pane_width - _PANE_CHROME_PADDING)
    raw = usable - _FIXED_ROW_COST
    return max(_MIN_NAME_WIDTH, min(_MAX_NAME_WIDTH, raw))


def _column_header_for(name_width: int) -> str:
    """Header text mirroring the row layout for the given NAME width."""
    name = f"{'NAME':<{name_width}}"
    size = f"{'SIZE':>{_SIZE_COL_WIDTH}}"
    modified = f"{'MODIFIED':<{_MODIFIED_COL_WIDTH}}"
    return f"   {name} {size}  {modified}"


class EntryRow(HubSubscriberMixin, Widget):
    """One entry row in a pane — bound to a single :class:`EntryVM`.

    Subscribes to the entry's own ``is_selected``/``is_marked`` changes so
    cursor moves and multi-select toggles update *this* row in place
    instead of triggering a body re-mount on the parent pane.

    Column widths are read from the parent Pane (which tracks its actual
    rendered size via :meth:`Pane.on_resize`), so NAME expands on wide
    panes and contracts on narrow ones while SIZE/MODIFIED stay visible.
    """

    DEFAULT_CSS = """
    EntryRow {
        height: 1;
    }
    """

    def __init__(
        self,
        entry_vm: EntryVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        merged = " ".join(c for c in (classes, "entry-row") if c)
        super().__init__(id=id, classes=merged)
        self._entry_vm = entry_vm
        self._hub = hub

    @property
    def entry_vm(self) -> EntryVM:
        return self._entry_vm

    def render(self) -> Text:
        vm = self._entry_vm
        host = self._find_pane()
        name_width = host.name_column_width if host is not None else _DEFAULT_NAME_WIDTH
        # No inline style on the cursor bar: the row's CSS class
        # (``-selected``) drives the color so theme swaps take effect
        # everywhere — including the bar — without re-rendering Python.
        name_str = f"{_truncate(vm.display_name, name_width):<{name_width}}"
        size_str = f"{vm.size_display:>{_SIZE_COL_WIDTH}}"
        modified_str = f"{vm.modified_display:<{_MODIFIED_COL_WIDTH}}"
        text = Text()
        text.append(vm.cursor_glyph)
        text.append(f"{vm.mark_glyph} {name_str} {size_str}  {modified_str}")
        return text

    def on_mount(self) -> None:
        self._apply_state_classes()
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._entry_vm,
            on_property_changed=self._on_entry_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    def _on_entry_changed(self, property_name: str) -> None:
        if property_name in ("is_selected", "is_marked"):
            self._apply_state_classes()
            self.refresh()

    async def on_click(self, event: object) -> None:
        """Click handling:

        - **Shift+click**: toggle the row's marked flag (multi-select).
          Cursor is also moved to the row so subsequent shift+clicks
          extend from a known anchor.
        - First click without shift: switch pane focus + move cursor.
        - Click on the already-selected row: delegates to
          :meth:`PaneVM.activate`.
        """
        host = self._find_pane()
        if host is None:
            return

        await host.on_click(event)

        filtered = host.vm.filtered_entries
        try:
            target_index = filtered.index(self._entry_vm)
        except ValueError:
            return

        # Modifier-click → multi-select toggle. We accept Shift, Meta
        # (Cmd on macOS), or Ctrl as the modifier because most macOS
        # terminals reserve Shift+Click for native text-selection and
        # never forward it to the app — Cmd+Click is the reliable path
        # there. The ".." parent link is not markable.
        modifier_pressed = bool(
            getattr(event, "shift", False)
            or getattr(event, "meta", False)
            or getattr(event, "ctrl", False)
        )
        if modifier_pressed and not self._entry_vm.is_parent_link:
            host.vm.move_cursor_to(target_index)
            host.vm.toggle_mark_at(target_index)
            return

        if not self._entry_vm.is_selected:
            host.vm.move_cursor_to(target_index)
            return

        await host.vm.activate(target_index)

    def _apply_state_classes(self) -> None:
        """Sync CSS classes to mirror VM flags (purely cosmetic)."""
        if self._entry_vm.is_selected:
            self.add_class("-selected")
        else:
            self.remove_class("-selected")
        if self._entry_vm.is_marked:
            self.add_class("-marked")
        else:
            self.remove_class("-marked")
        if self._entry_vm.is_directory:
            self.add_class("-dir")
        else:
            self.remove_class("-dir")

    def _find_pane(self) -> Pane | None:
        node: object | None = self
        while node is not None:
            if isinstance(node, Pane):
                return node
            node = getattr(node, "parent", None)
        return None


# Property names on PaneVM that warrant a full body re-mount (entry-list
# identity changed or placeholder swap). Cursor moves and viewmodel-only
# updates are handled by per-row subscriptions + chrome updates.
_BODY_REFRESH_PROPS: frozenset[str] = frozenset({"entries", "state", "path"})

# Property names that only require updating the breadcrumb / header /
# footer Static widgets — cheap, no re-mount.
_CHROME_REFRESH_PROPS: frozenset[str] = frozenset({"viewmodel", "filter_text"})

# Property names that just need the cursor to be scrolled into view (no
# re-mount, no Static update). The per-row hub subs handle the actual
# selected/unselected redraw — we only need to keep the row on-screen.
_SCROLL_TRACK_PROPS: frozenset[str] = frozenset({"cursor_index"})


class Pane(HubSubscriberMixin, Widget):
    """Single file-manager pane."""

    # Theme tokens ($text-dim etc) live in the theme .tcss files, not in
    # DEFAULT_CSS — the latter parses before the theme overlay loads.
    DEFAULT_CSS = """
    Pane {
        layout: vertical;
        height: 1fr;
        border-title-align: left;
    }
    """

    def __init__(
        self,
        vm: PaneVM,
        *,
        hub: MessageHub[Message],
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._vm: PaneVM = vm
        self._hub: MessageHub[Message] = hub
        # Recomputed on every Resize. EntryRow.render reads this directly
        # so wider terminals get wider NAME columns automatically.
        self._name_column_width: int = _DEFAULT_NAME_WIDTH

    @property
    def vm(self) -> PaneVM:
        return self._vm

    @property
    def name_column_width(self) -> int:
        return self._name_column_width

    def compose(self) -> ComposeResult:
        vm = self._vm.viewmodel
        # The inline ``.breadcrumb`` Static is intentionally absent — the
        # same path is rendered in the pane's top border title and
        # showing it twice was redundant.
        yield Static(_column_header_for(self._name_column_width), classes="column-header")
        # VerticalScroll instead of Vertical so long listings scroll on
        # mousewheel / trackpad without extra wiring, and so the cursor
        # can be scrolled into view via scroll_to_widget().
        yield VerticalScroll(id="pane-body")
        yield Static(vm.summary, classes="pane-footer")

    def on_mount(self) -> None:
        self._apply_border_title()
        # ``_render_body`` calls ``body.mount(...)`` on the ``#pane-body``
        # VerticalScroll yielded by ``compose``. Textual mounts children
        # asynchronously AFTER the parent's ``on_mount`` returns, so
        # calling ``body.mount`` synchronously here raises
        # ``MountError: Can't mount widget(s) before
        # VerticalScroll(id='pane-body') is mounted`` whenever the Pane
        # is mounted dynamically (e.g. via ``host.mount(DualPane(...))``
        # from ``AwsTuiApp._mount_initial_service_view``) AND the pane
        # lands in a non-IDLE state at boot (any placeholder branch in
        # ``_render_body`` has something to mount — UNREACHABLE,
        # FORBIDDEN, AUTH_REQUIRED, EMPTY, LOADING, ERROR).
        #
        # The user-visible trigger: an S3-compatible connection whose
        # endpoint is offline at app start (MinIO not running, etc.).
        #
        # Deferring to the next refresh tick lets Textual finish
        # mounting ``pane-body`` first. Matches the pattern every other
        # ``_render_body`` caller in this class uses
        # (``_on_vm_property_changed`` always goes through
        # ``call_after_refresh``).
        self.call_after_refresh(self._render_body)
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            on_property_changed=self._on_vm_property_changed,
        )

    def on_unmount(self) -> None:
        self.unsubscribe_from_vm()

    def on_resize(self, event: Resize) -> None:
        """Recompute NAME column width on resize and reflow the visible
        rows + header to fill the new pane width."""
        new_width = _name_width_for(event.size.width)
        if new_width == self._name_column_width:
            return
        self._name_column_width = new_width
        # Update the header Static; existing EntryRow widgets pick up
        # the new width on their next refresh, which we trigger here.
        self.call_after_refresh(self._reflow_columns)

    def _reflow_columns(self) -> None:
        try:
            header = self.query_one(".column-header", Static)
        except Exception:
            return
        header.update(_column_header_for(self._name_column_width))
        for row in self.query(EntryRow):
            row.refresh()

    def set_focused(self, value: bool) -> None:
        if value:
            self.add_class("-focused")
        else:
            self.remove_class("-focused")

    async def on_click(self, _event: object) -> None:
        """Clicking anywhere in a pane switches focus to it (when applicable)."""
        node: object | None = self
        while node is not None:
            if type(node).__name__ == "DualPane":
                dual_vm = getattr(node, "vm", None)
                if dual_vm is None:
                    return
                from aws_tui.vm.file_manager.dual_pane_vm import FocusedPane

                want = FocusedPane.LEFT if self._vm is dual_vm.left else FocusedPane.RIGHT
                if dual_vm.focused is not want:
                    dual_vm.switch_focus_command.execute()
                return
            node = getattr(node, "parent", None)

    # ── Internal ────────────────────────────────────────────────────────────

    def _apply_border_title(self) -> None:
        """Reflect the VM's live path + identity into the pane border.

        - ``border_title`` (top): the path, updates on every navigation.
        - ``border_subtitle`` (bottom): the connection identity (S3 only).

        Textual's ``_BorderTitle`` descriptor unconditionally runs the
        value through ``Content.from_markup``; there is NO per-widget
        knob to disable that. A path or S3 key containing ``[…]``
        (``/Users/me/[draft]``, ``releases[2025]/``, an
        ``s3-compatible`` connection named ``prod[us-east]``) would
        crash the render with ``MarkupError`` — escape the values
        before assignment so brackets render as literal text.
        """
        vm = self._vm.viewmodel
        self.border_title = _markup_escape(vm.border_title)
        if vm.border_subtitle is not None:
            self.border_subtitle = _markup_escape(vm.border_subtitle)

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name in _BODY_REFRESH_PROPS:
            self.call_after_refresh(self._refresh_all)
        elif property_name in _CHROME_REFRESH_PROPS:
            self.call_after_refresh(self._refresh_chrome)
        elif property_name in _SCROLL_TRACK_PROPS:
            self.call_after_refresh(self._scroll_to_cursor)

    def _scroll_to_cursor(self) -> None:
        """Keep the selected row inside the visible viewport. No-ops if
        the body is in a placeholder state or the row is already visible.
        """
        try:
            body = self.query_one("#pane-body", VerticalScroll)
        except Exception:
            return
        target_vm = self._vm.selected_entry
        if target_vm is None:
            return
        for row in body.query(EntryRow):
            if row.entry_vm is target_vm:
                body.scroll_to_widget(row, animate=False)
                return

    def _refresh_chrome(self) -> None:
        """Update header / footer Statics in place — no remount."""
        try:
            header = self.query_one(".column-header", Static)
            footer = self.query_one(".pane-footer", Static)
        except NoMatches:
            return
        vm = self._vm.viewmodel
        # Header always uses the adaptive width — VM's column_header_text
        # field stays as a fallback for non-Pane consumers.
        header.update(_column_header_for(self._name_column_width))
        footer.update(vm.summary)
        self._apply_border_title()

    def _refresh_all(self) -> None:
        self._refresh_chrome()
        self._render_body()

    def _render_body(self) -> None:
        try:
            body = self.query_one("#pane-body", VerticalScroll)
        except Exception:
            return
        for child in list(body.children):
            child.remove()

        vm = self._vm.viewmodel
        if vm.placeholder_text is not None:
            placeholder_class = "pane-placeholder"
            if vm.placeholder_severity:
                placeholder_class = f"{placeholder_class} -{vm.placeholder_severity}"
            # ``markup=False``: vm.placeholder_text appends
            # ``_error_text = str(exc)`` for non-IDLE states.
            # FileNotFoundError stringifies as "[Errno 2] No such
            # file or directory: ..." — the leading "[" triggers
            # Rich's markup parser and crashes the pane render.
            # Same guard JobRunDetailPane / JobRunLogsPane already
            # apply for the same PaneState machine; the
            # file-manager pane was missed in the R24 sweep.
            body.mount(Static(vm.placeholder_text, classes=placeholder_class, markup=False))
            return

        for entry_vm in self._vm.filtered_entries:
            row = EntryRow(entry_vm, hub=self._hub)
            body.mount(row)


__all__ = ["EntryRow", "Pane"]

"""Pane widget — single Norton-Commander column bound to :class:`PaneVM`.

MVVM with one explicit exception: column widths are computed in the
view layer because they depend on the actual rendered Pane width
(an intrinsically view-side measurement). The VM still owns every
user-visible string — the view just decides how much horizontal space
to give each column at the current Pane size.

Flicker discipline: cursor moves do NOT re-mount the entry list. The
Pane holds the only hub subscription and drives each :class:`EntryRow`
imperatively via :meth:`EntryRow.sync_state`; the rows carry no
subscription of their own, so the hub's observer count stays constant
instead of growing by two per entry. The Pane only re-renders the body
when ``entries`` or ``state`` change.
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


class EntryRow(Widget):
    """One entry row in a pane — bound to a single :class:`EntryVM`.

    Deliberately NOT a hub subscriber. A listing mounts one row per entry, so
    a per-row subscription made the hub's observer count ``6 + 2N`` and turned
    every property change into O(N) observer callbacks. The owning
    :class:`Pane` holds the single subscription and pushes state down through
    :meth:`sync_state`, which still updates *this* row in place instead of
    triggering a body re-mount on the parent pane.

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
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        merged = " ".join(c for c in (classes, "entry-row") if c)
        super().__init__(id=id, classes=merged)
        self._entry_vm = entry_vm
        self._tooltip_text: str | None = None
        # Last (is_selected, is_marked) pushed in by the Pane. ``None`` until
        # the first paint so the early-return in ``sync_state`` cannot swallow
        # it. Primed in ``on_mount``.
        self._state_cache: tuple[bool, bool] | None = None

    @property
    def entry_vm(self) -> EntryVM:
        return self._entry_vm

    def _sync_tooltip(self, full_name: str | None) -> None:
        """Attach the untruncated name, with the key that copies its path.

        A copy affordance *inside* the tooltip is not achievable: Textual's
        ``Tooltip`` is a ``Static`` and cannot host an interactive child, and
        ``Screen._maybe_clear_tooltip`` dismisses it as soon as the widget
        under the pointer stops being this row -- so moving the mouse toward a
        button drawn in it would destroy it first. Naming the keybinding is the
        honest alternative to drawing a control that cannot be clicked.

        It names the *cursor* entry rather than this row: ``p``
        (``pane.copy_entry_path``) copies whatever the cursor is on, and the
        pointer can rest on a row the cursor has not reached. The earlier
        "its path" implied the hovered row and was wrong whenever those two
        differed.
        """
        text = (
            None if full_name is None else f"{full_name}\n\npress p to copy the cursor entry's path"
        )
        if text == self._tooltip_text:
            return
        self._tooltip_text = text
        self.tooltip = text

    def render(self) -> Text:
        vm = self._entry_vm
        host = self._find_pane()
        name_width = host.name_column_width if host is not None else _DEFAULT_NAME_WIDTH
        # No inline style on the cursor bar: the row's CSS class
        # (``-selected``) drives the color so theme swaps take effect
        # everywhere — including the bar — without re-rendering Python.
        shown = _truncate(vm.display_name, name_width)
        # Only offer a tooltip when the column actually hid something; a
        # tooltip repeating a fully visible name is noise on every row.
        self._sync_tooltip(vm.display_name if shown != vm.display_name else None)
        name_str = f"{shown:<{name_width}}"
        size_str = f"{vm.size_display:>{_SIZE_COL_WIDTH}}"
        modified_str = f"{vm.modified_display:<{_MODIFIED_COL_WIDTH}}"
        text = Text()
        text.append(vm.cursor_glyph)
        text.append(f"{vm.mark_glyph} {name_str} {size_str}  {modified_str}")
        return text

    def on_mount(self) -> None:
        self._apply_state_classes()
        vm = self._entry_vm
        self._state_cache = (vm.is_selected, vm.is_marked)

    def sync_state(self, *, is_selected: bool, is_marked: bool) -> None:
        """Imperative mirror of the VM flags, driven by the owning Pane.

        The ``refresh()`` is NOT optional. :meth:`render` draws the VM's
        ``cursor_glyph`` and ``mark_glyph``, and Textual does not repaint
        content when a class changes — so a class flip alone would leave the
        cursor bar and the ``*`` painted on the wrong row. This is the same
        contract ``NavRow.set_selected`` documents.

        Never touches ``-alt``: the zebra stripe is assigned once, by
        :meth:`Pane._render_body`, from the row's position in the listing.
        """
        if self._state_cache == (is_selected, is_marked):
            return
        self._state_cache = (is_selected, is_marked)
        self.set_class(is_selected, "-selected")
        self.set_class(is_marked, "-marked")
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
        """Sync CSS classes to mirror VM flags (purely cosmetic).

        The initial-paint path only; every later change comes through
        :meth:`sync_state`. Like that method it must never touch ``-alt``.
        """
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
# ``filter_text`` belongs here, not in the chrome set: ``_render_body`` mounts
# ``vm.filtered_entries``, so a chrome-only refresh left non-matching rows
# mounted and clickable while the VM's cursor indices addressed the filtered
# list — the user saw one listing and the VM addressed another.
# ``_refresh_all`` updates chrome as well, so nothing is lost by promoting it.
_BODY_REFRESH_PROPS: frozenset[str] = frozenset({"entries", "state", "path", "filter_text"})

# Property names that only require updating the breadcrumb / header /
# footer Static widgets — cheap, no re-mount.
_CHROME_REFRESH_PROPS: frozenset[str] = frozenset({"viewmodel"})

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
        self._path_tooltip_text: str | None = None
        # Mounted rows in filtered order. The Pane owns their state because
        # the rows no longer subscribe to the hub themselves.
        self._rows: list[EntryRow] = []
        self._cursor_row: int | None = None
        self._body_refresh_pending: bool = False

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
        # mounting ``pane-body`` first. Reconcile chrome as well as the
        # body after subscribing: a fast setup can update the VM after
        # ``compose`` captures its initial values but before this mount
        # callback installs the subscription.
        #
        # This matches the pattern every other
        # ``_render_body`` caller in this class uses
        # (``_on_vm_property_changed`` always goes through
        # ``call_after_refresh``).
        self.subscribe_to_vm(
            hub=self._hub,
            vm=self._vm,
            property_names=(
                *_BODY_REFRESH_PROPS,
                *_CHROME_REFRESH_PROPS,
                *_SCROLL_TRACK_PROPS,
            ),
            on_property_changed=self._on_vm_property_changed,
        )
        self.call_after_refresh(self._refresh_all)

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
        # ``self._rows`` rather than a query: same O(N) walk without the DOM
        # traversal, and it cannot pick up rows that are pending removal.
        for row in self._rows:
            row.refresh()

    def set_focused(self, value: bool) -> None:
        if value:
            self.add_class("-focused")
        else:
            self.remove_class("-focused")

    def _hand_to_app(self, value: str, label: str) -> None:
        """Hand ``value`` to the app's clipboard writer, if the host has one.

        The app owns the only honest clipboard report: it has the port that
        can tell a real write from an unacknowledged OSC 52, and it has the
        toast stack. A ``Pane`` has neither -- ``DualPane.compose`` gives it
        a view model and the hub and nothing else -- so it hands the value
        up and says nothing itself.

        Duck-typed on purpose, the existing idiom in this file (see
        ``on_click``'s ``type(node).__name__ == "DualPane"``): the pane is
        mounted under a bare ``App`` in several tests, and ``ui/`` may not
        import the composition root regardless.

        It passes the value rather than calling ``action_copy_path``,
        which resolves the *focused* pane. ``on_click``'s border branch
        returns before the focus-switch fall-through, so routing through the
        action would copy the other pane's path on a border click.
        """
        handler = getattr(self.app, "copy_value", None)
        if handler is not None:
            handler(value, label)

    def copy_current_path(self) -> None:
        """Copy this pane's location. Bound to the border affordance and a key."""
        self._hand_to_app(self._vm.viewmodel.copy_path, "path")

    def copy_selected_path(self) -> None:
        """Copy the cursor entry's full location, if there is one.

        Silent on the parent link and on an empty listing: the pane cannot
        raise a toast, and the keyboard path for this
        (``AwsTuiApp.action_copy_entry_path``) advises there instead.
        """
        target = self._vm.viewmodel.copy_selected_path
        if target is None:
            return
        self._hand_to_app(target, "file path")

    def on_mouse_move(self, event: object) -> None:
        """Offer the full path while the pointer is on the top border row.

        The border is chrome rather than a child widget, so it cannot carry its
        own tooltip. Textual does report pointer position relative to this
        widget over the border, so the tooltip is attached and withdrawn as the
        pointer enters and leaves row 0 -- otherwise it would appear anywhere
        over the pane, which is worse than not having it.

        The key leads and the click follows: ``P`` works from the keyboard
        with no pointer and no hunting for the one row that is a target,
        and it is the affordance the footer, the help overlay and the
        command palette all name. The click is still mentioned because the
        border carries no glyph any more -- drop the word and the surviving
        click target becomes undiscoverable.
        """
        offset = getattr(event, "offset", None)
        on_border = offset is not None and offset.y == 0
        vm = self._vm.viewmodel
        text = f"{vm.copy_path}\n\npress P to copy, or click here" if on_border else None
        if text != self._path_tooltip_text:
            self._path_tooltip_text = text
            self.tooltip = text

    def on_leave(self, _event: object) -> None:
        """Withdraw the path tooltip when the pointer leaves the pane.

        ``on_mouse_move`` cannot do this alone: moving from the border straight
        into the body puts the pointer over an ``EntryRow``, which consumes the
        event, so the pane never sees the departure. Textual shows the hovered
        widget's own tooltip rather than this one, so a stale value is not
        visible -- but leaving it set is untidy and would surface if the pointer
        later rested on the pane's own chrome.
        """
        if self._path_tooltip_text is not None:
            self._path_tooltip_text = None
            self.tooltip = None

    async def on_click(self, event: object) -> None:
        """Clicking anywhere in a pane switches focus to it (when applicable).

        A click on the top border row is the path-copy affordance. EntryRow
        delegates its own click here with a ROW-relative offset, where ``y`` is
        also 0, so a genuine border hit is identified by the event's own widget
        rather than by offset alone.
        """
        offset = getattr(event, "offset", None)
        if getattr(event, "widget", None) is self and offset is not None and offset.y == 0:
            self.copy_current_path()
            return
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
        # No glyph marks the path as copyable. The border title is the one
        # piece of chrome that truncates to the pane width, and a trailing
        # U+1F4CB CLIPBOARD spent two cells of it restating what the hover
        # tooltip, the footer, the help overlay and the command palette all
        # say in words -- while advertising a mouse-only affordance as if it
        # were the whole story.
        #
        # If a marker is ever wanted back here it must be an SMP
        # single-codepoint emoji such as U+1F4CB, which measures 2 cells. Do
        # NOT reach for a BMP symbol (U+2398, U+29C9) instead: those
        # measure 1, and this project already paid for that lesson through
        # PR #76 -> #77 -> #79, where BMP symbols with VS-16 came out as
        # 1-cell text outlines and broke the surrounding width maths.
        self.border_title = _markup_escape(vm.border_title)
        if vm.border_subtitle is not None:
            self.border_subtitle = _markup_escape(vm.border_subtitle)

    def _on_vm_property_changed(self, property_name: str) -> None:
        if property_name in _BODY_REFRESH_PROPS:
            # Coalesce: one ``navigate_to`` emits ``path``, ``state`` (LOADING),
            # ``entries`` and ``state`` (IDLE), and each of those used to
            # schedule its own full teardown + remount of every row. The flag
            # collapses the burst into a single callback, which then observes
            # the FINAL VM state instead of four callbacks each observing an
            # intermediate one. ``_refresh_all`` clears it first thing, so a
            # notify arriving after the render begins still schedules a fresh
            # pass.
            if self._body_refresh_pending:
                return
            self._body_refresh_pending = True
            self.call_after_refresh(self._refresh_all)
        elif property_name in _CHROME_REFRESH_PROPS:
            self.call_after_refresh(self._refresh_chrome)
        elif property_name in _SCROLL_TRACK_PROPS:
            self.call_after_refresh(self._apply_cursor)

    def _apply_cursor(self) -> None:
        """Repaint the row the cursor left and the row it reached, then scroll.

        O(1) in rows: the previous cursor position is remembered, so a
        keystroke touches at most two widgets instead of walking the body.

        The flags are read back off ``entry_vm`` instead of being assumed to
        be ``True``/``False``: ``PaneVM._sync_cursor_selection`` has already
        run synchronously before the ``cursor_index`` notify, so the VM is
        authoritative and this stays an exact mirror — which is also what
        leaves a marked row's ``-marked`` intact as the cursor passes over it.
        """
        if not self._rows:
            self._cursor_row = None
            return
        new = self._vm.cursor_index
        if not (0 <= new < len(self._rows)):
            return
        old = self._cursor_row
        if old is not None and old != new and old < len(self._rows):
            prev = self._rows[old]
            prev.sync_state(
                is_selected=prev.entry_vm.is_selected,
                is_marked=prev.entry_vm.is_marked,
            )
        self._cursor_row = new
        row = self._rows[new]
        row.sync_state(is_selected=row.entry_vm.is_selected, is_marked=row.entry_vm.is_marked)
        try:
            self.query_one("#pane-body", VerticalScroll).scroll_to_widget(row, animate=False)
        except Exception:
            return

    def _sync_marks(self) -> None:
        """Mirror every row's mark/selection flags back off its own VM.

        Driven by the ``"viewmodel"`` notify, which every mark mutation already
        emits (``toggle_mark_at``, ``mark_at``, ``_toggle_select_cursor``,
        ``_select_all``, ``_clear_marks``). O(N) attribute reads with a per-row
        early return in ``sync_state``, so a bulk mark costs one repaint per
        row that actually changed and nothing for the rest.
        """
        for row in self._rows:
            vm = row.entry_vm
            row.sync_state(is_selected=vm.is_selected, is_marked=vm.is_marked)

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
        self._sync_marks()

    def _refresh_all(self) -> None:
        # FIRST statement, deliberately: everything below reads the VM, so a
        # notify that lands mid-render must be able to queue another pass.
        self._body_refresh_pending = False
        self._refresh_chrome()
        self._render_body()

    def _render_body(self) -> None:
        try:
            body = self.query_one("#pane-body", VerticalScroll)
        except Exception:
            return
        body.remove_children()
        self._rows = []
        self._cursor_row = None

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

        # ``-alt`` is the zebra stripe, assigned here and nowhere else.
        # An explicit class rather than ``:odd``/``:even``: the pseudo-class
        # selectors are catastrophically slow on long listings (3,000 rows
        # measured at 0.956 s -> 18.303 s).
        rows = [
            EntryRow(entry_vm, classes="-alt" if index % 2 else None)
            for index, entry_vm in enumerate(self._vm.filtered_entries)
        ]
        if rows:
            # ONE batched mount, not one call per row: ``Widget.mount``
            # schedules a ``update_styles`` callback over the parent's children
            # on every call, so the per-row loop was O(N^2) in scheduled
            # callbacks (3,000 rows measured at 3.022 s -> 1.100 s).
            body.mount(*rows)
        self._rows = rows
        self._apply_cursor()
        self._sync_marks()


__all__ = ["EntryRow", "Pane"]

"""In-process integration tests for the nav-routed Settings flow."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from aws_tui.app import AwsTuiApp
from aws_tui.composition import build_app_context
from aws_tui.infra.config_store import ConfigStore

_MINIO_LOCAL_TOML = (
    "[connections.minio-local]\n"
    'kind = "s3-compatible"\n'
    'endpoint_url = "http://127.0.0.1:1"\n'  # unreachable on purpose
    'region = "us-east-1"\n'
    'access_key_id = "AKIATEST"\n'
    'secret_access_key = "SECRETTEST"\n'
    "force_path_style = true\n"
    "verify_tls = false\n"
)


def _prep(tmp_path: Path, toml_text: str = "") -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(toml_text)
    return config_dir


async def _await_boot(pilot: object, app: object) -> None:
    """Wait for the initial-mount worker to clear ``_boot_in_flight``.

    Tests previously relied on a single ``pilot.pause()`` because
    ``on_mount`` did the initial mount synchronously — it awaited
    ``switch_service`` inline, which awaited ``PaneVM.setup`` →
    ``S3FS.list`` → boto3 connection-refused (7-15s wall-clock on
    the unreachable ``127.0.0.1:1`` test endpoint). Slow tests but
    deterministic.

    The mount now runs in a worker spawned by ``on_mount`` so the
    chrome paints immediately, but a single ``pilot.pause()`` no
    longer reaches the post-mount state. We wait for the
    ``content-mount`` group of workers to drain instead.

    Then we dismiss any boot-chain narration toasts the worker
    raised — they dock right and would cover Settings-panel click
    targets the test then exercises. The narration is real-user
    UX; tests interact post-boot and don't need it.
    """
    await app.workers.wait_for_complete(  # type: ignore[attr-defined]
        list(app.workers._workers)  # type: ignore[attr-defined]
    )
    await pilot.pause()  # type: ignore[attr-defined]
    stack = app.app_ctx.root_vm.chrome.toast_stack  # type: ignore[attr-defined]
    for toast in tuple(stack.toasts):
        tid = toast.model.id
        if tid.startswith("boot-") or tid.startswith("initial-fallback-"):
            stack.dismiss(tid)
    await pilot.pause()  # type: ignore[attr-defined]


def _dispose(ctx: object) -> None:
    """Standard teardown — mirrors the pattern in build_app_context order."""
    for attr in [
        "s3_connections_vm",
        "transfers_vm",
        "confirm_vm",
        "quick_look_vm",
        "command_palette_vm",
    ]:
        v = getattr(ctx, attr, None)
        if v is not None and hasattr(v, "dispose"):
            with contextlib.suppress(Exception):
                v.dispose()
    root = getattr(ctx, "root_vm", None)
    if root is not None and hasattr(root, "dispose"):
        with contextlib.suppress(Exception):
            root.dispose()


@pytest.mark.asyncio
async def test_toggle_settings_s3_settings_does_not_crash(tmp_path: Path) -> None:
    """Regression: clicking Settings → S3 → Settings used to crash with
    ``StatusTransitionError('Cannot construct from state Disposed.')``.

    Root cause: ``ctx.settings_vm`` was a singleton; ``ContentHostVM``
    calls ``vm.dispose()`` on swap-out and ``vm.construct()`` on
    swap-in, so the second Settings click tried to re-construct an
    already-Disposed VM. Fix: build a fresh ``SettingsVM`` per mount.
    """
    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML)
    (config_dir / "config.toml").write_text(
        _MINIO_LOCAL_TOML + '\n[defaults]\nconnection = "minio-local"\n'
    )
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await _await_boot(pilot, app)
            menu = ctx.root_vm.services_menu
            # 1st: settings
            menu.switch_service_command.execute("settings")
            await pilot.pause()
            # 2nd: back to s3
            menu.switch_service_command.execute("s3")
            await pilot.pause()
            # 3rd: settings again — this was the crash path.
            menu.switch_service_command.execute("settings")
            await pilot.pause()

            from aws_tui.vm.settings.settings_vm import SettingsVM

            assert isinstance(ctx.root_vm.content_host.current, SettingsVM), (
                "Settings VM should be re-mounted; if the singleton was reused "
                "it would have been Disposed after the s3 swap and the second "
                "Settings switch would have crashed in ContentHostVM.set_content "
                "while calling vm.construct()."
            )
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_comma_selects_settings_and_swaps_main_area(tmp_path: Path) -> None:
    """Press comma → SettingsView becomes the ContentHost's current content."""
    config_dir = _prep(tmp_path)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await _await_boot(pilot, app)
            await pilot.press("comma")
            await pilot.pause()
            # NavMenuVM should now have settings selected.
            # ctx.root_vm.services_menu is the canonical accessor (NavMenuVM).
            assert ctx.root_vm.services_menu.selected_id == "settings"
            # ContentHost's current VM should be the SettingsVM.
            from aws_tui.vm.settings.settings_vm import SettingsVM

            assert isinstance(ctx.root_vm.content_host.current, SettingsVM)
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_add_inline_form_persists_to_toml(tmp_path: Path) -> None:
    """Open Settings → expand inline form → fill + Save → TOML round-trip."""
    config_dir = _prep(tmp_path)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await _await_boot(pilot, app)
            await pilot.press("comma")
            await pilot.pause()

            # The inline form widget should be mounted (hidden) within
            # the SettingsView's Connections panel. Open it.
            from aws_tui.ui.widgets.settings.connection_form import (
                ConnectionFormInline,
                ConnectionFormSubmitted,
            )
            from aws_tui.vm.chrome.first_run_vm import S3CompatForm

            form = pilot.app.query_one(ConnectionFormInline)
            form.open_for_add()
            await pilot.pause()

            # Fill programmatically (pilot.press char-by-char is flaky
            # under the textual test harness for Input widgets).
            from textual.widgets import Input

            pilot.app.query_one("#form-name", Input).value = "minio-test"
            pilot.app.query_one("#form-endpoint_url", Input).value = "http://localhost:9000"
            pilot.app.query_one("#form-region", Input).value = "us-east-1"
            pilot.app.query_one("#form-access_key_id", Input).value = "AKIATEST"
            pilot.app.query_one("#form-secret_access_key", Input).value = "SECRETTEST"
            await pilot.pause()

            # Post the submission event the form would emit on Save click.
            form_obj = S3CompatForm(
                name="minio-test",
                endpoint_url="http://localhost:9000",
                region="us-east-1",
                access_key_id="AKIATEST",
                secret_access_key="SECRETTEST",
                force_path_style=True,
                verify_tls=True,
            )
            form.post_message(
                ConnectionFormSubmitted(form=form_obj, mode="add", original_name=None)
            )
            await pilot.pause()
    finally:
        _dispose(ctx)

    cfg = ConfigStore(path=config_dir / "config.toml").load()
    assert "minio-test" in cfg.connections
    entry = cfg.connections["minio-test"]
    assert entry.endpoint_url == "http://localhost:9000"


@pytest.mark.asyncio
async def test_delete_via_confirm_removes_from_toml(tmp_path: Path) -> None:
    """Seed a connection → open Settings → click delete chip → confirm → TOML removed."""
    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await _await_boot(pilot, app)
            await pilot.press("comma")
            await app.workers.wait_for_complete(  # type: ignore[attr-defined]
                list(app.workers._workers)  # type: ignore[attr-defined]
            )
            await pilot.pause()
            await pilot.click("#delete-0")
            await pilot.pause()
            # ConfirmModal opens; danger defaults focus to Cancel — press
            # Right then Enter to confirm.
            await pilot.press("right")
            await pilot.press("enter")
            await pilot.pause()
    finally:
        _dispose(ctx)

    cfg = ConfigStore(path=config_dir / "config.toml").load()
    assert "minio-local" not in cfg.connections


@pytest.mark.asyncio
async def test_dual_pane_mounts_at_startup_without_blank_screen(tmp_path: Path) -> None:
    """Regression: startup with an S3 connection MUST render the DualPane in
    the content host. Without the ``_boot_in_flight`` guard on
    ``_on_nav_selection_changed``, the seed selected_id change that
    ``switch_service("s3")`` fires during ``on_mount`` would spawn a
    ``_mount_service_view`` worker that races against on_mount's own
    ``_mount_initial_service_view``. Both call ``host.remove_children()`` +
    ``host.mount()`` on the same container; whichever runs second silently
    clobbers the other and the user sees a blank screen at startup.

    This test asserts the DualPane is actually mounted (not just that the
    VM is set), which is the user-visible outcome.
    """
    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML)
    # Mark the seeded connection as the default so on_mount picks it up.
    (config_dir / "config.toml").write_text(
        _MINIO_LOCAL_TOML + '\n[defaults]\nconnection = "minio-local"\n'
    )
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")
    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await _await_boot(pilot, app)
            # Boot guard must release after on_mount completes.
            assert app._boot_in_flight is False
            # DualPane must be mounted in #content-host.
            from textual.containers import Container

            from aws_tui.ui.widgets.dual_pane import DualPane

            host = pilot.app.query_one("#content-host", Container)
            dual_panes = host.query(DualPane)
            assert len(dual_panes) == 1, (
                f"expected exactly one DualPane in #content-host but got "
                f"{len(dual_panes)} — the seed selected_id change may be "
                f"racing _mount_initial_service_view again"
            )
            # Content host MUST have non-zero rendered width.  Without
            # the AwsTuiApp.CSS rule giving ``#content-host`` an
            # explicit ``width: 1fr``, the Horizontal layout doesn't
            # allocate the remaining space (NavMenu starts collapsed
            # at width 0), so the content host renders at zero width
            # and the DualPane mounted inside is invisible — the
            # user-reported "blank screen at launch" symptom.
            assert host.region.width > 0, (
                "#content-host rendered at zero width — DualPane is "
                "mounted but invisible. AwsTuiApp.CSS must give "
                "#content-host an explicit width:1fr so it takes the "
                "space the collapsed (display:none) NavMenu leaves."
            )
    finally:
        _dispose(ctx)


@pytest.mark.asyncio
async def test_expired_sso_proactively_falls_back_to_local(tmp_path: Path) -> None:
    """Regression: when probe_token returns EXPIRED for the resolved AWS
    connection at startup, the app MUST mount a LocalFS-on-both-panes
    DualPane (not a giant error placeholder) and surface a sticky
    recovery toast — proactive graceful degradation.

    The history: PR #55 originally mounted an "auth required" Static
    placeholder when SSO was expired (because going through
    switch_service would block 15s on boto3 trying to refresh the
    expired token). User feedback after the v0.7 nav rework was that
    an error-message-instead-of-panes is worse UX than
    panes-with-graceful-fallback; this fix replaces the placeholder
    with a LocalFS-only DualPane + recovery-hint toast, and the
    boto3 wait is short-circuited entirely (no S3FS construction →
    no network call).

    The test asserts BOTH that switch_service is NOT called (which
    would block 15s) AND that a DualPane is actually mounted in
    #content-host (not the old static placeholder).
    """
    import contextlib as _contextlib

    from aws_tui.infra.aws_session import TokenProbeResult, TokenState
    from aws_tui.infra.connection_resolver import Connection
    from aws_tui.ui.widgets.dual_pane import DualPane

    config_dir = _prep(tmp_path)  # empty config — we'll seed via monkey-patch
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")

    # Force a single AWS-kind connection to be the resolved initial,
    # and force probe_token to return EXPIRED for it.
    fake_conn = Connection(
        name="dev-sso",
        kind="aws",
        region="us-east-1",
        source="auto-aws-profile",
        profile="dev-sso",
    )
    ctx.connection_resolver.list = lambda: [fake_conn]  # type: ignore[assignment,method-assign]
    ctx.aws_session.probe_token = lambda _c: TokenProbeResult(  # type: ignore[assignment,method-assign]
        state=TokenState.EXPIRED
    )

    # Track whether switch_service was called — that's the hang path.
    called: list[str] = []
    original_switch = ctx.root_vm.switch_service

    async def _watching_switch(service_id: str) -> None:
        called.append(service_id)
        await original_switch(service_id)

    ctx.root_vm.switch_service = _watching_switch  # type: ignore[assignment,method-assign]

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await _await_boot(pilot, app)
            assert called == [], (
                f"switch_service must NOT be called when probe_token returns "
                f"EXPIRED — the S3 build path drives S3FS.list → boto3 SSO "
                f"token refresh → 15s network hang. Got call(s): {called}"
            )
            # DualPane MUST be mounted (graceful degradation — no
            # error-message-instead-of-panes UX regression).
            host = pilot.app.query_one("#content-host")
            assert len(host.query(DualPane)) == 1, (
                "expected exactly one DualPane mounted in #content-host "
                "(local-only graceful fallback); got "
                f"{len(host.query(DualPane))}"
            )
            # Connection marked unreachable so Shift+S skips it until
            # the user runs aws sso login + relaunches.
            assert (fake_conn.kind, fake_conn.name) in ctx.unreachable_connections
    finally:
        with _contextlib.suppress(Exception):
            _dispose(ctx)


@pytest.mark.asyncio
async def test_boot_chain_with_mixed_failures_populates_both_panes(tmp_path: Path) -> None:
    """User report: AWS profile + an s3-compatible connection BOTH
    unavailable. After boot, neither pane shows content. After
    Settings → S3 toggle, the local source appears. The toggle path
    works because ``content_host.current_id`` is ``"settings"`` so
    ``set_content`` adopts the new local DualPaneVM. The boot path
    is broken because the failed s3-compatible attempt during the
    chain already set ``current_id = "s3"``, and the chain's
    closing ``_mount_local_only_dual_pane`` call asks for the same
    ``service_id="s3"`` — the idempotent ``set_content`` early-return
    short-circuits the swap and the failed S3FS DualPane stays in
    ``content_host.current``. The DualPane WIDGET gets re-mounted
    over the host, but it's bound to a stale local-only VM (or no
    VM at all if the path doesn't even mount the widget) and the
    panes look empty.
    """
    import contextlib as _contextlib

    from aws_tui.infra.aws_session import TokenProbeResult, TokenState
    from aws_tui.infra.connection_resolver import Connection

    config_dir = _prep(tmp_path, _MINIO_LOCAL_TOML)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")

    aws_conn = Connection(
        name="dev-sso",
        kind="aws",
        region="us-east-1",
        source="auto-aws-profile",
        profile="dev-sso",
    )
    # The minio-local entry from _MINIO_LOCAL_TOML provides the
    # s3-compatible candidate; the resolver returns AWS first so the
    # chain probes AWS+MISSING (fast-fail), then attempts the
    # s3-compatible against 127.0.0.1:1 (UNREACHABLE).
    real_list = ctx.connection_resolver.list

    def _list_with_aws_first() -> list[Connection]:
        return [aws_conn, *real_list()]

    ctx.connection_resolver.list = _list_with_aws_first  # type: ignore[assignment,method-assign]
    ctx.aws_session.probe_token = lambda _c: TokenProbeResult(  # type: ignore[assignment,method-assign]
        state=TokenState.MISSING
    )

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await _await_boot(pilot, app)
            for _ in range(5):
                await pilot.pause()

            dual_vm = ctx.root_vm.content_host.current
            assert dual_vm is not None
            # The current VM must be the LOCAL DualPaneVM. If
            # ``set_content`` short-circuited the swap during chain
            # exhaustion, ``current`` will still be the FAILED S3 VM
            # whose left pane is UNREACHABLE.
            from aws_tui.domain.local_fs import LocalFS

            left_provider = dual_vm.left._provider  # type: ignore[union-attr]
            assert isinstance(left_provider, LocalFS), (
                f"after chain exhaustion the LEFT pane should be LocalFS "
                f"but is {type(left_provider).__name__} — "
                f"``set_content(service_id='s3')`` was a no-op because "
                f"the failed s3-compatible attempt earlier in the chain "
                f"already set current_id to 's3'"
            )

            # And the rendered DOM must reflect populated panes.
            from aws_tui.ui.widgets.pane import EntryRow, Pane

            panes = pilot.app.query(Pane)
            assert len(panes) == 2
            for pane in panes:
                rows = list(pane.query(EntryRow))
                assert len(rows) > 0, (
                    f"Pane #{pane.id} has 0 EntryRows. VM state: "
                    f"{getattr(pane._vm, 'state', '?')}. This is the "  # type: ignore[attr-defined]
                    f"user-visible empty-panes bug."
                )
    finally:
        with _contextlib.suppress(Exception):
            _dispose(ctx)


@pytest.mark.asyncio
async def test_boot_chain_local_fallback_populates_both_panes(tmp_path: Path) -> None:
    """User report against PR #73: "upon launching the app, neither pane
    shows any content when neither aws s3 nor s3-compatible are
    available. Only when I browse the settings and come back to S3,
    it shows the local source."

    The mount-the-widget contract (asserted in
    ``test_boot_chain_narrates_failure_then_local_fallback``) is not
    the same as the panes-actually-show-files contract. The DualPane
    is mounted with two LocalFS panes but their entries are empty
    on user-visible screen at boot, even though a subsequent
    Settings → S3 toggle through the SAME ``_mount_local_only_dual_pane``
    code path populates them. This test pins the boot-time contract
    so any future regression on this UX gap fails fast.
    """
    import contextlib as _contextlib

    from aws_tui.infra.aws_session import TokenProbeResult, TokenState
    from aws_tui.infra.connection_resolver import Connection

    config_dir = _prep(tmp_path)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")

    fake_conn = Connection(
        name="dev-sso",
        kind="aws",
        region="us-east-1",
        source="auto-aws-profile",
        profile="dev-sso",
    )
    ctx.connection_resolver.list = lambda: [fake_conn]  # type: ignore[assignment,method-assign]
    ctx.aws_session.probe_token = lambda _c: TokenProbeResult(  # type: ignore[assignment,method-assign]
        state=TokenState.MISSING
    )

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await _await_boot(pilot, app)
            # Give the background setup tasks a couple of ticks
            # (LocalFS.list is microseconds but the setup task is
            # dispatched via ``asyncio.create_task`` so it has to
            # yield through the event loop first).
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            dual_vm = ctx.root_vm.content_host.current
            assert dual_vm is not None
            # VM-side entries must be populated.
            left_entries = list(dual_vm.left.entries)
            right_entries = list(dual_vm.right.entries)
            assert len(left_entries) > 0, (
                f"LEFT pane VM is empty after boot-chain local-fallback. "
                f"VM state: {dual_vm.left.state}."
            )
            assert len(right_entries) > 0, "RIGHT pane VM is empty after boot-chain local-fallback."

            # DOM-side EntryRow widgets must be rendered. The user's
            # report is "neither pane shows any content" — that's the
            # widget contract, not the VM contract. The VM having
            # entries doesn't help if the Pane widget never re-rendered
            # them.
            from aws_tui.ui.widgets.pane import EntryRow, Pane

            panes = pilot.app.query(Pane)
            assert len(panes) == 2, f"expected 2 Pane widgets, got {len(panes)}"
            for pane in panes:
                rows = list(pane.query(EntryRow))
                assert len(rows) > 0, (
                    f"Pane widget #{pane.id} mounted with no EntryRow "
                    f"children — VM has {len(dual_vm.left.entries) if pane.id == 'pane-left' else len(dual_vm.right.entries)} "
                    f"entries but they aren't rendered in the DOM. "
                    f"This is the user-visible 'neither pane shows any "
                    f"content' bug after boot-chain local-fallback."
                )
    finally:
        with _contextlib.suppress(Exception):
            _dispose(ctx)


@pytest.mark.asyncio
async def test_settings_to_s3_toggle_preserves_chain_local_fallback(tmp_path: Path) -> None:
    """When the boot chain ran out of candidates and mounted local
    on both panes, a subsequent Settings → S3 toggle must NOT
    rebuild the S3 DualPane (which would re-attempt the same
    failed connections and dump the user back on the same
    UNREACHABLE error pane the chain already navigated past).

    User report against the post-PR-71 build: "shows how the S3
    screen is again showing incorrect content despite recent
    fixes after switching to settings and then back again to
    S3 where it's supposed to fall back". The screenshot shows
    the LEFT pane back in ``s3://`` mode rendering "endpoint
    unreachable - press r to retry".
    """
    import contextlib as _contextlib

    from aws_tui.domain.local_fs import LocalFS
    from aws_tui.infra.aws_session import TokenProbeResult, TokenState
    from aws_tui.infra.connection_resolver import Connection
    from aws_tui.ui.widgets.dual_pane import DualPane

    config_dir = _prep(tmp_path)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")

    fake_conn = Connection(
        name="dev-sso",
        kind="aws",
        region="us-east-1",
        source="auto-aws-profile",
        profile="dev-sso",
    )
    ctx.connection_resolver.list = lambda: [fake_conn]  # type: ignore[assignment,method-assign]
    ctx.aws_session.probe_token = lambda _c: TokenProbeResult(  # type: ignore[assignment,method-assign]
        state=TokenState.MISSING
    )

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            await _await_boot(pilot, app)
            # Sanity: boot ended in local-fallback.
            assert app._chain_resolved_to_local is True

            # Toggle Settings → S3 via the NavMenu VM (same path the
            # ``comma`` keybinding takes, but VM-direct keeps the
            # test off keymap-routing concerns).
            from aws_tui.vm.nav_menu_vm import SETTINGS_NAV_ID

            ctx.root_vm.services_menu.switch_service_command.execute(SETTINGS_NAV_ID)
            await app.workers.wait_for_complete(  # type: ignore[attr-defined]
                list(app.workers._workers)  # type: ignore[attr-defined]
            )
            await pilot.pause()
            ctx.root_vm.services_menu.switch_service_command.execute("s3")
            await app.workers.wait_for_complete(  # type: ignore[attr-defined]
                list(app.workers._workers)  # type: ignore[attr-defined]
            )
            await pilot.pause()

            host = pilot.app.query_one("#content-host")
            dual_panes = host.query(DualPane)
            assert len(dual_panes) == 1
            dual_vm = ctx.root_vm.content_host.current
            # LEFT pane MUST still be LocalFS — the toggle must
            # NOT have rebuilt an S3FS provider.
            assert isinstance(dual_vm.left._provider, LocalFS), (  # type: ignore[union-attr]
                f"expected LEFT pane provider to remain LocalFS after the "
                f"Settings → S3 toggle (chain-fallback state persists for "
                f"the session); got {type(dual_vm.left._provider).__name__}"  # type: ignore[union-attr]
            )
    finally:
        with _contextlib.suppress(Exception):
            _dispose(ctx)


@pytest.mark.asyncio
async def test_boot_chain_narrates_failure_then_local_fallback(tmp_path: Path) -> None:
    """The post-PR-70 boot-chain UX: when the only configured
    connection's offline probe says "no working session", the chain
    raises a failure WARNING toast (not silent), then the
    local-fallback WARNING toast, then mounts LocalFS on both panes.

    The user explicitly asked for this narration after PR #70:
    "show toast notifications when each source is about to be tried.
    And if that source turns out not available, we show another toast
    that it's not or that it failed, and then show another one about
    trying the next option and so on. This way the app and its UX
    becomes more usable and tolerable to the user."

    This test pins the WARNING-toast contract (a failure happened
    AND the local fallback fired) without depending on the
    transient pre-attempt INFO toast — the chain's grace window
    means a fast offline probe (AWS+MISSING) resolves before the
    pre-attempt toast is raised, which is intentional: we don't
    flash chrome at users on a fast-resolving boot.
    """
    import contextlib as _contextlib

    from aws_tui.infra.aws_session import TokenProbeResult, TokenState
    from aws_tui.infra.connection_resolver import Connection
    from aws_tui.ui.widgets.dual_pane import DualPane
    from aws_tui.vm.chrome.toast_vm import ToastLevel

    config_dir = _prep(tmp_path)
    ctx = build_app_context(config_dir=config_dir, cache_dir=tmp_path / "cache")

    fake_conn = Connection(
        name="dev-sso",
        kind="aws",
        region="us-east-1",
        source="auto-aws-profile",
        profile="dev-sso",
    )
    ctx.connection_resolver.list = lambda: [fake_conn]  # type: ignore[assignment,method-assign]
    ctx.aws_session.probe_token = lambda _c: TokenProbeResult(  # type: ignore[assignment,method-assign]
        state=TokenState.MISSING
    )

    app = AwsTuiApp(ctx)
    try:
        async with app.run_test() as pilot:
            # Drive boot WITHOUT the helper — the helper dismisses
            # boot-* toasts so test interactions aren't covered by
            # them, but here we want to inspect those exact toasts
            # before they're dismissed.
            await app.workers.wait_for_complete(  # type: ignore[attr-defined]
                list(app.workers._workers)  # type: ignore[attr-defined]
            )
            await pilot.pause()

            stack = ctx.root_vm.chrome.toast_stack
            ids = {t.model.id for t in stack.toasts}
            warning_levels = {
                t.model.level for t in stack.toasts if t.model.level is ToastLevel.WARNING
            }

            # Per-attempt failure toast raised AND chain's
            # local-fallback toast raised. Two WARNING entries with
            # distinct ids — the user sees "✗ dev-sso no AWS
            # credentials" then "All configured sources unavailable
            # — both panes fell back to local".
            assert any(i.startswith("boot-outcome-aws-dev-sso") for i in ids), ids
            assert "boot-fallback-local" in ids, ids
            assert warning_levels == {ToastLevel.WARNING}, warning_levels

            # And the panes are usable: both LocalFS.
            host = pilot.app.query_one("#content-host")
            assert len(host.query(DualPane)) == 1
            assert (fake_conn.kind, fake_conn.name) in ctx.unreachable_connections
    finally:
        with _contextlib.suppress(Exception):
            _dispose(ctx)

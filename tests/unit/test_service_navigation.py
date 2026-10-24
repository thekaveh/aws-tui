"""Navigation ownership and adoption contracts without a Textual application."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from vmx import NULL_DISPATCHER, MessageHub, PropertyChangedMessage

from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.connection_resolver import Connection
from aws_tui.service_navigation import ServiceMountPorts, ServiceNavigation
from aws_tui.vm.messages import ConnectionChangedMessage
from aws_tui.vm.nav_menu_vm import NavMenuVM
from aws_tui.vm.services_protocol import ServiceDescriptor, ServiceRegistry


def connection(name="dev", region="us-east-1"):
    return Connection(name=name, kind="aws", region=region, profile=name, source="config")


class MountHarness:
    def __init__(self, navigation):
        self.navigation = navigation
        self.events = []
        self.root = SimpleNamespace(
            active_connection=connection(),
            content_host=SimpleNamespace(current=object(), current_id="s3"),
            switch_service=self.switch_service,
        )
        self.fail_at = None
        self.supersede_at = None
        self.retry_outcome = "ok"
        self.local_result = True
        self.errors = []
        self.log = SimpleNamespace(
            error=lambda event, **fields: self.errors.append((event, fields))
        )

    def checkpoint(self, event):
        self.events.append(event)
        if self.supersede_at == event:
            self.navigation.supersede()
        if self.fail_at == event:
            raise RuntimeError(event)

    async def cancel_transfers(self):
        self.checkpoint("cancel-transfers")
        await asyncio.sleep(0)

    def prepare_mount(self):
        self.checkpoint("capture-host")

        async def publish(service_id, vm):
            assert vm is self.root.content_host.current
            self.checkpoint(f"publish-{service_id}")
            await asyncio.sleep(0)

        return publish

    async def switch_service(self, service_id):
        self.checkpoint(f"switch-{service_id}")
        await asyncio.sleep(0)
        self.root.content_host.current_id = service_id

    async def retry(self, target):
        assert target is self.navigation.fallback_connection
        self.checkpoint("retry")
        return self.retry_outcome

    async def local_fallback(self, target):
        assert target is self.navigation.fallback_connection
        self.checkpoint("local-fallback")
        return self.local_result

    def ports(self):
        return ServiceMountPorts(
            root=self.root,
            log=self.log,
            cancel_transfers=self.cancel_transfers,
            prepare_mount=self.prepare_mount,
            retry_connection=self.retry,
            mount_local_fallback=self.local_fallback,
            begin_retry=lambda target: self.checkpoint("begin-retry"),
            report_retry_failure=lambda target, outcome: self.checkpoint(f"retry-failed-{outcome}"),
            report_local_fallback=lambda: self.checkpoint("fallback-notice"),
            restore_navigation=lambda service: self.checkpoint(f"restore-{service}"),
            refresh_hints=lambda: self.checkpoint("refresh-hints"),
        )


async def test_successful_adoption_preserves_operation_order():
    nav = ServiceNavigation()
    harness = MountHarness(nav)
    nav.supersede()
    assert await nav.mount_service("athena", ports=harness.ports())
    assert harness.events == [
        "cancel-transfers",
        "capture-host",
        "switch-athena",
        "publish-athena",
        "refresh-hints",
    ]
    assert harness.errors == []


@pytest.mark.parametrize("stage", ["capture-host", "switch-athena", "publish-athena"])
async def test_failed_adoption_matches_the_existing_recovery_boundary(stage):
    nav = ServiceNavigation()
    harness = MountHarness(nav)
    harness.fail_at = stage
    assert not await nav.mount_service("athena", ports=harness.ports())
    assert bool([event for event in harness.events if event == "restore-athena"]) == (
        stage != "publish-athena"
    )
    assert len(harness.errors) == 1


@pytest.mark.parametrize("stage", ["cancel-transfers", "switch-athena", "publish-athena"])
async def test_superseded_mount_never_reports_success_or_later_publication(stage):
    nav = ServiceNavigation()
    harness = MountHarness(nav)
    harness.supersede_at = stage
    assert not await nav.mount_service("athena", ports=harness.ports())
    if stage == "cancel-transfers":
        assert harness.events == [stage]
    elif stage == "switch-athena":
        assert "publish-athena" not in harness.events
    assert "refresh-hints" not in harness.events
    assert harness.errors == []


async def test_required_connection_is_read_after_transfer_drain_and_checked_in_full():
    nav = ServiceNavigation()
    harness = MountHarness(nav)
    required = harness.root.active_connection

    async def cancel_and_change_source():
        harness.checkpoint("cancel-transfers")
        harness.root.active_connection = connection(region="eu-west-1")

    harness.cancel_transfers = cancel_and_change_source
    assert not await nav.mount_service(
        "athena", ports=harness.ports(), required_connection=required
    )
    assert harness.events == ["cancel-transfers"]
    assert harness.errors[0][0] == "app.mount_service_view.connection_mismatch"


async def test_missing_view_model_is_not_a_success_or_a_second_adoption():
    nav = ServiceNavigation()
    harness = MountHarness(nav)
    harness.root.content_host.current = None
    assert not await nav.mount_service("s3", ports=harness.ports())
    assert harness.events == ["cancel-transfers", "capture-host", "switch-s3"]
    assert harness.errors[0][0] == "app.mount_service_view.missing_view_model"


@pytest.mark.parametrize("outcome", ["ok", "timeout"])
async def test_remembered_local_fallback_retry_keeps_existing_policy(outcome):
    nav = ServiceNavigation()
    harness = MountHarness(nav)
    remembered = connection("remembered")
    nav.remember_local_fallback(remembered)
    harness.retry_outcome = outcome
    assert await nav.mount_service("s3", ports=harness.ports())
    assert harness.events[:3] == ["cancel-transfers", "begin-retry", "retry"]
    if outcome == "ok":
        assert harness.events == ["cancel-transfers", "begin-retry", "retry"]
        assert not nav.local_fallback
        assert nav.fallback_connection is None
    else:
        assert harness.events[3:] == ["retry-failed-timeout", "fallback-notice", "local-fallback"]
        assert nav.local_fallback
        assert nav.fallback_connection is remembered


async def test_explicit_required_connection_does_not_trigger_remembered_retry():
    nav = ServiceNavigation()
    harness = MountHarness(nav)
    nav.remember_local_fallback(connection("remembered"))
    assert await nav.mount_service(
        "s3", ports=harness.ports(), required_connection=harness.root.active_connection
    )
    assert harness.events == ["cancel-transfers", "capture-host", "switch-s3", "publish-s3"]


async def test_rapid_service_and_source_ownership_keeps_broader_table_rollback_rule():
    nav = ServiceNavigation()
    first = nav.advance("table")
    second = nav.advance("table")
    assert not nav.claim_table(first)
    assert nav.claim_table(second)
    assert nav.may_restore_handoff(first)
    newer_source = nav.supersede()
    assert nav.owns("external", newer_source)
    assert not nav.owns("table", second)
    assert not nav.may_restore_handoff(first)
    assert not nav.claim_table(second)


async def test_selection_projection_is_publication_and_service_scoped():
    from aws_tui.vm.selection_projection import selection_changed

    nav = ServiceNavigation()
    with nav.suppress_selection("athena"):
        projected = selection_changed(object(), "menu", "athena")
        unrelated = selection_changed(object(), "menu", "glue")

    # A queued projection retains its origin outside its creation scope/task.
    async def delivered_later():
        return nav.consume_selection("athena", projected)

    assert await asyncio.create_task(delivered_later())
    assert not nav.consume_selection("glue", unrelated)
    assert not ServiceNavigation().consume_selection("athena", projected)
    user = selection_changed(object(), "menu", "athena")
    assert not nav.consume_selection("athena", user)
    assert not nav.consume_selection(
        "athena", PropertyChangedMessage.create(object(), "menu", "selected_id")
    )


async def test_advance_cancels_without_joining_a_handoff_needing_the_service_lock():
    nav = ServiceNavigation()
    started = asyncio.Event()
    rolled_back = asyncio.Event()

    async def handoff():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            async with nav.service_lock:
                rolled_back.set()

    task = asyncio.create_task(handoff())
    nav.track_navigation(task)
    await started.wait()
    async with nav.service_lock:
        nav.supersede()
        await asyncio.sleep(0)
        assert not rolled_back.is_set()
    await asyncio.gather(task, return_exceptions=True)
    assert rolled_back.is_set()
    await nav.drain()
    assert task.done()
    assert nav.navigation_tasks == frozenset()


async def test_shutdown_closes_intake_before_draining_and_never_cancels_rollback():
    nav = ServiceNavigation()
    started = asyncio.Event()
    release = asyncio.Event()
    rollback_finished = asyncio.Event()

    async def rollback():
        started.set()
        await release.wait()
        rollback_finished.set()

    task = asyncio.create_task(rollback())
    nav.track_rollback(task)
    await started.wait()
    nav.close_intake()
    assert nav.closed
    assert not task.cancelled()
    drain = asyncio.create_task(nav.drain())
    await asyncio.sleep(0)
    drain.cancel()
    release.set()
    await drain
    assert rollback_finished.is_set()
    assert nav.rollback_tasks == frozenset()


@pytest.mark.parametrize("current_id", ["s3", None])
async def test_restoration_uses_real_menu_and_suppresses_its_own_selection(current_id):
    nav = ServiceNavigation()
    registry = ServiceRegistry()
    for service in ["s3", "athena"]:
        registry.register(
            SimpleNamespace(
                descriptor=ServiceDescriptor(id=service, label=service, icon="s"),
                supports=lambda conn: True,
                build_vm=lambda conn: object(),
            )
        )
    hub = MessageHub()
    menu = NavMenuVM(registry=registry, hub=hub, dispatcher=NULL_DISPATCHER)
    menu.construct()
    subscription = None
    try:
        hub.send(ConnectionChangedMessage(connection=connection(), auth_state=TokenState.CONNECTED))
        menu.switch_service_command.execute("athena")
        generation = nav.supersede()
        hints = []
        notices = []
        suppressed = []

        def selected(message):
            if (
                not isinstance(message, PropertyChangedMessage)
                or message.sender_object is not menu
                or message.property_name != "selected_id"
            ):
                return
            if menu.selected_id is None:
                return
            consumed = nav.consume_selection(menu.selected_id, message)
            suppressed.append(consumed)
            if not consumed:
                nav.supersede()

        subscription = hub.messages.subscribe(on_next=selected)
        root = SimpleNamespace(
            content_host=SimpleNamespace(current_id=current_id),
            services_menu=menu,
            chrome=SimpleNamespace(hint_legend=SimpleNamespace(set_current_service=hints.append)),
        )
        nav.restore_navigation(
            root,
            "athena",
            notify_failure=lambda requested, current: notices.append((requested, current)),
        )
        assert menu.selected_id == current_id
        assert hints == [current_id]
        assert notices == [("athena", current_id)]
        assert nav.owns("external", generation)
        assert suppressed == ([True] if current_id is not None else [])
    finally:
        if subscription is not None:
            subscription.dispose()
        menu.dispose()


async def test_overlapping_service_and_source_mounts_publish_only_latest_owner():
    nav = ServiceNavigation()
    harness = MountHarness(nav)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def switch(service):
        harness.checkpoint("switch-" + service)
        if service == "athena":
            entered.set()
            await release.wait()
        harness.root.content_host.current_id = service

    harness.root.switch_service = switch

    async def request(service, target, generation):
        async with nav.service_lock:
            if not nav.owns("external", generation):
                return False
            harness.root.active_connection = target
            return await nav.mount_service(
                service, ports=harness.ports(), required_connection=target
            )

    first = nav.supersede()
    old = asyncio.create_task(request("athena", connection(), first))
    await asyncio.wait_for(entered.wait(), timeout=2)
    latest = nav.supersede()
    target = connection("prod", "eu-west-1")
    new = asyncio.create_task(request("glue", target, latest))
    release.set()
    assert await asyncio.wait_for(asyncio.gather(old, new), timeout=2) == [False, True]
    assert harness.root.active_connection is target
    assert harness.root.content_host.current_id == "glue"
    assert [event for event in harness.events if event.startswith("publish-")] == ["publish-glue"]
    assert harness.events.count("refresh-hints") == 1
    assert nav.owns("external", latest)


async def test_closed_intake_keeps_transfer_drain_before_rejecting_mount():
    nav = ServiceNavigation()
    harness = MountHarness(nav)
    nav.close_intake()
    assert not await nav.mount_service("athena", ports=harness.ports())
    assert harness.events == ["cancel-transfers"]


@pytest.mark.skipif(not hasattr(asyncio, "eager_task_factory"), reason="Python 3.12 eager tasks")
async def test_reentrant_eager_handoff_projection_preserves_owner_and_later_user_supersedes():
    from aws_tui.app import AwsTuiApp
    from aws_tui.domain.data_catalog import TableRef
    from aws_tui.vm.messages import OpenAthenaTableRequest

    nav = ServiceNavigation()
    registry = ServiceRegistry()
    for service in ["glue", "athena"]:
        registry.register(
            SimpleNamespace(
                descriptor=ServiceDescriptor(id=service, label=service, icon="s"),
                supports=lambda conn: True,
                build_vm=lambda conn: object(),
            )
        )
    hub = MessageHub()
    menu = NavMenuVM(registry=registry, hub=hub, dispatcher=NULL_DISPATCHER)
    menu.construct()
    hub.send(ConnectionChangedMessage(connection=connection(), auth_state=TokenState.CONNECTED))
    menu.switch_service_command.execute("glue")
    app = SimpleNamespace(
        service_navigation=nav,
        _app_ctx=SimpleNamespace(
            root_vm=SimpleNamespace(
                services_menu=menu,
                chrome=SimpleNamespace(
                    hint_legend=SimpleNamespace(set_current_service=lambda _: None)
                ),
            )
        ),
        _boot_in_flight=False,
        _supersede_table_navigation=nav.supersede,
        _run_lifecycle_worker=lambda *args, **kwargs: None,
        _mount_external_navigation=lambda *args: None,
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    tasks = []
    generation = nav.advance("table")

    async def handoff():
        with nav.suppress_selection("athena"):
            menu.switch_service_command.execute("athena")
            entered.set()
            await release.wait()

    def route(message):
        if isinstance(message, OpenAthenaTableRequest):
            tasks.append(asyncio.create_task(handoff()))
        else:
            AwsTuiApp._on_nav_selection_changed(app, message)

    subscription = hub.messages.subscribe(on_next=route)
    loop = asyncio.get_running_loop()
    original_factory = loop.get_task_factory()
    try:
        loop.set_task_factory(asyncio.eager_task_factory)
        hub.send(
            OpenAthenaTableRequest(
                table_ref=TableRef(
                    connection_name="dev",
                    region="us-east-1",
                    catalog_name="AwsDataCatalog",
                    database_name="db",
                    table_name="table",
                )
            )
        )
        assert entered.is_set(), "handoff must publish while the outer MessageHub drain is active"
        assert nav.owns("table", generation)
        # The user's later away-and-back choice must not inherit suppression.
        menu.switch_service_command.execute("glue")
        assert nav.owns("external", generation + 1)
        menu.switch_service_command.execute("athena")
        assert nav.owns("external", generation + 2)
    finally:
        loop.set_task_factory(original_factory)
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        subscription.dispose()
        menu.dispose()

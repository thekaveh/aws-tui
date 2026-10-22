"""Remember only actual outgoing file panes, before their disposal."""

import asyncio

import pytest
from vmx import NULL_DISPATCHER, MessageHub, PropertyChangedMessage

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.navigation_workspace import FileLocation, NavigationTarget, WorkspaceError
from aws_tui.domain.transfer_journal import TransferJournal
from aws_tui.infra.aws_session import TokenState
from aws_tui.infra.keymap_store import KeymapStore
from aws_tui.infra.log_sink import LogSink
from aws_tui.infra.theme_store import ThemeStore
from aws_tui.services.s3 import S3Service
from aws_tui.vm.messages import ConnectionChangedMessage
from aws_tui.vm.root_vm import RootVM
from aws_tui.vm.services_protocol import ServiceRegistry
from aws_tui.vm.workspace_panes import capture_pane_target
from tests.unit.vm.test_root_vm import _aws_conn, _FailingService, _FakeService


async def make_root(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    (local / "data").mkdir()
    remote = InMemoryFS()
    await remote.mkdir(PathRef(("bucket", "literal", "", "prefix")))
    hub = MessageHub()
    service = S3Service(
        transfer_journal=TransferJournal(base_dir=tmp_path / "journal"),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
        local_root=local,
        s3_fs_factory=lambda _connection: remote,
    )
    registry = ServiceRegistry()
    for plugin in (service, _FakeService("other"), _FailingService("bad", fail_during="construct")):
        registry.register(plugin)
    log = LogSink(base_dir=tmp_path / "log")
    root = RootVM(
        registry=registry,
        keymap=KeymapStore(),
        theme=ThemeStore(),
        log=log,
        dispatcher=NULL_DISPATCHER,
        hub=hub,
    )
    root.construct()
    await root.switch_connection_with(_aws_conn(), TokenState.CONNECTED)
    await root.switch_service("s3")
    await root.content_host._setup_task
    return root, service, log


@pytest.mark.asyncio
async def test_last_known_panes_survive_switch_to_another_service(tmp_path):
    root, service, log = await make_root(tmp_path)
    try:
        assert service.navigation_panes() is None
        dual = root.content_host.current
        await dual.left.navigate_to(PathRef(("bucket", "literal", "", "prefix")))
        await dual.right.navigate_to(PathRef(("data",)))
        expected = (capture_pane_target(dual.left), capture_pane_target(dual.right))
        await root.switch_service("other")
        assert service.navigation_panes() == expected
        assert dual._disposed
        assert root.content_host.current_id == "other"
    finally:
        await root.content_host.shutdown()
        root.dispose()
        log.close()


@pytest.mark.asyncio
async def test_failed_candidate_does_not_replace_last_known_navigation(tmp_path):
    root, service, log = await make_root(tmp_path)
    try:
        current = root.content_host.current
        with pytest.raises(RuntimeError, match="candidate construction failed"):
            await root.switch_service("bad")
        assert root.content_host.current is current
        assert not current._disposed
        assert service.navigation_panes() is None
        expected = (capture_pane_target(current.left), capture_pane_target(current.right))
        await root.switch_connection_with(_aws_conn("next"), TokenState.CONNECTED)
        assert service.navigation_panes() == expected
    finally:
        await root.content_host.shutdown()
        root.dispose()
        log.close()


@pytest.mark.asyncio
async def test_capture_failure_is_visible_and_does_not_interrupt_retirement(tmp_path):
    root, service, log = await make_root(tmp_path)
    try:
        current = root.content_host.current
        # A caller without complete source metadata cannot save a guessed region.
        await current.left.swap_provider(
            InMemoryFS(), connection_key=("aws", "unknown-region"), path_protocol="s3:"
        )
        await root.switch_service("other")
        assert root.content_host.current_id == "other"
        assert current._disposed
        with pytest.raises(WorkspaceError):
            service.navigation_panes()
    finally:
        await root.content_host.shutdown()
        root.dispose()
        log.close()


@pytest.mark.asyncio
async def test_prepared_local_workspace_adoption_clears_global_source_without_dummy_connection(
    tmp_path,
):
    root, service, log = await make_root(tmp_path)
    current = root.content_host.current
    local_target = NavigationTarget("s3", location=FileLocation(local=str(tmp_path / "local")))
    stage = service.build_workspace_vm((local_target, local_target), ())
    stage.vm.construct()
    await stage.vm.setup()
    messages = []
    subscription = root.message_hub.messages.subscribe(
        lambda message: (
            messages.append(message) if isinstance(message, ConnectionChangedMessage) else None
        )
    )
    try:
        assert await root.adopt_prepared_service_vm(None, TokenState.MISSING, "s3", stage.vm)
        stage.commit_selection()
        assert root.content_host.current is stage.vm
        assert current._disposed
        assert root.active_connection is None
        assert root.active_auth_state is TokenState.MISSING
        assert root.services_menu.selected_id == "s3"
        assert {item.descriptor.id for item in root.services_menu.items} == {"s3", "settings"}
        assert messages == [
            ConnectionChangedMessage(connection=None, auth_state=TokenState.MISSING)
        ]
    finally:
        subscription.dispose()
        await root.content_host.shutdown()
        if root.content_host.current is not stage.vm:
            await stage.vm.shutdown()
            stage.vm.dispose()
        root.dispose()
        service.transfer_runtime.dispose()
        log.close()


@pytest.mark.parametrize("case", ["ownership-lost", "unsupported-service"])
@pytest.mark.asyncio
async def test_rejected_sourceless_adoption_keeps_current_connection_and_content(tmp_path, case):
    root, service, log = await make_root(tmp_path)
    original = root.active_connection
    current = root.content_host.current
    local_target = NavigationTarget("s3", location=FileLocation(local=str(tmp_path / "local")))
    stage = service.build_workspace_vm((local_target, local_target), ())
    stage.vm.construct()
    await stage.vm.setup()
    try:
        if case == "ownership-lost":
            assert not await root.adopt_prepared_service_vm(
                None, TokenState.MISSING, "s3", stage.vm, ownership_is_current=lambda: False
            )
        else:
            with pytest.raises(RuntimeError, match="Only file navigation"):
                await root.adopt_prepared_service_vm(None, TokenState.MISSING, "other", stage.vm)
        assert root.active_connection == original
        assert root.content_host.current is current
        assert not current._disposed
        assert root.services_menu.selected_id == "s3"
        assert not stage.vm._disposed
    finally:
        await stage.vm.shutdown()
        stage.vm.dispose()
        await root.content_host.shutdown()
        root.dispose()
        service.transfer_runtime.dispose()
        log.close()


@pytest.mark.parametrize("outgoing", ["s3", "other"])
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
@pytest.mark.asyncio
async def test_published_local_workspace_keeps_truthful_menu_after_observer_failure(
    tmp_path, outgoing, error_type
):
    root, service, log = await make_root(tmp_path)
    if outgoing != "s3":
        await root.switch_service(outgoing)
        if root.content_host._setup_task is not None:
            await root.content_host._setup_task
    target = NavigationTarget("s3", location=FileLocation(local=str(tmp_path / "local")))
    stage = service.build_workspace_vm((target, target), ())
    stage.vm.construct()
    await stage.vm.setup()
    fired = False
    messages = []

    def observer(message):
        nonlocal fired
        if (
            not fired
            and isinstance(message, PropertyChangedMessage)
            and message.property_name == "current"
            and root.content_host.current is stage.vm
        ):
            fired = True
            raise error_type("synthetic observer failure")

    subscription = root.message_hub.messages.subscribe(observer)
    observed = root.message_hub.messages.subscribe(
        lambda message: (
            messages.append(message) if isinstance(message, ConnectionChangedMessage) else None
        )
    )
    try:
        try:
            assert await root.adopt_prepared_service_vm(None, TokenState.CONNECTED, "s3", stage.vm)
        except asyncio.CancelledError:
            assert error_type is asyncio.CancelledError
        assert fired
        assert root.content_host.current is stage.vm
        assert not stage.vm._disposed
        assert root.active_connection is None
        assert root.active_auth_state is TokenState.MISSING
        assert root.services_menu.selected_id == "s3"
        assert {item.descriptor.id for item in root.services_menu.items} == {"s3", "settings"}
        assert messages == [ConnectionChangedMessage(None, TokenState.MISSING)]
        stage.commit_selection()
    finally:
        subscription.dispose()
        observed.dispose()
        await root.content_host.shutdown()
        root.dispose()
        if not stage.vm._disposed:
            await stage.vm.shutdown()
            stage.vm.dispose()
        service.transfer_runtime.dispose()
        log.close()

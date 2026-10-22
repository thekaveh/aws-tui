import asyncio
from dataclasses import replace
from threading import Event

import pytest

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.unavailable_navigation_fs import UnavailableNavigationFS
from aws_tui.vm.file_manager.pane_vm import PaneState
from aws_tui.vm.messages import ConnectionListChangedMessage
from aws_tui.vm.workspace_panes import capture_pane_target
from aws_tui.workspace_restore import prepare_file_bookmark, retire_workspace_candidate
from tests.unit.services.s3.test_workspace_candidate import connection, local, make_service, remote


class Resolver:
    def __init__(self, source):
        self.source = source
        self.calls = 0

    def resolve_selected(self, name):
        assert name == self.source.name
        self.calls += 1
        return self.source


@pytest.mark.asyncio
async def test_deferred_companion_has_honest_loading_state(tmp_path):
    (tmp_path / "local").mkdir()
    entered, release = asyncio.Event(), asyncio.Event()

    class Held(InMemoryFS):
        async def list(self, path):
            entered.set()
            await release.wait()
            return []

    service = make_service(tmp_path, factory=lambda _source: Held())
    resolver = Resolver(connection())
    stage = await prepare_file_bookmark(
        local(tmp_path / "local"), remote(segments=()), service, (), resolver=resolver
    )
    try:
        assert resolver.calls == 0
        stage.recovery.commit_selection()
        await asyncio.wait_for(entered.wait(), 3)
        pane = stage.recovery.vm.right
        assert pane.state is PaneState.LOADING, (
            f"held companion read is presented as {pane.state}: {pane.viewmodel}"
        )
    finally:
        release.set()
        await retire_workspace_candidate(stage.recovery.vm)
        service.transfer_runtime.dispose()


@pytest.mark.asyncio
async def test_deferred_companion_source_event_aba_invalidates_old_stage(tmp_path):
    (tmp_path / "local").mkdir()
    entered, release = asyncio.Event(), asyncio.Event()

    class Held(InMemoryFS):
        async def list(self, path):
            entered.set()
            await release.wait()
            return []

    provider = Held()
    service = make_service(tmp_path, factory=lambda _source: provider)
    original = connection()
    resolver = Resolver(original)
    target = remote(segments=())
    stage = await prepare_file_bookmark(
        local(tmp_path / "local"), target, service, (), resolver=resolver, hub=service._hub
    )
    try:
        stage.recovery.commit_selection()
        pane = stage.recovery.vm.right
        before = pane.provider
        await asyncio.wait_for(entered.wait(), 3)
        resolver.source = replace(original, profile="other-synthetic-profile")
        service._hub.send(ConnectionListChangedMessage(names=(original.name,), change="updated"))
        resolver.source = original
        service._hub.send(ConnectionListChangedMessage(names=(original.name,), change="updated"))
        release.set()
        await asyncio.gather(*tuple(stage.recovery.vm._refresh_tasks))
        assert pane.provider is before, "companion stage adopted despite named-source change ABA"
        assert capture_pane_target(pane) == target
    finally:
        release.set()
        await retire_workspace_candidate(stage.recovery.vm)
        service.transfer_runtime.dispose()


@pytest.mark.parametrize("newer_input", ["source", "filter_aba", "refresh"])
@pytest.mark.asyncio
async def test_deferred_companion_does_not_overwrite_newer_pane_input(tmp_path, newer_input):
    (tmp_path / "local").mkdir()
    entered, release = asyncio.Event(), asyncio.Event()

    class Held(InMemoryFS):
        async def list(self, path):
            entered.set()
            await release.wait()
            return []

    provider = Held()
    service = make_service(tmp_path, factory=lambda _source: provider)
    stage = await prepare_file_bookmark(
        local(tmp_path / "local"), remote(segments=()), service, (), resolver=Resolver(connection())
    )
    try:
        stage.recovery.commit_selection()
        pane = stage.recovery.vm.right
        await asyncio.wait_for(entered.wait(), 3)
        if newer_input == "source":
            replacement = service.build_local_provider()
            await pane.swap_provider(
                replacement, identity_label="local", path_protocol="", connection_key=None
            )
        elif newer_input == "filter_aba":
            replacement = pane.provider
            pane.set_filter_command.execute("newer")
            pane.set_filter_command.execute("")
        else:
            replacement = pane.provider
            await pane.refresh()
        release.set()
        await asyncio.gather(*tuple(stage.recovery.vm._refresh_tasks))
        assert pane.provider is replacement
        assert pane.provider is not provider
    finally:
        release.set()
        await retire_workspace_candidate(stage.recovery.vm)
        service.transfer_runtime.dispose()


@pytest.mark.asyncio
async def test_deferred_companion_resolver_drains_on_repeated_retirement_cancellation(tmp_path):
    (tmp_path / "local").mkdir()
    entered, release, drained = Event(), Event(), Event()

    class HeldResolver(Resolver):
        def resolve_selected(self, name):
            entered.set()
            try:
                assert release.wait(5)
                return super().resolve_selected(name)
            finally:
                drained.set()

    service = make_service(tmp_path)
    stage = await prepare_file_bookmark(
        local(tmp_path / "local"),
        remote(segments=()),
        service,
        (),
        resolver=HeldResolver(connection()),
    )
    dual = stage.recovery.vm
    shutdown = None
    try:
        stage.recovery.commit_selection()
        assert await asyncio.to_thread(entered.wait, 3)
        shutdown = asyncio.create_task(dual.shutdown())
        for _ in range(5):
            await asyncio.sleep(0)
        assert not shutdown.done()
        shutdown.cancel()
        for _ in range(5):
            await asyncio.sleep(0)
        assert not shutdown.done()
        assert not drained.is_set()
        release.set()
        result = await asyncio.wait_for(asyncio.gather(shutdown, return_exceptions=True), 3)
        assert isinstance(result[0], asyncio.CancelledError)
        assert drained.is_set()
        assert not dual._refresh_tasks
        assert isinstance(dual.right.provider, UnavailableNavigationFS)
    finally:
        release.set()
        if shutdown is not None:
            await asyncio.gather(shutdown, return_exceptions=True)
        dual.dispose()
        service.transfer_runtime.dispose()


@pytest.mark.parametrize("change", ["current_binding", "unrelated_event"])
@pytest.mark.asyncio
async def test_deferred_companion_current_route_revalidation_is_scoped(tmp_path, change):
    (tmp_path / "local").mkdir()
    entered, release = asyncio.Event(), asyncio.Event()

    class Held(InMemoryFS):
        async def list(self, path):
            entered.set()
            await release.wait()
            return []

    provider = Held()
    service = make_service(tmp_path, factory=lambda _source: provider)
    original = connection()
    resolver = Resolver(original)
    target = remote(segments=())
    stage = await prepare_file_bookmark(
        local(tmp_path / "local"), target, service, (), resolver=resolver, hub=service._hub
    )
    try:
        stage.recovery.commit_selection()
        pane = stage.recovery.vm.right
        before = pane.provider
        await asyncio.wait_for(entered.wait(), 3)
        if change == "current_binding":
            resolver.source = replace(original, profile="new-synthetic-profile")
        else:
            service._hub.send(ConnectionListChangedMessage(names=("unrelated",), change="updated"))
        release.set()
        await asyncio.gather(*tuple(stage.recovery.vm._refresh_tasks))
        if change == "current_binding":
            assert pane.provider is before
            assert pane.state is PaneState.UNREACHABLE
        else:
            assert pane.provider is provider
            assert pane.state is PaneState.EMPTY
        assert capture_pane_target(pane) == target
    finally:
        release.set()
        await retire_workspace_candidate(stage.recovery.vm)
        service.transfer_runtime.dispose()


@pytest.mark.asyncio
async def test_deferred_metadata_cleanup_drains_and_restores_unavailable_state(tmp_path):
    (tmp_path / "local").mkdir()
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Held(InMemoryFS):
        async def list(self, path):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleanup.set()
                await release.wait()

    service = make_service(tmp_path, factory=lambda _source: Held())
    stage = await prepare_file_bookmark(
        local(tmp_path / "local"), remote(segments=()), service, (), resolver=Resolver(connection())
    )
    dual = stage.recovery.vm
    shutdown = None
    try:
        stage.recovery.commit_selection()
        await asyncio.wait_for(entered.wait(), 3)
        assert dual.right.state is PaneState.LOADING
        shutdown = asyncio.create_task(dual.shutdown())
        await asyncio.wait_for(cleanup.wait(), 3)
        for _ in range(2):
            shutdown.cancel()
            await asyncio.sleep(0)
            assert not shutdown.done()
        release.set()
        result = await asyncio.wait_for(asyncio.gather(shutdown, return_exceptions=True), 3)
        assert isinstance(result[0], asyncio.CancelledError)
        assert not dual._refresh_tasks
        assert isinstance(dual.right.provider, UnavailableNavigationFS)
        assert dual.right.state is PaneState.UNREACHABLE
        assert dual.right.viewmodel.placeholder_text is not None
    finally:
        release.set()
        if shutdown is not None:
            await asyncio.gather(shutdown, return_exceptions=True)
        dual.dispose()
        service.transfer_runtime.dispose()

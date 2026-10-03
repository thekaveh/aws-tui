"""Current-object details, driven by real pane selection and lifecycle."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError, replace
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from vmx import NULL_DISPATCHER, Message, MessageHub, PropertyChangedMessage

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.s3_object_details import S3ObjectDetails
from aws_tui.vm.file_manager.pane_vm import PaneVM
from aws_tui.vm.file_manager.s3_object_details_vm import (
    ObjectDetailField,
    S3ObjectDetailsState,
    S3ObjectDetailsVM,
)
from tests.s3_object_details_support import DetailsInMemoryFS, DetailsReadBarrier

SECRET_ERROR = "https://user:secret@example.com/?X-Amz-Signature=signature token=hidden"
Context = tuple[DetailsInMemoryFS, PaneVM, S3ObjectDetailsVM]

LABELS = (
    "Content type",
    "Content encoding",
    "Size",
    "Last modified",
    "Storage class",
    "ETag",
    "Version ID",
    "Encryption",
    "User metadata",
    "Tags",
    "Checksums",
    "Checksum type",
    "Checksum verification",
)


def details(key: str = "a.txt", **kwargs: Any) -> S3ObjectDetails:
    return replace(S3ObjectDetails(bucket="bucket", key=key, content_type=key), **kwargs)


async def stream() -> AsyncIterator[bytes]:
    yield b"payload"


async def seeded() -> DetailsInMemoryFS:
    fs = DetailsInMemoryFS()
    for name in ("a.txt", "b.txt"):
        await fs.write_stream(PathRef((name,)), stream())
    return fs


@pytest.fixture
async def context() -> AsyncIterator[Context]:
    fs = await seeded()
    hub = cast("MessageHub[Message]", MessageHub())
    pane = PaneVM(
        provider=fs,
        path_protocol="s3:",
        connection_key=("aws", "fixture"),
        hub=hub,
        dispatcher=NULL_DISPATCHER,
    )
    pane.construct()
    await pane.setup()
    vm = S3ObjectDetailsVM(pane=pane, hub=hub, dispatcher=NULL_DISPATCHER)
    vm.construct()
    try:
        yield fs, pane, vm
    finally:
        await vm.shutdown()
        vm.dispose()
        pane.dispose()


def fields(vm: S3ObjectDetailsVM) -> dict[str, ObjectDetailField]:
    return {field.label: field for field in vm.fields}


async def wait(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=1)


async def load(vm: S3ObjectDetailsVM) -> None:
    await vm.load_revision(vm.request_generation)


async def test_all_required_fields_and_missing_values_project_explicitly(context: Context) -> None:
    fs, _, vm = context
    fs.queue_details(
        details(
            content_type="text/plain",
            size=42,
            modified=datetime(2026, 10, 3, tzinfo=UTC),
            etag='"opaque-etag"',
        )
    )
    await load(vm)
    assert vm.state is S3ObjectDetailsState.READY
    assert tuple(fields(vm)) == LABELS
    assert fields(vm)["Content type"].value == "text/plain"
    assert fields(vm)["Size"].value == "42 bytes"
    assert fields(vm)["Size"].copy_value == "42"
    assert fields(vm)["Last modified"].value == "2026-10-03T00:00:00+00:00"
    assert fields(vm)["ETag"].copy_value == '"opaque-etag"'
    for label in (
        "Content encoding",
        "Storage class",
        "Version ID",
        "Encryption",
        "User metadata",
        "Tags",
        "Checksums",
        "Checksum type",
    ):
        assert fields(vm)[label].value == "Unavailable"
        assert fields(vm)[label].copy_value is None
    assert isinstance(vm.fields, tuple)
    with pytest.raises(FrozenInstanceError):
        vm.fields[0].value = "mutated"  # type: ignore[misc]  # Test runtime immutability.
    assert "a.txt" in vm.title


async def test_etag_is_separate_and_checksums_never_claim_verification(context: Context) -> None:
    fs, _, vm = context
    fs.queue_details(
        details(
            etag='"not-an-md5-2"',
            checksums=(("ChecksumSHA256", "reported=="),),
            checksum_type="COMPOSITE",
        )
    )
    await load(vm)
    assert fields(vm)["ETag"].value == '"not-an-md5-2"'
    assert json.loads(fields(vm)["Checksums"].value) == {"ChecksumSHA256": "reported=="}
    assert "not performed" in fields(vm)["Checksum verification"].value.lower()
    assert fields(vm)["Checksum type"].copy_value == "COMPOSITE"
    assert "not-an-md5" not in fields(vm)["Checksums"].value


async def test_empty_value_is_copyable_but_unavailable_is_not(context: Context) -> None:
    fs, _, vm = context
    fs.queue_details(details(content_type="", metadata=(), tags=(), checksums=(), encryption=()))
    await load(vm)
    assert fields(vm)["Content type"].copy_value == ""
    assert fields(vm)["Content type"].value == "(empty)"
    for label in ("User metadata", "Tags", "Checksums", "Encryption"):
        assert fields(vm)[label].value == "{}"
        assert fields(vm)[label].copy_value == "{}"
    assert fields(vm)["Content encoding"].copy_value is None


async def test_partial_errors_are_redacted_without_hiding_other_fields(
    context: Context, caplog: pytest.LogCaptureFixture
) -> None:
    fs, _, vm = context
    metadata = (("token", "literal secret"), ("[bold]", '\nquoted "text"'))
    fs.queue_details(
        details(metadata=metadata, tags_error=SECRET_ERROR, checksums_error=SECRET_ERROR)
    )
    await load(vm)
    assert vm.state is S3ObjectDetailsState.READY
    assert json.loads(fields(vm)["User metadata"].value) == dict(metadata)
    for label in ("Tags", "Checksums"):
        value = fields(vm)[label].value
        assert "[REDACTED]" in value
        assert "secret" not in value
        assert "hidden" not in value
        assert "signature" not in value
        assert fields(vm)[label].copy_value is None
    assert fields(vm)["Content type"].value == "a.txt"
    assert SECRET_ERROR not in caplog.text


@pytest.mark.parametrize("late_error", [False, True])
async def test_old_reply_cannot_replace_new_selected_object(
    context: Context, late_error: bool, caplog: pytest.LogCaptureFixture
) -> None:
    fs, pane, vm = context
    barrier = DetailsReadBarrier(
        RuntimeError(SECRET_ERROR) if late_error else details(), cancellation_resistant=True
    )
    fs.queue_details(barrier, details("b.txt"))
    old_generation = vm.request_generation
    old = asyncio.create_task(vm.load_revision(old_generation))
    await wait(barrier.entered)
    pane.move_cursor_command.execute(1)
    assert pane.selected_entry is not None
    assert pane.selected_entry.name == "b.txt"
    assert vm.request_generation > old_generation
    assert vm.fields == ()
    await vm.load_revision(old_generation)  # obsolete calls do not read
    await load(vm)
    expected = vm.fields, vm.title
    barrier.release.set()
    await asyncio.wait_for(old, timeout=1)
    assert (vm.fields, vm.title) == expected
    assert fields(vm)["Content type"].value == "b.txt"
    assert fs.details_paths == [PathRef(("a.txt",)), PathRef(("b.txt",))]
    assert SECRET_ERROR not in caplog.text


@pytest.mark.parametrize("late_error", [False, True])
async def test_same_named_target_different_provider_is_not_current(
    context: Context, late_error: bool, caplog: pytest.LogCaptureFixture
) -> None:
    fs, pane, vm = context
    barrier = DetailsReadBarrier(
        RuntimeError(SECRET_ERROR) if late_error else details(content_type="old provider"),
        cancellation_resistant=True,
    )
    fs.queue_details(barrier)
    old = asyncio.create_task(load(vm))
    await wait(barrier.entered)
    new = await seeded()
    new.queue_details(details(content_type="new provider"))
    await pane.swap_provider(new, path_protocol="s3:", connection_key=("aws", "fixture"))
    await load(vm)
    barrier.release.set()
    await asyncio.wait_for(old, timeout=1)
    assert fields(vm)["Content type"].value == "new provider"
    assert len(fs.details_paths) == len(new.details_paths) == 1
    assert "hidden" not in caplog.text


@pytest.mark.parametrize("late_error", [False, True])
async def test_selection_away_and_back_invalidates_first_reply(
    context: Context, late_error: bool, caplog: pytest.LogCaptureFixture
) -> None:
    fs, pane, vm = context
    barrier = DetailsReadBarrier(
        RuntimeError(SECRET_ERROR) if late_error else details(content_type="first"),
        cancellation_resistant=True,
    )
    fs.queue_details(barrier, details(content_type="fresh"))
    first_generation = vm.request_generation
    old = asyncio.create_task(load(vm))
    await wait(barrier.entered)
    pane.move_cursor_command.execute(1)
    pane.move_cursor_command.execute(-1)
    assert vm.request_generation > first_generation
    await load(vm)
    barrier.release.set()
    await asyncio.wait_for(old, timeout=1)
    assert fields(vm)["Content type"].value == "fresh"
    assert fs.details_paths == [PathRef(("a.txt",)), PathRef(("a.txt",))]
    assert "hidden" not in caplog.text


@pytest.mark.parametrize("late_error", [False, True])
async def test_pane_completion_closes_before_reply(
    context: Context, late_error: bool, caplog: pytest.LogCaptureFixture
) -> None:
    fs, pane, vm = context
    barrier = DetailsReadBarrier(
        RuntimeError(SECRET_ERROR) if late_error else details(), cancellation_resistant=True
    )
    fs.queue_details(barrier)
    task = asyncio.create_task(load(vm))
    await wait(barrier.entered)
    pane.dispose()
    assert vm.state is S3ObjectDetailsState.CLOSED
    assert vm.fields == ()
    barrier.release.set()
    await asyncio.wait_for(task, timeout=1)
    assert vm.state is S3ObjectDetailsState.CLOSED
    assert vm.fields == ()
    assert "hidden" not in caplog.text


@pytest.mark.parametrize("dispose", [False, True])
@pytest.mark.parametrize("late_error", [False, True])
async def test_close_dispose_drains_and_suppresses_late_errors(
    context: Context, dispose: bool, late_error: bool, caplog: pytest.LogCaptureFixture
) -> None:
    fs, pane, vm = context
    barrier = DetailsReadBarrier(
        RuntimeError(SECRET_ERROR) if late_error else details(), cancellation_resistant=True
    )
    fs.queue_details(barrier)
    read = asyncio.create_task(load(vm))
    await wait(barrier.entered)
    changes: list[str] = []
    sub = vm.on_property_changed.subscribe(changes.append)
    vm.dispose() if dispose else vm.close()
    shutdown = asyncio.create_task(vm.shutdown())
    await wait(barrier.cancelled)
    assert not shutdown.done()
    assert not barrier.finished.is_set()
    snapshot = tuple(changes)
    pane.move_cursor_command.execute(1)
    await load(vm)
    barrier.release.set()
    await asyncio.wait_for(asyncio.gather(read, shutdown), timeout=1)
    assert barrier.finished.is_set()
    assert vm.state is S3ObjectDetailsState.CLOSED
    assert vm.fields == ()
    assert tuple(changes) == snapshot
    assert SECRET_ERROR not in caplog.text
    assert "hidden" not in caplog.text
    assert len(fs.details_paths) == 1
    sub.dispose()


async def test_marks_do_not_issue_another_details_read(context: Context) -> None:
    fs, pane, vm = context
    fs.queue_details(details())
    await load(vm)
    generation = vm.request_generation
    pane.toggle_select_command.execute()
    pane.select_all_command.execute()
    pane.clear_selection_command.execute()
    await load(vm)
    assert vm.request_generation == generation
    assert len(fs.details_paths) == 1


@pytest.mark.parametrize("channel", ["subject", "hub"])
async def test_synchronous_observer_selection_change_at_publication(
    context: Context, channel: str
) -> None:
    fs, pane, vm = context
    fs.queue_details(details())
    changed = False

    def mutate(prop: str) -> None:
        nonlocal changed
        if prop == "fields" and vm.state is S3ObjectDetailsState.READY and not changed:
            changed = True
            pane.move_cursor_command.execute(1)

    if channel == "subject":
        subscription = vm.on_property_changed.subscribe(mutate)
    else:
        subscription = vm._hub.messages.subscribe(
            lambda msg: (
                mutate(msg.property_name)
                if isinstance(msg, PropertyChangedMessage) and msg.sender_object is vm
                else None
            )
        )
    await load(vm)
    assert changed
    assert vm.fields == ()
    assert vm.state is S3ObjectDetailsState.LOADING
    assert "b.txt" in vm.title
    subscription.dispose()


async def test_observer_mutation_after_shutdown_begins_cannot_request_read(
    context: Context,
) -> None:
    fs, pane, vm = context
    barrier = DetailsReadBarrier(details(), cancellation_resistant=True)
    fs.queue_details(barrier)
    read = asyncio.create_task(load(vm))
    await wait(barrier.entered)
    vm.on_property_changed.subscribe(
        lambda _: (
            pane.move_cursor_command.execute(1) if vm.state is S3ObjectDetailsState.CLOSED else None
        )
    )
    shutdown = asyncio.create_task(vm.shutdown())
    await wait(barrier.cancelled)
    generation = vm.request_generation
    await load(vm)
    barrier.release.set()
    await asyncio.wait_for(asyncio.gather(read, shutdown), timeout=1)
    assert vm.request_generation == generation
    assert len(fs.details_paths) == 1


async def test_caller_cancellation_propagates(context: Context) -> None:
    fs, _, vm = context
    barrier = DetailsReadBarrier(details())
    fs.queue_details(barrier)
    task = asyncio.create_task(load(vm))
    await wait(barrier.entered)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert barrier.finished.is_set()


async def test_unknown_whole_read_error_is_safe_error_state(context: Context) -> None:
    fs, _, vm = context
    fs.queue_details(RuntimeError(SECRET_ERROR))
    await load(vm)
    assert vm.state is S3ObjectDetailsState.ERROR
    assert "[REDACTED]" in vm.fields[0].value
    assert "hidden" not in vm.fields[0].value
    assert vm.fields[0].copy_value is None


@pytest.mark.parametrize(("kind", "protocol"), [(None, "s3:"), ("local", "s3:"), ("aws", "")])
async def test_ineligible_source_does_not_read(
    context: Context, kind: str | None, protocol: str
) -> None:
    fs, pane, vm = context
    await pane.swap_provider(
        fs, path_protocol=protocol, connection_key=(kind, "fixture") if kind else None
    )
    await load(vm)
    assert vm.state is S3ObjectDetailsState.UNAVAILABLE
    assert fs.details_paths == []


async def test_no_capability_directory_and_parent_are_unavailable(context: Context) -> None:
    fs, pane, vm = context
    plain = InMemoryFS()
    await plain.write_stream(PathRef(("a.txt",)), stream())
    await pane.swap_provider(plain, path_protocol="s3:", connection_key=("aws", "fixture"))
    await load(vm)
    assert vm.state is S3ObjectDetailsState.UNAVAILABLE
    await fs.mkdir(PathRef(("directory",)))
    await pane.swap_provider(fs, path_protocol="s3:", connection_key=("s3-compatible", "fixture"))
    assert pane.selected_entry is not None
    assert pane.selected_entry.name == "directory"
    await load(vm)
    assert vm.state is S3ObjectDetailsState.UNAVAILABLE
    await pane.navigate_to(PathRef(("directory",)))
    assert pane.selected_entry is not None
    assert pane.selected_entry.is_parent_link
    await load(vm)
    assert vm.state is S3ObjectDetailsState.UNAVAILABLE
    assert fs.details_paths == []


async def test_refresh_invalidates_reply_and_title_remains_literal(context: Context) -> None:
    fs, pane, vm = context
    barrier = DetailsReadBarrier(details(content_type="old"), cancellation_resistant=True)
    fs.queue_details(barrier, details(content_type="refreshed"))
    read = asyncio.create_task(load(vm))
    await wait(barrier.entered)
    await pane.refresh()
    await load(vm)
    barrier.release.set()
    await asyncio.wait_for(read, timeout=1)
    assert fields(vm)["Content type"].value == "refreshed"
    await fs.write_stream(PathRef(("[bold]literal.txt",)), stream())
    await pane.refresh()
    assert "[bold]literal.txt" in vm.title


async def test_duplicate_loading_revision_does_not_start_second_read(context: Context) -> None:
    fs, _, vm = context
    barrier = DetailsReadBarrier(details())
    fs.queue_details(barrier)
    task = asyncio.create_task(load(vm))
    await wait(barrier.entered)
    await load(vm)
    assert len(fs.details_paths) == 1
    barrier.release.set()
    await asyncio.wait_for(task, timeout=1)


async def test_all_returned_fields_are_literal_and_copyable(context: Context) -> None:
    fs, _, vm = context
    fs.queue_details(
        details(
            content_encoding="gzip",
            storage_class="GLACIER",
            version_id="opaque-version",
            encryption=(("ServerSideEncryption", "aws:kms"), ("SSEKMSKeyId", "arn:literal")),
            metadata=(("name", '[bold]markup[/bold]\nquoted "text"'),),
            tags=(("token", "literal value"),),
        )
    )
    await load(vm)
    for label, expected in (
        ("Content encoding", "gzip"),
        ("Storage class", "GLACIER"),
        ("Version ID", "opaque-version"),
    ):
        assert fields(vm)[label].value == fields(vm)[label].copy_value == expected
    assert fields(vm)["Encryption"].copy_value is not None
    assert json.loads(cast(str, fields(vm)["Encryption"].copy_value)) == {
        "ServerSideEncryption": "aws:kms",
        "SSEKMSKeyId": "arn:literal",
    }
    assert fields(vm)["User metadata"].copy_value is not None
    assert json.loads(cast(str, fields(vm)["User metadata"].copy_value)) == {
        "name": '[bold]markup[/bold]\nquoted "text"'
    }
    assert fields(vm)["Tags"].copy_value is not None
    assert json.loads(cast(str, fields(vm)["Tags"].copy_value)) == {"token": "literal value"}


async def test_notifications_carry_only_property_names(context: Context) -> None:
    fs, _, vm = context
    messages: list[PropertyChangedMessage[S3ObjectDetailsVM]] = []
    sub = vm._hub.messages.subscribe(
        lambda msg: (
            messages.append(msg)
            if isinstance(msg, PropertyChangedMessage) and msg.sender_object is vm
            else None
        )
    )
    fs.queue_details(details(content_type="literal-private-content"))
    await load(vm)
    assert messages
    assert all(
        message.property_name in {"fields", "title", "state", "request_generation"}
        for message in messages
    )
    assert all(
        tuple(field.name for field in dataclass_fields(message))
        == ("sender", "sender_name", "property_name")
        for message in messages
    )
    sub.dispose()


async def test_construct_after_pane_disposal_closes_without_subscription(
    context: Context, caplog: pytest.LogCaptureFixture
) -> None:
    _, pane, vm = context
    vm.dispose()
    pane.dispose()
    closed = S3ObjectDetailsVM(pane=pane, hub=vm._hub, dispatcher=NULL_DISPATCHER)
    closed.construct()
    assert closed.state is S3ObjectDetailsState.CLOSED
    await load(closed)
    await closed.shutdown()
    closed.dispose()
    assert not caplog.records


async def test_shutdown_cancellation_still_drains_owned_read(context: Context) -> None:
    fs, _, vm = context
    barrier = DetailsReadBarrier(details(), cancellation_resistant=True)
    fs.queue_details(barrier)
    read = asyncio.create_task(load(vm))
    await wait(barrier.entered)
    shutdown = asyncio.create_task(vm.shutdown())
    await wait(barrier.cancelled)
    assert not shutdown.done()
    shutdown.cancel()
    barrier.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(shutdown, timeout=1)
    await asyncio.wait_for(read, timeout=1)
    assert barrier.finished.is_set()
    assert not vm._owner.tasks
    assert vm._subscription is None


async def test_cancellation_resistant_read_propagates_caller_cancellation(context: Context) -> None:
    fs, _, vm = context
    barrier = DetailsReadBarrier(details(), cancellation_resistant=True)
    fs.queue_details(barrier)
    read = asyncio.create_task(load(vm))
    await wait(barrier.entered)
    read.cancel()
    await wait(barrier.cancelled)
    barrier.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(read, timeout=1)
    assert vm.state is S3ObjectDetailsState.LOADING
    assert vm.fields == ()
    assert barrier.finished.is_set()


async def test_synchronous_observer_close_at_publication(context: Context) -> None:
    fs, _, vm = context
    fs.queue_details(details())
    subscription = vm.on_property_changed.subscribe(
        lambda _: vm.close() if vm.state is S3ObjectDetailsState.READY else None
    )
    await load(vm)
    assert vm.state is S3ObjectDetailsState.CLOSED
    assert vm.fields == ()
    assert vm._subscription is None
    subscription.dispose()


async def test_future_outcome_and_s3_compatible_source(context: Context) -> None:
    fs, pane, vm = context
    await pane.swap_provider(fs, path_protocol="s3:", connection_key=("s3-compatible", "fixture"))
    future: asyncio.Future[S3ObjectDetails] = asyncio.get_running_loop().create_future()
    future.set_result(details(content_type="future-result"))
    fs.queue_details(future)
    await load(vm)
    assert fields(vm)["Content type"].value == "future-result"

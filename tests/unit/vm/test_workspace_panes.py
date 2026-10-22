"""Each file pane owns its own source/region and literal location."""

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.local_fs import LocalFS
from aws_tui.domain.navigation_workspace import FileLocation, NavigationTarget
from aws_tui.vm.file_manager.pane_vm import PaneVM


@pytest.mark.asyncio
async def test_remote_capture_follows_atomic_source_region_changes():
    from aws_tui.vm.workspace_panes import capture_pane_target

    pane = PaneVM(
        provider=InMemoryFS(),
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        id_prefix="test",
        initial_path=PathRef(("bucket", "literal", "", "λ\\name")),
        path_protocol="s3:",
        connection_key=("aws", "left"),
        connection_region="us-east-1",
    )
    try:
        assert capture_pane_target(pane) == NavigationTarget(
            "s3",
            "left",
            "us-east-1",
            location=FileLocation(s3=("bucket", "literal", "", "λ\\name")),
        )
        await pane.swap_provider(
            InMemoryFS(),
            path_protocol="s3:",
            connection_key=("aws", "right"),
            connection_region="eu-west-1",
        )
        assert capture_pane_target(pane) == NavigationTarget(
            "s3", "right", "eu-west-1", location=FileLocation(s3=())
        )
        await pane.swap_provider(LocalFS(), path_protocol="", connection_key=None)
        assert capture_pane_target(pane) == NavigationTarget("s3", location=FileLocation(local="/"))
    finally:
        pane.dispose()


def test_rooted_local_capture_preserves_current_anchor(tmp_path):
    from aws_tui.vm.workspace_panes import capture_pane_target

    pane = PaneVM(
        provider=LocalFS(root=tmp_path),
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        id_prefix="local",
        initial_path=PathRef(("selected",)),
    )
    try:
        assert capture_pane_target(pane) == NavigationTarget(
            "s3", location=FileLocation(local=str(tmp_path / "selected"))
        )
    finally:
        pane.dispose()


@pytest.mark.asyncio
async def test_provider_recovery_stages_region_and_publishes_it_with_source():
    from aws_tui.vm.workspace_panes import capture_pane_target

    pane = PaneVM(
        provider=InMemoryFS(),
        hub=MessageHub(),
        dispatcher=NULL_DISPATCHER,
        id_prefix="test",
        path_protocol="s3:",
        connection_key=("aws", "before"),
        connection_region="us-east-1",
    )
    try:
        staged = await pane.stage_provider_recovery(
            InMemoryFS(),
            path=PathRef(),
            identity_label="after",
            path_protocol="s3:",
            connection_key=("aws", "after"),
            connection_region="eu-west-1",
        )
        assert pane.current_connection_region == "us-east-1"
        assert capture_pane_target(pane).connection == "before"
        pane.commit_provider_recovery(staged)
        assert pane.current_connection_region == "eu-west-1"
        assert capture_pane_target(pane) == NavigationTarget(
            "s3", "after", "eu-west-1", location=FileLocation(s3=())
        )
    finally:
        pane.dispose()

"""Actual independent-process app driver for navigation persistence acceptance."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from aws_tui import composition
from aws_tui.app import AwsTuiApp
from aws_tui.demo.in_memory_emr import InMemoryEmr
from aws_tui.demo.in_memory_fs import InMemoryFS
from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.workspace_store import WorkspaceStore
from aws_tui.infra.aws_session import TokenProbeResult, TokenState
from aws_tui.infra.keychain import InMemoryKeychain
from tests.helpers import drain_workers
from tests.integration.test_workspace_manager import focus_with_tabs, open_manager
from tests.unit.vm.athena.test_page_vm import PageClient
from tests.unit.vm.glue._fake_glue import InMemoryGlue

SQL_CANARY = "SELECT 'WORKSPACE_MUST_NOT_STORE_SQL_263'"


class ReadAudit:
    """Fail at the provider boundary on execution, mutation or content replay."""

    def __getattribute__(self, name):
        value = super().__getattribute__(name)
        if not name.startswith("_") and (
            inspect.iscoroutinefunction(value) or inspect.isasyncgenfunction(value)
        ):

            def checked(*args, **kwargs):
                self.audit.append(name)
                assert name.startswith(("list", "get", "stat")), name
                assert name not in {"get_query_results", "get_results_page"}, name
                return value(*args, **kwargs)

            return checked
        return value


class Files(ReadAudit, InMemoryFS):
    pass


class Athena(ReadAudit, PageClient):
    pass


class Glue(ReadAudit, InMemoryGlue):
    pass


class Emr(ReadAudit, InMemoryEmr):
    pass


async def drive(root: Path, mode: str, active: str, size: tuple[int, int]) -> None:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(composition, "Keyring", InMemoryKeychain)
        ctx = composition.build_app_context(config_dir=root / "config", cache_dir=root / mode)
        ctx.aws_session.probe_token = lambda _connection: TokenProbeResult(TokenState.CONNECTED)
        providers = {}
        for name, region in (("dev", "us-east-1"), ("other", "us-west-2")):
            fs = Files()
            fs.audit = []
            await InMemoryFS.mkdir(fs, PathRef(("bucket", "chosen")))
            athena = Athena(connection_name=name, region=region)
            athena.audit = []
            glue = Glue(connection_name=name, region=region)
            glue.audit = []
            glue.add_table("a-first", "first")
            glue.add_table("lake", "events")
            emr = Emr()
            emr.audit = []
            emr.add_application(app_id="a-first", name="First")
            emr.add_application(app_id="chosen", name="Chosen")
            providers[name] = {"s3": fs, "athena": athena, "glue": glue, "emr-serverless": emr}
        s3 = ctx.registry.get("s3")
        s3._local_root = root / "local"
        s3._s3_fs_factory = lambda connection: providers[connection.name]["s3"]
        for identity in ("athena", "glue", "emr-serverless"):
            ctx.registry.get(identity)._client_factory = lambda connection, identity=identity: (
                providers[connection.name][identity]
            )
        ctx.registry.get("glue")._athena_client_factory = lambda c: providers[c.name]["athena"]
        app = AwsTuiApp(ctx)
        store = WorkspaceStore(path=root / "config" / "workspaces.json")

        async def visit(identity, pilot):
            ctx.root_vm.services_menu.switch_service_command.execute(identity)
            await drain_workers(app)
            await app._wait_for_current_service_setup()
            await pilot.pause()
            assert ctx.root_vm.content_host.current_id == identity
            return ctx.root_vm.content_host.current

        try:
            async with app.run_test(size=size) as pilot:
                await drain_workers(app)
                if mode == "save":
                    for identity in ("athena", "glue", "emr-serverless"):
                        await visit(identity, pilot)
                        for name in ("dev", "other"):
                            source = ctx.connection_resolver.resolve_selected(name)
                            assert await app._rebuild_single_context_source(identity, source)
                            await drain_workers(app)
                            await app._wait_for_current_service_setup()
                            await pilot.pause()
                            page = ctx.root_vm.content_host.current
                            if identity == "athena":
                                await page.select_workgroup("analysts")
                                await page.select_database("events")
                            elif identity == "glue":
                                await page.select_database("lake")
                                await page.select_table("events")
                            else:
                                await page.select_application("chosen")
                            await drain_workers(app)
                            await pilot.pause()
                            targets = ctx.registry.get(identity).navigation_targets()
                            assert any(target.connection == name for target in targets), (
                                identity,
                                name,
                                targets,
                            )
                            chosen = next(target for target in targets if target.connection == name)
                            assert (
                                dict(chosen.selectors)
                                == {
                                    "athena": {
                                        "workgroup": "analysts",
                                        "catalog": "AwsDataCatalog",
                                        "database": "events",
                                    },
                                    "glue": {"database": "lake", "table": "events"},
                                    "emr-serverless": {"application": "chosen"},
                                }[identity]
                            )
                    panes = await visit("s3", pilot)
                    await app._rebind_pane_to_connection(
                        panes.left, ctx.connection_resolver.resolve_selected("dev")
                    )
                    await panes.left.navigate_to(PathRef(("bucket", "chosen")))
                    await panes.right.navigate_to(PathRef(("chosen",)))
                    if active == "athena":
                        page = await visit("athena", pilot)
                        page.query.set_sql(SQL_CANARY)
                        await pilot.pause()
                    expected = app._capture_navigation_workspace("Across processes")
                    assert len(expected.scopes) == 6, repr(expected.scopes)
                    await open_manager(app, pilot)
                    await focus_with_tabs(app, pilot, "workspace-name")
                    await pilot.press(*"Across processes")
                    await focus_with_tabs(app, pilot, "workspace-save")
                    await pilot.press("enter")
                    await drain_workers(app)
                    assert store.list() == (expected,)
                    await pilot.press("escape")
                else:
                    assert all(
                        ctx.registry.get(identity).navigation_targets() == ()
                        for identity in ("athena", "glue", "emr-serverless")
                    )
                    original = ctx.root_vm.content_host.current
                    await open_manager(app, pilot)
                    assert [item.name for item in app.screen.vm.items] == ["Across processes"]
                    await focus_with_tabs(app, pilot, "workspace-open")
                    await pilot.press("enter")
                    await drain_workers(app)
                    await pilot.pause()
                    expected = store.list()[0]
                    assert ctx.root_vm.content_host.current is not original
                    assert ctx.root_vm.content_host.current_id == active
                    assert app._capture_navigation_workspace("Across processes") == expected
                    assert ctx.root_vm.active_connection.name == "other"
                    for identity in ("athena", "glue", "emr-serverless"):
                        assert set(ctx.registry.get(identity).navigation_targets()) == {
                            target for target in expected.scopes if target.service == identity
                        }
                    assert s3.navigation_panes() == expected.file_panes
                    if active == "athena":
                        page = ctx.root_vm.content_host.current
                        assert SQL_CANARY not in page.query.sql
                assert not app.crash_report
                report = {
                    "pid": os.getpid(),
                    "mode": mode,
                    "active": active,
                    "workspace": asdict(expected),
                    "calls": {
                        name: {key: client.audit for key, client in clients.items()}
                        for name, clients in providers.items()
                    },
                }
                (root / f"{mode}.json").write_text(json.dumps(report, indent=2))
        finally:
            ctx.root_vm.dispose()
            ctx.log_sink.close()
            for clients in providers.values():
                clients["emr-serverless"].dispose()


if __name__ == "__main__":
    asyncio.run(
        drive(Path(sys.argv[1]), sys.argv[2], sys.argv[3], (int(sys.argv[4]), int(sys.argv[5])))
    )

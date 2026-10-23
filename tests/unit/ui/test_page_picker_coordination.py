"""Page-owned picker exclusivity and legacy optional-picker compatibility."""

from __future__ import annotations

import pytest
from textual.app import App

from aws_tui.ui.widgets.athena.page import AthenaPage
from aws_tui.ui.widgets.context_picker import ContextOption, ContextPicker
from aws_tui.ui.widgets.emr_serverless.application_picker import ApplicationPicker
from aws_tui.ui.widgets.emr_serverless.page import EmrServerlessPage
from aws_tui.ui.widgets.glue.page import GluePage
from tests.helpers import wait_until
from tests.snapshot.apps.athena import AthenaPageApp
from tests.snapshot.apps.emr import EmrPageApp
from tests.snapshot.apps.glue import GluePageApp


def _app(service: str) -> App[None]:
    if service == "glue":
        return GluePageApp(theme="carbon")
    if service == "athena":
        return AthenaPageApp(theme="carbon", fixture="success-results")
    return EmrPageApp(theme="carbon")


def _page(app: App[None]) -> GluePage | AthenaPage | EmrServerlessPage:
    return app.query_one(
        GluePage
        if isinstance(app, GluePageApp)
        else AthenaPage
        if isinstance(app, AthenaPageApp)
        else EmrServerlessPage
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("service", "count"), [("glue", 3), ("athena", 5), ("emr", 2)])
async def test_every_page_picker_shares_its_synchronous_open_intent(
    service: str, count: int
) -> None:
    app = _app(service)
    async with app.run_test() as pilot:
        await pilot.pause()
        page = _page(app)
        pickers = [*page.query(ContextPicker), *page.query(ApplicationPicker)]
        assert len(pickers) == count
        assert all(picker.open_intent is page._picker_open_intent for picker in pickers)


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["glue", "athena", "emr"])
async def test_stale_coordinated_open_message_cannot_replay_ownership(service: str) -> None:
    app = _app(service)
    async with app.run_test() as pilot:
        await pilot.pause()
        page = _page(app)
        first = page.query_one(ContextPicker)
        newest = (
            page.query_one(ApplicationPicker)
            if service == "emr"
            else next(picker for picker in page.query(ContextPicker) if picker is not first)
        )
        first.open()
        newest.open()
        intent = page._picker_open_intent
        assert newest.is_open
        assert not first.is_open
        epoch = intent.epoch
        page.on_context_picker_open_changed(ContextPicker.OpenChanged(first, True))
        assert intent.epoch == epoch
        assert intent.desired is newest
        assert newest.is_open
        assert not first.is_open
        await pilot.pause()
        assert intent.desired is newest
        assert newest.is_open
        assert not first.is_open


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["glue", "athena", "emr"])
async def test_intentless_picker_message_still_closes_the_previous_owner(service: str) -> None:
    app = _app(service)
    async with app.run_test() as pilot:
        await pilot.pause()
        page = _page(app)
        previous = page.query_one(ContextPicker)
        legacy = ContextPicker("Legacy", (ContextOption("one", "one"),), selected="one")
        await page.mount(legacy)
        previous.open()
        assert previous.is_open
        legacy.open()
        await wait_until(
            lambda: page._picker_open_intent.desired is legacy,
            what="intent-less picker fallback observed",
        )
        assert legacy.is_open
        assert not previous.is_open
        legacy.close(refocus=False)
        await wait_until(
            lambda: page._picker_open_intent.desired is None,
            what="intent-less picker close cleared ownership",
        )

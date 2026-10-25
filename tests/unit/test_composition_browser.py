"""Browser capability is injected, with a mandatory demo deny policy."""

from aws_tui.composition import build_app_context
from aws_tui.infra.browser import DisabledBrowser, NativeBrowser


def test_browser_port_injection_and_demo_denial(tmp_path):
    injected = DisabledBrowser()
    for demo in (False, True):
        ctx = build_app_context(
            config_dir=tmp_path / str(demo) / "config",
            cache_dir=tmp_path / str(demo) / "cache",
            demo=demo,
            browser=injected,
        )
        try:
            assert isinstance(ctx.browser, DisabledBrowser)
            assert (ctx.browser is injected) is (not demo)
        finally:
            ctx.close_unstarted()


def test_production_browser_default_has_no_dispatch_at_construction(tmp_path, monkeypatch):
    import webbrowser

    def forbidden(*args, **kwargs):
        raise AssertionError("construction must not launch a browser")

    monkeypatch.setattr(webbrowser, "open", forbidden)
    ctx = build_app_context(config_dir=tmp_path / "config", cache_dir=tmp_path / "cache")
    try:
        assert isinstance(ctx.browser, NativeBrowser)
    finally:
        ctx.close_unstarted()

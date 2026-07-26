from pathlib import Path

ROOT = Path(__file__).parents[2]


def _text(path: str) -> str:
    source = (ROOT / path).read_text(encoding="utf-8").replace("\n> ", "\n")
    return " ".join(source.split())


def test_readme_describes_shipped_runtime_bindings_quick_look_and_palette() -> None:
    text = _text("README.md")

    assert "runtime rebinding deferred" not in text
    assert "Streaming Quick Look (deferred)" not in text
    assert "Command palette (deferred)" not in text
    assert "`BindingResolver` installs handled `[keybindings]` overrides at runtime" in text
    assert "**Streaming Quick Look.** Press `Space`" in text
    assert "**Command palette.** Press `:` or `Ctrl+K`" in text


def test_cookbook_describes_live_keybinding_overrides() -> None:
    text = _text("docs/cookbook.md")

    assert "Runtime dispatch still uses `AwsTuiApp.BINDINGS`" not in text
    assert "so `d` still follows `AwsTuiApp.BINDINGS`" not in text
    assert "The composition root installs handled overrides on the live Textual keymap" in text
    assert "an empty `[keybindings]` value removes the live keybinding" in text


def test_keybindings_describes_shipped_palette_and_runtime_resolver() -> None:
    text = _text("docs/keybindings.md")

    assert "live `AwsTuiApp.BINDINGS`" not in text
    assert "wired directly in `AwsTuiApp.BINDINGS`" not in text
    assert "the palette open binding is deferred" not in text
    assert "The command palette opens today" in text
    assert "All live App-level bindings are installed through `BindingResolver`" in text


def test_unreleased_changelog_does_not_contradict_shipped_handlers_or_demo() -> None:
    unreleased = _text("CHANGELOG.md").split("## 1.2.", maxsplit=1)[0]

    assert "(Quick Look, command palette) still need their own handlers" not in unreleased
    assert "seeded in-memory S3 + EMR fakes" not in unreleased
    assert "Quick Look and the command palette now register their handlers" in unreleased
    assert "seeded in-memory S3, EMR, and Glue fakes" in unreleased

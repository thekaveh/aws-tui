from pathlib import Path

ROOT = Path(__file__).parents[2]


def _text(path: str) -> str:
    source = (ROOT / path).read_text(encoding="utf-8").replace("\n> ", "\n")
    return " ".join(source.split())


def test_readme_describes_shipped_runtime_bindings_quick_look_and_palette() -> None:
    text = _text("README.md")

    assert "runtime rebinding deferred" not in text
    assert "runtime wiring is deferred to v0.9 (the `BindingResolver` work" not in text
    assert "pending `[keybindings]` overlay contract" not in text
    assert "Streaming Quick Look (deferred)" not in text
    assert "Command palette (deferred)" not in text
    assert "`BindingResolver` installs handled `[keybindings]` overrides at runtime" in text
    assert "Handlerless action IDs, including `auth.authenticate`, remain unbound" in text
    assert "shipped `[keybindings]` overlay behavior" in text
    assert "**Streaming Quick Look.** Press `Space`" in text
    assert "**Command palette.** Press `:` or `Ctrl+K`" in text


def test_cookbook_describes_live_keybinding_overrides() -> None:
    text = _text("docs/cookbook.md")
    changelog = _text("CHANGELOG.md").split("## 1.2.", maxsplit=1)[0]
    active_docs = f"{text}\n{changelog}"

    assert "Runtime dispatch still uses `AwsTuiApp.BINDINGS`" not in text
    assert "so `d` still follows `AwsTuiApp.BINDINGS`" not in text
    assert "The composition root installs handled overrides on the live Textual keymap" in text
    assert "an empty `[keybindings]` value removes the live keybinding" in text
    assert '"pane.copy" = "ctrl+y"' in text
    assert '"pane.copy" = "y"' not in active_docs
    assert 'pane.copy = "y"' not in active_docs


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


def test_current_docs_do_not_claim_deleted_first_run_or_resume_modals() -> None:
    current = " ".join(
        _text(path)
        for path in (
            "README.md",
            "docs/architecture.md",
            "docs/connections.md",
            "docs/recording-todo.md",
        )
    )
    unreleased = _text("CHANGELOG.md").split("## 1.2.", maxsplit=1)[0]

    assert "welcome modal exists" not in current.lower()
    assert "resume modal pops up" not in current.lower()
    assert "FirstRunModal" not in current
    assert "ResumeModal" not in current
    assert "overlays like command palette / confirm / quick look / crash / first-run" not in current
    assert "first-run persistence store credentials" not in unreleased
    assert "First-run S3-compatible save failures" not in unreleased
    assert "Settings and first-run now share" not in unreleased


def test_contributing_documents_gitflow_base_branches() -> None:
    text = _text("CONTRIBUTING.md")

    assert "Branch feature, fix, and maintenance work from `develop`" in text
    assert "Reserve `main` for release-promotion PRs from `develop`" in text


def test_current_keybinding_guide_avoids_platform_and_pr_chronology() -> None:
    keybindings = _text("docs/keybindings.md")

    assert "macOS-tailored" not in keybindings
    assert "PR #" not in keybindings
    assert "post-tag" not in keybindings


def test_release_checklist_covers_published_package_and_platform_status() -> None:
    releasing = _text("docs/RELEASING.md")

    assert "PyPI project status" in releasing
    assert "Clean install smoke" in releasing
    assert "Supported-platform status" in releasing


def test_installed_help_and_current_docs_use_executable_contracts() -> None:
    app = _text("src/aws_tui/app.py")
    help_modal = _text("src/aws_tui/ui/widgets/help_modal.py")
    s3 = _text("docs/services/s3.md")
    theming = _text("docs/theming.md")
    recording = _text("docs/recording-todo.md")

    docs_url = "https://thekaveh.github.io/aws-tui/"
    assert docs_url in app
    assert docs_url in help_modal
    assert "See [b]docs/connections.md[/] in the repo" not in app
    assert '"  docs/connections.md' not in help_modal
    assert "show or hide dotfiles with `.`" not in s3
    assert 'THEME_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/aws-tui/themes"' in theming
    assert '> "$THEME_DIR/midnight.tcss"' in theming
    assert "The crash dump writer and interactive crash modal are live" not in recording
    assert "The crash dump writer is live" in recording
    assert "is not wired into the unhandled exception path" in recording
    assert "v0.9.0 development docs" in recording
    assert "S3Mock" in recording


def test_current_contract_ledger_discloses_exact_pinned_private_adapters() -> None:
    ledger = _text("docs/contract-ledger.md")

    assert "Textual compatibility adapter uses exact-version private hooks" in ledger
    for private_name in ("`_bindings`", "`_pre_process`", "`_handle_exception`"):
        assert private_name in ledger

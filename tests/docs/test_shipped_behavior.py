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
    assert "Handlerless deferred action IDs remain unbound" in text
    assert "shipped `[keybindings]` overlay behavior" in text
    assert "**Streaming Quick Look.** Press `Space`" in text
    assert "**Command palette.** Press `:` or `Ctrl+K`" in text


def test_current_docs_describe_in_session_credential_recovery() -> None:
    readme = _text("README.md")
    keybindings = _text("docs/keybindings.md")
    connections = _text("docs/connections.md")
    s3 = _text("docs/services/s3.md")

    assert "press `a` to retry the active source in place" in readme
    assert "**Retry active source credentials**" in keybindings
    assert "aws-tui never runs the AWS CLI or writes credentials" in connections
    assert "expired SSO, missing credentials, access denied, and network failures" in connections
    assert "preserves each matching pane's current path" in s3
    assert "falls back to remote root only when a saved non-root path no longer exists" in s3


def test_cookbook_describes_live_keybinding_overrides() -> None:
    text = _text("docs/cookbook.md")
    # Scope to [Unreleased] by splitting at the next release heading. The
    # previous token, "## 1.2.", occurs nowhere in CHANGELOG.md, so the split
    # was a no-op and all three guards below silently asserted against the
    # whole 131k-character file: the positive assertions could be satisfied
    # by any historical section, and the negative ones over-constrained
    # released history.
    changelog = _text("CHANGELOG.md").split("## [0.8.0]", maxsplit=1)[0]
    active_docs = f"{text}\n{changelog}"

    assert "Runtime dispatch still uses `AwsTuiApp.BINDINGS`" not in text
    assert "so `d` still follows `AwsTuiApp.BINDINGS`" not in text
    assert "The composition root installs handled overrides on the live Textual keymap" in text
    assert "an empty `[keybindings]` value removes the live keybinding" in text
    assert '"pane.copy" = "ctrl+y"' in text
    assert '"pane.copy" = "y"' not in active_docs
    assert 'pane.copy = "y"' not in active_docs


def test_connections_uses_the_literal_environment_prefix_contract() -> None:
    text = _text("docs/connections.md")

    assert "`env:PREFIX_`" in text
    assert "`env:PREFIX_*`" not in text
    assert 'credentials = "keychain:minio-local" # or env:PREFIX_' in text


def test_keybindings_describes_shipped_palette_and_runtime_resolver() -> None:
    text = _text("docs/keybindings.md")

    assert "live `AwsTuiApp.BINDINGS`" not in text
    assert "wired directly in `AwsTuiApp.BINDINGS`" not in text
    assert "the palette open binding is deferred" not in text
    assert "Help (`?`) and the command palette (`:` / `Ctrl+K`)" in text
    assert "Use &lt;name&gt; · &lt;region&gt; for &lt;service&gt;" in text
    assert "Dynamic `connection switch <name>` palette entries are not registered" not in text
    assert "All live App-level bindings are installed through `BindingResolver`" in text


def test_unreleased_changelog_does_not_contradict_shipped_handlers_or_demo() -> None:
    unreleased = _text("CHANGELOG.md").split("## [0.8.0]", maxsplit=1)[0]

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
    unreleased = _text("CHANGELOG.md").split("## [0.8.0]", maxsplit=1)[0]

    assert "welcome modal exists" not in current.lower()
    assert "resume modal pops up" not in current.lower()
    assert "FirstRunModal" not in current
    assert "ResumeModal" not in current
    assert "overlays like command palette / confirm / quick look / crash / first-run" not in current
    for path in ("README.md", "docs/connections.md"):
        text = _text(path)
        assert "**Save and open**" in text
        assert "**Retry discovery**" in text
        assert "No first-run" not in text
        assert "local-only placeholder" not in text
    assert "In-session connection setup (#243)" in unreleased
    assert "Setup leaves AWS config and credentials files unchanged" in unreleased


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


def test_sso_startup_docs_promise_only_local_no_network_io() -> None:
    surfaces = (_text("README.md"), _text("docs/connections.md"))

    for content in surfaces:
        assert "local AWS config and SSO cache reads only; no AWS network call" in content
        assert "~1 KB" not in content
        assert "Sub-millisecond" not in content
        assert "one `stat`" not in content
        assert "one `os.stat`" not in content


def test_installed_help_and_current_docs_use_executable_contracts() -> None:
    app = _text("src/aws_tui/app.py")
    help_modal = _text("src/aws_tui/ui/widgets/help_modal.py")
    s3 = _text("docs/services/s3.md")
    theming = _text("docs/theming.md")
    recording = _text("docs/recording-todo.md")

    docs_url = "https://thekaveh.github.io/aws-tui/"
    first_run = _text("src/aws_tui/ui/widgets/first_run.py")
    assert "from aws_tui.ui.widgets.first_run import" in app
    assert docs_url in first_run
    assert docs_url in help_modal
    assert "See [b]docs/connections.md[/] in the repo" not in app
    assert '"  docs/connections.md' not in help_modal
    assert "show or hide dotfiles with `.`" not in s3
    assert 'THEME_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/aws-tui/themes"' in theming
    assert '> "$THEME_DIR/midnight.tcss"' in theming
    assert "The crash dump writer and interactive crash modal are live" not in recording
    assert "The crash dump writer is live" in recording
    assert "is not wired into the unhandled exception path" in recording
    assert "subagent" not in recording.casefold()
    assert "v0.9.0 development docs" in recording
    assert "S3Mock" in recording


def test_current_contract_ledger_discloses_exact_pinned_private_adapters() -> None:
    ledger = _text("docs/contract-ledger.md")

    assert "Textual compatibility adapter uses exact-version private hooks" in ledger
    for private_name in (
        "`_bindings`",
        "`_pre_process`",
        "`_handle_exception`",
        "`Screen._clear_tooltip`",
        # The bracketed-paste guard subclasses Textual's private input parser
        # and rebinds the name each driver builds it from; a private surface
        # that the exact 8.2.8 pin is what makes safe.
        "`_xterm_parser.XTermParser`",
    ):
        assert private_name in ledger


def test_launch_selectors_document_values_precedence_and_session_contract() -> None:
    for path in ("README.md", "docs/cookbook.md"):
        text = _text(path)
        for flag in ("--connection", "--profile", "--region", "--service", "--location"):
            assert flag in text
        for command in (
            "aws-tui --connection dev --region eu-west-1 --service athena",
            "aws-tui --profile analytics --location 's3://reports-bucket/daily/'",
            "aws-tui --location './downloads'",
        ):
            assert command in text
        for claim in (
            "mutually exclusive",
            "[defaults].connection",
            "AWS_PROFILE",
            "session only",
            "left pane",
            "right pane",
            "local-only",
            "no configuration is saved",
            "one-line",
            "doctor",
            "AWS_TUI_DEMO",
            "emr-serverless",
            "literal",
            "existing native directory",
        ):
            assert claim in text


def test_quick_look_formats_controls_and_budgets_documented() -> None:
    readme = (ROOT / "README.md").read_text()
    keys = (ROOT / "docs/keybindings.md").read_text()
    cookbook = (ROOT / "docs/cookbook.md").read_text()
    for text in (readme, keys, cookbook):
        for needle in ("CSV", "JSON", "JSONL", "Parquet", "`r`", "64 KiB"):
            assert needle in text
    for needle in (
        "32 physical file requests",
        "8 MiB",
        "512 KiB",
        "32 MiB",
        "50 rows",
        "24 columns",
        "five-second",
        "truncated",
        "Left",
        "Right",
        "Preview timed out",
        "Encrypted Parquet preview is not supported",
    ):
        assert needle in cookbook
    assert "hard native-memory limit" in cookbook
    assert "without another file read" in cookbook

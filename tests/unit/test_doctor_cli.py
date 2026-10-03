"""Real CLI dispatch over synthetic local configuration, with side effects barred."""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

import aioboto3
import botocore.session
import keyring
import pytest

from aws_tui import app as app_module
from aws_tui.infra.doctor import DoctorCheck


def forbidden(*_args, **_kwargs):
    pytest.fail("doctor must not compose, launch, resolve providers, or perform external I/O")


@pytest.fixture
def healthy_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".config" / "aws-tui"
    config.mkdir(parents=True)
    (config / "config.toml").write_text("[defaults]\nconnection = 'dev'\n")
    aws = home / ".aws"
    aws.mkdir()
    (aws / "config").write_text("[profile dev]\nregion = us-east-1\n")
    (aws / "credentials").write_text(
        "[dev]\naws_access_key_id = synthetic-access\naws_secret_access_key = synthetic-secret\n"
    )
    for key in tuple(os.environ):
        if key.startswith("AWS_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(aws / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(aws / "credentials"))
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(keyring, "get_password", forbidden)
    monkeypatch.setattr(keyring, "set_password", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(aioboto3, "Session", forbidden)
    monkeypatch.setattr(botocore.session.Session, "get_credentials", forbidden)
    monkeypatch.setattr(app_module, "build_app_context", forbidden)
    monkeypatch.setattr(app_module, "prefer_sigwinch_resize", forbidden)
    monkeypatch.setattr(app_module.AwsTuiApp, "run", forbidden)
    monkeypatch.setattr(app_module, "probe_source", forbidden, raising=False)
    return home


def snapshot(home):
    return {
        str(path.relative_to(home)): (
            stat.S_IMODE(path.stat().st_mode),
            path.read_bytes() if path.is_file() else None,
        )
        for path in [home, *sorted(home.rglob("*"))]
    }


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("demo_env", [False, True])
@pytest.mark.parametrize("invalid_config", [False, True])
def test_doctor_local_report_never_composes_or_writes(
    monkeypatch, capsys, healthy_home, json_output, demo_env, invalid_config
):
    if invalid_config:
        (healthy_home / ".config/aws-tui/config.toml").write_text("[defaults\n")
    if demo_env:
        monkeypatch.setenv("AWS_TUI_DEMO", "1")
    before = snapshot(healthy_home)
    monkeypatch.setattr(sys, "argv", ["aws-tui", "doctor", *(["--json"] if json_output else [])])

    with pytest.raises(SystemExit) as exit_info:
        app_module.main()

    assert exit_info.value.code == (1 if invalid_config else 0)
    captured = capsys.readouterr()
    assert captured.err == ""
    if json_output:
        payload = json.loads(captured.out)
        assert type(payload["schema_version"]) is int
        assert payload["schema_version"] == 1
        checks = payload["checks"]
        assert all({"name", "result", "context", "next_step"} <= check.keys() for check in checks)
        assert [check["result"] for check in checks if check["name"] == "config"] == [
            "invalid_config" if invalid_config else "ok"
        ]
        assert [check["result"] for check in checks if check["name"] == "probe"] == ["skipped"]
        assert str(healthy_home / ".aws/config") in captured.out
    else:
        assert ("config: invalid_config" if invalid_config else "config: ok") in captured.out
        assert "probe: skipped" in captured.out
        assert "Next step:" in captured.out
    assert "synthetic-secret" not in captured.out
    assert snapshot(healthy_home) == before


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize(("probe_result", "code"), [("ok", 0), ("denied", 1)])
def test_explicit_probe_replaces_skipped_row_and_preserves_local_checks(
    monkeypatch, capsys, healthy_home, json_output, probe_result, code
):
    probe_calls = []

    def fake_probe(name):
        assert name == "exact source [name]"
        probe_calls.append(name)
        return DoctorCheck("probe", probe_result, {"source": "1"}, "Synthetic result.", code == 1)

    monkeypatch.setattr(app_module, "probe_source", fake_probe, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aws-tui",
            "doctor",
            "--probe",
            "exact source [name]",
            *(["--json"] if json_output else []),
        ],
    )
    with pytest.raises(SystemExit) as exit_info:
        app_module.main()
    assert exit_info.value.code == code
    assert probe_calls == ["exact source [name]"]
    output = capsys.readouterr().out
    if json_output:
        checks = json.loads(output)["checks"]
        assert [check["result"] for check in checks if check["name"] == "probe"] == [probe_result]
        assert {check["name"] for check in checks} == {
            "version",
            "runtime",
            "paths",
            "config",
            "keymap",
            "sources",
            "auth",
            "offline",
            "probe",
        }
    else:
        assert output.count("probe:") == 1
        assert f"probe: {probe_result}" in output
        assert "config: ok" in output
        assert "offline: unverified" in output


@pytest.mark.parametrize(
    "arguments",
    [
        ["doctor", "--probe"],
        ["doctor", "--wat"],
        ["doctor", "extra"],
        ["doctor", "--demo"],
        ["--demo", "doctor"],
        ["doctor", "--version"],
        ["--version", "doctor"],
    ],
)
def test_doctor_usage_errors_exit_two_before_composition(
    monkeypatch, capsys, healthy_home, arguments
):
    monkeypatch.setattr(sys, "argv", ["aws-tui", *arguments])
    with pytest.raises(SystemExit) as exit_info:
        app_module.main()
    assert exit_info.value.code == 2
    output = capsys.readouterr()
    assert "usage:" in output.err
    assert "error:" in output.err
    assert output.out == ""


def test_doctor_help_explains_flags_without_collecting(monkeypatch, capsys, healthy_home):
    monkeypatch.setattr(app_module, "collect_local_diagnostics", forbidden)
    monkeypatch.setattr(sys, "argv", ["aws-tui", "doctor", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        app_module.main()
    assert exit_info.value.code == 0
    assert "--probe" in capsys.readouterr().out

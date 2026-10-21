"""Execute the cookbook's independent connection routes with isolated services."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from vmx import NULL_DISPATCHER, MessageHub

from aws_tui.domain.filesystem import PathRef
from aws_tui.domain.s3_fs import S3FS
from aws_tui.infra.config_store import ConfigStore
from aws_tui.infra.connection_resolver import ConnectionResolver
from aws_tui.infra.keychain import InMemoryKeychain
from aws_tui.vm.settings.s3_compat_form import S3CompatForm
from aws_tui.vm.settings.s3_connections_vm import S3ConnectionsVM, entry_from_s3_form

pytestmark = pytest.mark.unit
_ROOT = Path(__file__).parents[3]


def _example(section: str) -> str:
    guide = (_ROOT / "docs/cookbook.md").read_text()
    text = guide.split(f"### {section}.", 1)[1].split("### ", 1)[0]
    return re.findall(r"```toml\n(.*?)\n```", text, re.S)[0]


@pytest.mark.parametrize("route", ["settings", "static"])
async def test_documented_routes_create_one_consumed_connection(
    tmp_path, monkeypatch, s3_endpoint, route
):
    """Use real seed/provider HTTP; only keychain storage and endpoint are isolated."""
    spec = importlib.util.spec_from_file_location(
        "s3mock_recipe_seed", _ROOT / "scripts/test-services/s3/seed.py"
    )
    assert spec is not None
    assert spec.loader is not None
    seed = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, seed)
    spec.loader.exec_module(seed)
    monkeypatch.setattr(seed, "ENDPOINT", s3_endpoint)
    monkeypatch.setattr(seed, "ACCESS_KEY", "test")
    monkeypatch.setattr(seed, "SECRET_KEY", "test")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-aws-credentials"))
    assert await seed.main() == 0

    store = ConfigStore(path=tmp_path / "config.toml")
    keychain = InMemoryKeychain()
    resolver = ConnectionResolver(
        config_store=store,
        keychain=keychain,
        aws_config_path=tmp_path / "no-aws-config",
        aws_credentials_path=tmp_path / "no-aws-credentials",
    )
    example = _example("1.3" if route == "settings" else "1.4")
    # Substitute only the listener address; preserve every documented setting.
    example = example.replace("http://localhost:9000", s3_endpoint)
    expected = tomllib.loads(example)["connections"]
    assert list(expected) == ["s3mock-local"]
    if route == "static":
        store.path.write_text(example)
    else:
        guide = (_ROOT / "docs/cookbook.md").read_text()
        section = guide.split("### 1.3.", 1)[1].split("### 1.4.", 1)[0]
        values = dict(re.findall(r"\| ([^|]+?) \| `([^`]+)` \|", section))
        vm = S3ConnectionsVM(
            resolver=resolver,
            config_store=store,
            keychain=keychain,
            hub=MessageHub(),
            dispatcher=NULL_DISPATCHER,
        )
        vm.construct()
        try:
            await vm.add_async(
                entry_from_s3_form(
                    S3CompatForm(
                        name=values["Name"],
                        endpoint_url=values["Endpoint URL"].replace(
                            "http://localhost:9000", s3_endpoint
                        ),
                        region=values["Region"],
                        access_key_id=values["Access key ID"],
                        secret_access_key=values["Secret access key"],
                    )
                )
            )
        finally:
            vm.dispose()
        illustrated = ConfigStore(path=tmp_path / "illustrated.toml")
        illustrated.path.write_text(example)
        # Compare parsed settings: ConfigStore can omit true/default fields on disk.
        assert store.load().connections == illustrated.load().connections
        assert keychain._store == {
            ("aws-tui:connections/s3mock-local", "access_key_id"): "test",
            ("aws-tui:connections/s3mock-local", "secret_access_key"): "test",
        }
        assert "secret_access_key" not in store.path.read_text()

    assert list(store.load().connections) == ["s3mock-local"]
    connection = resolver.resolve_selected("s3mock-local")
    assert (connection.access_key_id, connection.secret_access_key) == ("test", "test")
    assert connection.session_token is None
    if route == "static":
        assert keychain._store == {}
    session = seed.aioboto3.Session(
        aws_access_key_id=connection.access_key_id,
        aws_secret_access_key=connection.secret_access_key,
        region_name=connection.region,
    )
    fs = S3FS(
        session=session,
        bucket=None,
        endpoint_url=connection.endpoint_url,
        force_path_style=connection.force_path_style,
        verify_tls=connection.verify_tls,
    )
    assert {entry.name for entry in await fs.list(PathRef(()))} == set(seed._BUCKETS)
    stream = await fs.read_stream(PathRef(("aws-tui-dev-unicode", "résumé.pdf")))
    content = b"".join([chunk async for chunk in stream])
    assert content == seed._unicode_seed()[0].body


_TOOL = """
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['RECIPE_CALLS'], 'a') as stream:
    stream.write(json.dumps([name, str(Path.cwd()), *args]) + '\\n')
if name == 'docker':
    if args[0] == 'inspect':
        print('healthy')
    elif args[-2:] == ['up', '-d'] and os.environ['RECIPE_MODE'] == 'start-failure':
        sys.exit(7)
elif name == 'uv':
    if args == ['--version']:
        print('uv 0.13.4')
    elif os.environ['RECIPE_MODE'] == 'seed-failure':
        sys.exit(8)
else:
    sys.exit(127)
"""


@pytest.mark.parametrize("mode", ["success", "start-failure", "seed-failure"])
@pytest.mark.skipif(
    sys.platform == "win32", reason="controlled executable shims require a POSIX host"
)
def test_checkout_harness_dispatch_and_failure_boundaries(tmp_path, mode):
    """Run actual scripts, substituting Docker/uv rather than shared infrastructure."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("the documented checkout harness requires Bash")
    checkout = tmp_path / "checkout with spaces"
    for relative in [
        "scripts/test-services/s3/up.sh",
        "scripts/test-services/s3/down.sh",
        "scripts/run-with-uv.sh",
    ]:
        dest = checkout / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_ROOT / relative, dest)
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ["docker", "uv"]:
        tool = tools / name
        tool.write_text(f"#!{sys.executable}\n" + _TOOL)
        tool.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    env = dict(
        os.environ,
        PATH=str(tools) + os.pathsep + os.environ["PATH"],
        RECIPE_MODE=mode,
        RECIPE_CALLS=str(calls),
    )
    result = subprocess.run(
        [bash, "scripts/test-services/s3/up.sh"],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == {"success": 0, "start-failure": 7, "seed-failure": 8}[mode]
    observed = [json.loads(line) for line in calls.read_text().splitlines()]
    assert all(row[1] == str(checkout) for row in observed)
    assert observed[0][2:] == [
        "compose",
        "-f",
        "scripts/test-services/s3/docker-compose.yml",
        "up",
        "-d",
    ]
    if mode == "start-failure":
        assert len(observed) == 1
    else:
        assert observed[-1][2:] == ["run", "python", "scripts/test-services/s3/seed.py"]
    if mode != "success":
        assert "dev S3 is up" not in result.stdout
        return
    calls.unlink()
    subprocess.run(
        [bash, "scripts/test-services/s3/down.sh"],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    down = json.loads(calls.read_text())
    assert down[2:] == ["compose", "-f", "scripts/test-services/s3/docker-compose.yml", "down"]

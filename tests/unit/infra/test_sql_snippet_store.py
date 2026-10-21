"""The local snippet library is bounded, private and independent of drafts/AWS."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest


def api():
    assert importlib.util.find_spec("aws_tui.infra.sql_snippet_store") is not None, (
        "Local SQL snippet storage is missing"
    )
    return importlib.import_module("aws_tui.infra.sql_snippet_store")


def test_named_snippet_round_trip_with_explicit_provenance(tmp_path):
    m = api()
    path = tmp_path / "private" / "sql-snippets.json"
    store = m.SqlSnippetStore(path=path)
    assert store.list() == ()
    assert not path.parent.exists()
    saved = store.add(
        "Find events", "SELECT '🪿'", provenance="Local editor · fixture/eu-west-1/catalog/sales"
    )
    assert len(saved.id) == 32
    assert store.list() == (saved,)
    assert saved.name == "Find events"
    assert saved.sql == "SELECT '🪿'"
    assert saved.provenance.startswith("Local editor")
    assert "Find events" not in repr(saved)
    assert "SELECT" not in repr(saved)
    assert m.SqlSnippetStore(path=path).list() == (saved,)
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == 1
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700


def test_duplicate_name_never_silently_replaces_snippet(tmp_path):
    m = api()
    store = m.SqlSnippetStore(path=tmp_path / "snippets.json")
    first = store.add("Example", "SELECT 1", provenance="Local editor")
    with pytest.raises(m.SqlSnippetError, match="exists"):
        store.add("example", "SELECT 2", provenance="Different origin")
    assert store.list() == (first,)


@pytest.mark.parametrize("name", ["", "a" * 81, "name\nnewline", "name\x00null"])
def test_invalid_names_do_not_write(tmp_path, name):
    m = api()
    path = tmp_path / "private" / "snippets.json"
    with pytest.raises(m.SqlSnippetError):
        m.SqlSnippetStore(path=path).add(name, "SELECT 1", provenance="Local editor")
    assert not path.exists()


def test_snippet_sql_and_record_count_are_bounded(tmp_path):
    m = api()
    store = m.SqlSnippetStore(path=tmp_path / "snippets.json")
    with pytest.raises(m.SqlSnippetError, match="limit"):
        store.add("large", "🪿" * 16385, provenance="Local editor")
    for index in range(100):
        store.add(f"Snippet {index}", f"SELECT {index}", provenance="Local editor")
    before = store.list()
    assert len(before) == 100
    with pytest.raises(m.SqlSnippetError, match="limit"):
        store.add("one too many", "SELECT 101", provenance="Local editor")
    assert store.list() == before


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        '{"schema_version":999,"snippets":[]}',
        '{"schema_version":1,"snippets":"invalid"}',
        '{"schema_version":1,"snippets":[{}]}',
        '{"schema_version":1,"schema_version":2,"snippets":[]}',
    ],
)
def test_corrupt_or_future_library_is_not_overwritten(tmp_path, payload):
    m = api()
    path = tmp_path / "snippets.json"
    path.write_text(payload)
    store = m.SqlSnippetStore(path=path)
    for operation in (store.list, lambda: store.add("new", "SELECT 1", provenance="Local editor")):
        with pytest.raises(m.SqlSnippetError):
            operation()
    assert path.read_text() == payload


def test_library_read_is_bounded_before_json_decode(tmp_path):
    m = api()
    path = tmp_path / "snippets.json"
    path.write_bytes(b" " * (8_388_608 + 1))
    with pytest.raises(m.SqlSnippetError, match="limit"):
        m.SqlSnippetStore(path=path).list()


def test_delete_is_explicit_and_does_not_touch_other_snippets(tmp_path):
    m = api()
    store = m.SqlSnippetStore(path=tmp_path / "snippets.json")
    first = store.add("One", "SELECT 1", provenance="Local editor")
    second = store.add("Two", "SELECT 2", provenance="Local file")
    store.delete(first.id)
    assert store.list() == (second,)
    with pytest.raises(m.SqlSnippetError):
        store.delete(first.id)
    assert store.list() == (second,)


def test_publish_permission_error_preserves_library_and_cleans_stage(tmp_path, monkeypatch):
    m = api()
    path = tmp_path / "snippets.json"
    store = m.SqlSnippetStore(path=path)
    saved = store.add("One", "SELECT 1", provenance="Local editor")
    before = path.read_bytes()

    def deny(*args, **kwargs):
        raise PermissionError("private name and SQL")

    monkeypatch.setattr(m.os, "replace", deny)
    with pytest.raises(m.SqlSnippetError) as error:
        store.add("Two", "SELECT 2", provenance="Local editor")
    assert "private" not in str(error.value)
    assert path.read_bytes() == before
    assert store.list() == (saved,)
    assert not list(tmp_path.glob(".snippet-*"))


def test_independent_store_instances_preserve_both_updates(tmp_path):
    m = api()
    path = tmp_path / "snippets.json"
    first_store = m.SqlSnippetStore(path=path)
    second_store = m.SqlSnippetStore(path=path)
    first = first_store.add("One", "SELECT 1", provenance="Local editor")
    second = second_store.add("Two", "SELECT 2", provenance="Local editor")
    assert first_store.list() == second_store.list() == (first, second)


def test_snippet_cancellation_prevents_publication(tmp_path):
    m = api()
    permit = m.SqlWritePermit()
    permit.cancel()
    path = tmp_path / "snippets.json"
    with pytest.raises(m.SqlSnippetError, match="cancelled"):
        m.SqlSnippetStore(path=path).add(
            "One", "SELECT 1", provenance="Local editor", permit=permit
        )
    assert not path.exists()


def test_snippet_cancellation_during_final_library_read_preserves_original(tmp_path, monkeypatch):
    m = api()
    path = tmp_path / "snippets.json"
    store = m.SqlSnippetStore(path=path)
    saved = store.add("One", "SELECT 1", provenance="Local editor")
    entered, release = threading.Event(), threading.Event()
    original = store._records
    calls = 0

    def block_final_read():
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            assert release.wait(3)
        return original()

    monkeypatch.setattr(store, "_records", block_final_read)
    permit = m.SqlWritePermit()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(store.add, "Two", "SELECT 2", provenance="Local editor", permit=permit)
        try:
            assert entered.wait(3)
            permit.cancel()
        finally:
            release.set()
        with pytest.raises(m.SqlSnippetError, match="cancelled"):
            future.result(timeout=3)
    assert store.list() == (saved,)
    assert not list(tmp_path.glob(".snippet-*"))


@pytest.mark.parametrize("field", ["name", "provenance"])
def test_surrogate_labels_are_corrupt_not_displayable_records(tmp_path, field):
    m = api()
    path = tmp_path / "snippets.json"
    store = m.SqlSnippetStore(path=path)
    store.add("One", "SELECT 1", provenance="Local editor")
    payload = json.loads(path.read_text())
    payload["snippets"][0][field] = "invalid\ud800label"
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    with pytest.raises(m.SqlSnippetError):
        store.list()
    assert path.read_bytes() == before

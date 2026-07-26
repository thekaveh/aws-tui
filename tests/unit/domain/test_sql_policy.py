from __future__ import annotations

import logging

import pytest

from aws_tui.domain.sql_policy import QueryRejectedError, ReadOnlySqlPolicy


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM analytics.events LIMIT 100",
        "WITH recent AS (SELECT * FROM events) SELECT * FROM recent",
        "SHOW TABLES",
        "SHOW TBLPROPERTIES analytics.events",
        "DESCRIBE analytics.events",
        "EXPLAIN SELECT * FROM analytics.events",
        "  SELECT 1;  ",
        'SELECT "select", "mixed Case" FROM "analytics"."events"',
        "SELECT * FROM (SELECT event_id FROM events) AS nested",
        "-- Athena line comment\nSELECT 1",
        "/* Athena block comment */ SELECT 1",
        "sElEcT * FrOm events",
        "EXPLAIN WITH recent AS (SELECT * FROM events) SELECT * FROM recent",
    ],
)
def test_policy_accepts_one_read_only_statement(sql: str) -> None:
    assert ReadOnlySqlPolicy().validate(sql) == sql.strip()


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "SELECT 1; SELECT 2",
        "SELECT 1; /* hidden */ DELETE FROM events",
        "CREATE TABLE x AS SELECT 1",
        "INSERT INTO x SELECT 1",
        "UPDATE x SET value = 1",
        "DELETE FROM x",
        "MERGE INTO x USING y ON x.id = y.id WHEN MATCHED THEN DELETE",
        "UNLOAD (SELECT 1) TO 's3://bucket/out/'",
        "CALL system.runtime.kill_query()",
        "EXPLAIN CREATE TABLE x AS SELECT 1",
        "EXPLAIN ANALYZE SELECT * FROM analytics.events",
        "EXPLAIN /* bypass */ ANALYZE SELECT * FROM analytics.events",
        "SHOW CREATE TABLE analytics.events",
        "show create table analytics.events",
        "SHOW /* bypass */ CREATE TABLE analytics.events",
        "VACUUM x",
        "SELEC FROM",
        "/* comment only */",
        "/* bypass */ INSERT INTO x SELECT 1",
        "WITH changed AS (DELETE FROM x RETURNING *) SELECT * FROM changed",
        "SELECT * FROM (DELETE FROM x RETURNING *) AS changed",
    ],
)
def test_policy_rejects_mutating_or_unknown_sql(sql: str) -> None:
    with pytest.raises(QueryRejectedError):
        ReadOnlySqlPolicy().validate(sql)


def test_query_rejected_error_is_a_validation_error() -> None:
    from aws_tui.domain.filesystem import ValidationError

    assert issubclass(QueryRejectedError, ValidationError)


def test_policy_does_not_log_query_text_during_command_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sql = "EXPLAIN SELECT sensitive_customer_secret FROM analytics.events"
    caplog.set_level(logging.WARNING, logger="sqlglot")

    ReadOnlySqlPolicy().validate(sql)

    assert "sensitive_customer_secret" not in caplog.text

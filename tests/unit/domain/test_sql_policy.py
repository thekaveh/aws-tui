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
        "DESCRIBE analytics.events",
        "DESCRIBE FORMATTED analytics.events",
        "DESCRIBE EXTENDED analytics.events",
        "DESCRIBE analytics.events PARTITION (event_date = '2026-07-25')",
    ],
)
def test_policy_accepts_athena_table_describe_grammar(sql: str) -> None:
    assert ReadOnlySqlPolicy().validate(sql) == sql


@pytest.mark.parametrize(
    "sql",
    [
        "DESCRIBE orders columnA",
        "DESCRIBE `orders table` `column name`",
        "DESCRIBE analytics.orders PARTITION "
        "(`event date` = '2026-07-25', shard = 7) `column name`",
        "EXPLAIN DESCRIBE orders columnA",
        "EXPLAIN (TYPE IO, FORMAT TEXT) DESCRIBE analytics.orders "
        "PARTITION (`event date` = '2026-07-25', shard = 7) `column name`",
    ],
)
def test_policy_accepts_bounded_athena_describe_column_grammar(sql: str) -> None:
    assert ReadOnlySqlPolicy().validate(sql) == sql


@pytest.mark.parametrize(
    "sql",
    [
        "DESCRIBE orders PARTITION (p > 1)",
        "DESCRIBE orders PARTITION (p <> 1)",
        "DESCRIBE orders PARTITION (p = other_column)",
        "DESCRIBE orders PARTITION (p = lower('value'))",
        "DESCRIBE orders PARTITION (p = (SELECT 1))",
        "DESCRIBE orders PARTITION (p = TRUE)",
        "DESCRIBE orders PARTITION (p 1)",
        "DESCRIBE orders PARTITION (p = 'value',)",
        "DESCRIBE orders PARTITION ()",
        "DESCRIBE orders PARTITION (p = 'value') columnA trailing",
        "DESCRIBE orders columnA trailing",
        "DESCRIBE orders PARTITION (p = 'value') PARTITION (q = 'next')",
        "DESCRIBE AwsDataCatalog.analytics.orders",
        "DESCRIBE orders; DELETE FROM orders",
        "EXPLAIN DESCRIBE orders PARTITION (p > 1)",
        "EXPLAIN DESCRIBE orders PARTITION (p = other_column)",
        "EXPLAIN DESCRIBE orders PARTITION (p = lower('value'))",
        "EXPLAIN DESCRIBE orders PARTITION (p = (SELECT 1))",
        "EXPLAIN DESCRIBE orders PARTITION (p = 'value') columnA trailing",
        "EXPLAIN DESCRIBE orders columnA trailing",
    ],
)
def test_policy_rejects_unbounded_athena_describe_grammar(sql: str) -> None:
    with pytest.raises(QueryRejectedError):
        ReadOnlySqlPolicy().validate(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DESCRIBE SELECT 1",
        "DESCRIBE TABLE events",
        "DESCRIBE DELETE FROM events",
        "DESCRIBE INSERT INTO events SELECT 1",
        "DESCRIBE UPDATE events SET value = 1",
        "DESCRIBE CREATE TABLE events (value int)",
        "DESCRIBE DROP TABLE events",
        "DESCRIBE CALL system.runtime.kill_query()",
        "DESCRIBE VACUUM events",
        "EXPLAIN DESCRIBE DELETE FROM events",
        "EXPLAIN DESCRIBE INSERT INTO events SELECT 1",
        "EXPLAIN DESCRIBE UPDATE events SET value = 1",
        "EXPLAIN DESCRIBE CREATE TABLE events (value int)",
    ],
)
def test_policy_rejects_non_table_or_write_describe_grammar(sql: str) -> None:
    with pytest.raises(QueryRejectedError):
        ReadOnlySqlPolicy().validate(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SHOW DATABASES",
        "SHOW DATABASES LIKE '.*analytics'",
        "SHOW DATABASES IN `lambda:function`",
        "SHOW SCHEMAS",
        "SHOW TABLES",
        "SHOW TABLES IN sampledb",
        "SHOW TABLES IN AwsDataCatalog.sampledb '*logs'",
        "SHOW COLUMNS FROM orders",
        "SHOW COLUMNS IN customers.orders",
        "SHOW COLUMNS FROM orders FROM customers",
        "SHOW PARTITIONS flight_delays_csv",
        "SHOW TBLPROPERTIES orders",
        "SHOW TBLPROPERTIES orders('comment')",
        "SHOW VIEWS",
        "SHOW VIEWS IN marketing_analytics LIKE 'orders*'",
    ],
)
def test_policy_accepts_allowlisted_athena_show_grammar(sql: str) -> None:
    assert ReadOnlySqlPolicy().validate(sql) == sql


@pytest.mark.parametrize(
    "sql",
    [
        "SHOW",
        "SHOW FROBNICATE events",
        "SHOW DROP TABLE events",
        "SHOW CALL system.runtime.kill_query()",
        "SHOW VACUUM events",
        "SHOW TABLES DELETE FROM events",
        "SHOW TABLES IN analytics INSERT INTO events SELECT 1",
        "SHOW DATABASES LIKE 'analytics' UPDATE events SET value = 1",
        "SHOW CREATE TABLE events",
        "SHOW CREATE VIEW event_view",
        "SHOW COLUMNS",
        "SHOW PARTITIONS",
        "SHOW TBLPROPERTIES",
    ],
)
def test_policy_rejects_unknown_write_or_incomplete_show_grammar(sql: str) -> None:
    with pytest.raises(QueryRejectedError):
        ReadOnlySqlPolicy().validate(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 UNION SELECT 2",
        "SELECT 1 INTERSECT SELECT 1",
        "SELECT 1 EXCEPT SELECT 2",
        "(SELECT 1 UNION SELECT 2) EXCEPT SELECT 3",
        "SELECT * FROM (SELECT 1 UNION SELECT 2) AS combined",
    ],
)
def test_policy_accepts_safe_select_set_operations(sql: str) -> None:
    assert ReadOnlySqlPolicy().validate(sql) == sql


def test_policy_rejects_write_nested_in_set_operation() -> None:
    sql = (
        "WITH changed AS (DELETE FROM events RETURNING *) "
        "SELECT * FROM changed UNION SELECT * FROM events"
    )

    with pytest.raises(QueryRejectedError):
        ReadOnlySqlPolicy().validate(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "EXPLAIN (TYPE DISTRIBUTED) SELECT * FROM analytics.events",
        "EXPLAIN (FORMAT JSON) SELECT * FROM analytics.events",
        "EXPLAIN (FORMAT GRAPHVIZ, TYPE LOGICAL) SELECT 1 UNION SELECT 2",
        "EXPLAIN (TYPE IO, FORMAT TEXT) DESCRIBE analytics.events",
    ],
)
def test_policy_accepts_valid_explain_options(sql: str) -> None:
    assert ReadOnlySqlPolicy().validate(sql) == sql


@pytest.mark.parametrize(
    "sql",
    [
        "EXPLAIN (TYPE UNKNOWN) SELECT 1",
        "EXPLAIN (FORMAT YAML) SELECT 1",
        'EXPLAIN ("TYPE" DISTRIBUTED) SELECT 1',
        'EXPLAIN ("FORMAT" JSON) SELECT 1',
        "EXPLAIN (FORMAT JSON) ANALYZE SELECT 1",
        "EXPLAIN (TYPE DISTRIBUTED) DELETE FROM events",
        "EXPLAIN (FORMAT JSON) CREATE TABLE events AS SELECT 1",
    ],
)
def test_policy_rejects_unknown_explain_options_or_unsafe_body(sql: str) -> None:
    with pytest.raises(QueryRejectedError):
        ReadOnlySqlPolicy().validate(sql)


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

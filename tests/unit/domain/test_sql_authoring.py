"""SQL authoring contracts: layout preservation and cursor-aware insertion."""

from __future__ import annotations

import importlib
import importlib.util

import pytest
import sqlglot

from aws_tui.domain.data_catalog import Column, TableRef
from aws_tui.domain.query import QueryContext
from aws_tui.domain.sql_policy import ReadOnlySqlPolicy


@pytest.fixture
def authoring():
    assert importlib.util.find_spec("aws_tui.domain.sql_authoring") is not None, (
        "The SQL authoring helper has not been implemented"
    )
    return importlib.import_module("aws_tui.domain.sql_authoring")


@pytest.fixture
def context():
    return QueryContext("fixture", "eu-west-1", "analytics", "catalog", "sales")


@pytest.fixture
def tables(authoring, context):
    return (
        authoring.CompletionTable(
            TableRef(context.catalog, context.database, "sales table", "fixture", "eu-west-1"),
            (
                Column("First Name", "varchar", None, False),
                Column('say "hello"', "varchar", None, False),
                Column("amount", "decimal(18,2)", None, False),
                Column(
                    "payload", 'row("nested value" varchar, item row("a.b" bigint))', None, False
                ),
            ),
        ),
        authoring.CompletionTable(
            TableRef(context.catalog, context.database, "other", "fixture", "eu-west-1"),
            (Column("other_id", "bigint", None, False),),
        ),
    )


@pytest.mark.parametrize(
    "source",
    [
        "select  a, b  from t where a > 1 order by b limit 5",
        'select "First Name", \'two  spaces\' as "a.b" from "sales table"',
        "-- keep  this\nselect a /* exact  comment */ from t -- tail\nwhere a = 'it''s  here'",
        "with x as (select a from t where a > 0) select a from x union all select b from u",
        'select "say ""hello""" from t',
        "SELECT sum(amount) OVER (PARTITION BY x ORDER BY y) FROM t LIMIT 5;",
        "SELECT '🪿' AS emoji FROM t",
    ],
)
def test_format_preserves_semantics_and_protected_text(authoring, source):
    result = authoring.format_sql(source)
    assert result.reason is None
    assert ReadOnlySqlPolicy().validate(result.sql)
    assert sqlglot.parse_one(result.sql, read="athena") == sqlglot.parse_one(source, read="athena")
    # The formatter changes only whitespace between tokens, never token bytes.
    tokens = sqlglot.Dialect.get_or_raise("athena").tokenize
    old = tokens(source)
    new = tokens(result.sql)
    assert [source[t.start : t.end + 1] for t in old] == [
        result.sql[t.start : t.end + 1] for t in new
    ]
    assert [t.comments for t in old] == [t.comments for t in new]
    assert authoring.format_sql(result.sql).sql == result.sql


def test_format_has_visible_clause_layout(authoring):
    result = authoring.format_sql("select a, b from t where a > 1 order by b limit 5")
    assert "\nfrom t\nwhere" in result.sql
    assert "\norder by b\nlimit 5" in result.sql


@pytest.mark.parametrize(
    "source",
    [
        "",
        "SELECT",
        "SELECT a FROM",
        "SELECT (",
        'SELECT "open',
        "SELECT 1; SELECT 2",
        "DELETE FROM t",
        "SELECT FROM t",
        "SELECT  a,",
        "SELECT  a FROM t GROUP BY",
        "SELECT a, FROM t",
        "WITH x AS (SELECT a,) SELECT * FROM x",
        "SELECT a FROM t JOIN u ON",
        "SELECT a FROM t OFFSET",
        "SELECT a FROM t FETCH",
        "SELECT * FROM (SELECT a FROM t JOIN u ON) x",
    ],
)
def test_format_refuses_incomplete_or_unsafe_without_mutation(authoring, source):
    result = authoring.format_sql(source)
    assert result.sql == source
    assert result.reason
    assert "SELECT a FROM" not in result.reason


def test_format_bounds_input_and_does_not_echo_values(authoring):
    source = "SELECT 'private-value'" + " " * 1_048_576
    result = authoring.format_sql(source)
    assert result.sql == source
    assert result.reason
    assert "private-value" not in result.reason


def test_format_retains_trailing_comment_bytes(authoring):
    source = "  select  a from t -- preserve  trailing  \n"
    result = authoring.format_sql(source)
    assert result.reason is None
    assert result.sql.endswith("-- preserve  trailing  \n")


@pytest.mark.parametrize(
    "source",
    [
        'SELECT a, "FROM" FROM t',
        "SELECT a, 'WHERE' FROM t",
        'SELECT a, "GROUP BY" FROM t',
        "SELECT a FROM t FETCH FIRST ROW ONLY",
    ],
)
def test_formatter_does_not_confuse_protected_tokens_with_clauses(authoring, source):
    result = authoring.format_sql(source)
    assert result.reason is None
    assert ReadOnlySqlPolicy().validate(result.sql)


@pytest.mark.parametrize(
    "suffix", ["FIRST", "NEXT", "FIRST 2", "FIRST ROWS", "FIRST 2 ROWS", "FIRST 2 ROWS WITH"]
)
def test_formatter_refuses_incomplete_fetch_clause(authoring, suffix):
    source = "SELECT  a FROM t FETCH " + suffix
    result = authoring.format_sql(source)
    assert result.sql == source
    assert result.reason


def choices(authoring, context, tables, marked):
    cursor = marked.index("|")
    sql = marked.replace("|", "")
    target = authoring.completion_target(sql, cursor)
    return sql, target, authoring.completion_choices(sql, target, tables, context)


@pytest.mark.parametrize(
    ("marked", "expected", "kind"),
    [
        ("SEL|", "SELECT", "keyword"),
        ("SELECT * FROM sa|", '"sales table"', "table"),
        ('SELECT * FROM "sa|les table"', '"sales table"', "table"),
        ('SELECT "First N|ame" FROM "sales table"', '"First Name"', "column"),
        ('SELECT s.am| FROM "sales table" AS s', '"amount"', "column"),
        ('SELECT s."say |" FROM "sales table" s', '"say ""hello"""', "column"),
        ('SELECT payload."nested |" FROM "sales table"', '"nested value"', "field"),
        ('SELECT s.payload.item."a|" FROM "sales table" s', '"a.b"', "field"),
    ],
)
def test_contextual_completion_replaces_only_cursor_token(
    authoring, context, tables, marked, expected, kind
):
    sql, target, suggestions = choices(authoring, context, tables, marked)
    selected = [s for s in suggestions if s.text == expected and s.kind == kind]
    assert len(selected) == 1
    result = sql[: target.start] + selected[0].text + sql[target.end :]
    assert result.startswith(sql[: target.start])
    assert result.endswith(sql[target.end :])
    if '"sa' in sql:
        assert result.count('"sales table"') == 1


@pytest.mark.parametrize(
    "marked",
    [
        "SELECT 'pr|ivate' FROM t",
        "SELECT 1 -- co|mment",
        "SELECT /* co|mment */ 1",
        "SELECT /* outer /* ne|sted */ end */ 1",
    ],
)
def test_completion_never_inserts_in_literals_or_comments(authoring, context, tables, marked):
    _, target, suggestions = choices(authoring, context, tables, marked)
    assert not target.allowed
    assert not suggestions


def test_table_qualifier_scopes_to_current_database(authoring, context, tables):
    _, _, current = choices(authoring, context, tables, "SELECT * FROM sales.sa|")
    _, _, foreign = choices(authoring, context, tables, "SELECT * FROM other_db.sa|")
    assert any(s.kind == "table" for s in current)
    assert not any(s.kind == "table" for s in foreign)


def test_alias_filters_columns_and_foreign_source_rows_are_ignored(authoring, context, tables):
    foreign = authoring.CompletionTable(
        TableRef("catalog", "sales", "foreign", "different", "eu-west-1"),
        (Column("secret_column", "int", None, False),),
    )
    _, _, result = choices(
        authoring,
        context,
        (*tables, foreign),
        'SELECT s.| FROM "sales table" s JOIN other o ON s.amount = o.other_id',
    )
    assert any(s.text == '"amount"' for s in result)
    assert not any(s.text in {'"other_id"', '"secret_column"'} for s in result)


def test_completion_is_bounded_and_deterministic(authoring, context):
    tables = tuple(
        authoring.CompletionTable(
            TableRef("catalog", "sales", f"table_{i:04d}", "fixture", "eu-west-1"), ()
        )
        for i in range(500)
    )
    _, _, suggestions = choices(authoring, context, tables, "SELECT * FROM |")
    assert len(suggestions) == 200
    assert len(set(s.text for s in suggestions)) == 200


@pytest.mark.parametrize(
    "marked",
    [
        'SELECT x.| FROM "sales table" x WHERE EXISTS (SELECT 1 FROM other x)',
        'SELECT catalog.sales."sales table".| FROM catalog.sales."sales table"',
    ],
)
def test_column_completion_uses_cursor_scope_and_full_qualification(
    authoring, context, tables, marked
):
    _, _, result = choices(authoring, context, tables, marked)
    assert any(s.text == '"amount"' for s in result)
    assert not any(s.text == '"other_id"' for s in result)


def test_unqualified_outer_completion_does_not_leak_inner_table(authoring, context, tables):
    _, _, result = choices(
        authoring,
        context,
        tables,
        'SELECT | FROM "sales table" x WHERE EXISTS (SELECT 1 FROM other o)',
    )
    assert any(s.text == '"amount"' for s in result)
    assert not any(s.text == '"other_id"' for s in result)


@pytest.mark.parametrize(
    "marked",
    [
        'SELECT (SELECT count(*) FROM other), | FROM "sales table"',
        'SELECT ((SELECT count(*) FROM other)), | FROM "sales table"',
        'SELECT coalesce((SELECT count(*) FROM other), 0), | FROM "sales table"',
    ],
)
def test_select_comma_after_scalar_subquery_offers_only_outer_columns(
    authoring, context, tables, marked
):
    _, target, result = choices(authoring, context, tables, marked)
    assert not target.table_position
    assert any(s.text == '"amount"' and s.kind == "column" for s in result)
    assert not any(s.text == '"other_id"' or s.kind == "table" for s in result)


@pytest.mark.parametrize(
    ("marked", "table_position"),
    [
        ("SELECT * FROM (SELECT amount FROM other) x, |", True),
        ("SELECT * FROM (SELECT amount FROM other WHERE amount > 0) x, |", True),
        ("SELECT * FROM events WHERE EXISTS (SELECT 1 FROM other, |)", True),
        ("SELECT * FROM events WHERE EXISTS (SELECT 1, | FROM other)", False),
        ("SELECT coalesce('FROM', |) FROM other", False),
    ],
)
def test_comma_classification_respects_current_parenthesis_scope(authoring, marked, table_position):
    target = authoring.completion_target(marked.replace("|", ""), marked.index("|"))
    assert target.allowed
    assert target.table_position is table_position


@pytest.mark.parametrize(
    "marked",
    [
        'WITH x AS (SELECT amount AS renamed FROM "sales table") SELECT x.| FROM x',
        'SELECT x.| FROM (SELECT amount AS renamed FROM "sales table") x',
        'WITH x AS (SELECT * FROM "sales table") SELECT x.| FROM x',
    ],
)
def test_cte_and_derived_projection_completion(authoring, context, tables, marked):
    _, _, result = choices(authoring, context, tables, marked)
    expected = '"amount"' if "SELECT *" in marked else '"renamed"'
    assert any(s.text == expected for s in result)
    assert not any(s.text == '"other_id"' for s in result)


@pytest.mark.parametrize("foreign", ["other_db", "foreign_catalog.sales"])
def test_same_table_name_cannot_import_columns_into_foreign_alias(
    authoring, context, tables, foreign
):
    marked = (
        f'SELECT x.| FROM {foreign}."sales table" x JOIN catalog.sales."sales table" y ON 1 = 1'
    )
    _, _, result = choices(authoring, context, tables, marked)
    assert not result


def test_array_row_field_completion_does_not_offer_top_level_names(authoring, context):
    table = authoring.CompletionTable(
        TableRef("catalog", "sales", "events", "fixture", "eu-west-1"),
        (Column("arr", 'array(row("nested field" varchar))', None, False),),
    )
    _, _, result = choices(authoring, context, (table,), "SELECT arr[1].| FROM events")
    assert [s.text for s in result] == ['"nested field"']


@pytest.mark.parametrize(
    "marked", ["SELECT 12|3 FROM other", "SELECT 12.3|e2 FROM other", "SELECT .2|5 FROM other"]
)
def test_numeric_literals_never_become_identifier_edits(authoring, context, tables, marked):
    _, target, result = choices(authoring, context, tables, marked)
    assert not target.allowed
    assert not result


@pytest.mark.parametrize(
    "marked",
    [
        'WITH x AS (SELECT o.| FROM other) SELECT * FROM "sales table" o',
        'SELECT * FROM (SELECT o.| FROM other) x JOIN "sales table" o ON true',
    ],
)
def test_noncorrelated_scopes_do_not_import_outer_aliases(authoring, context, tables, marked):
    _, _, result = choices(authoring, context, tables, marked)
    assert not result


def test_legal_correlated_subquery_can_offer_outer_alias_columns(authoring, context, tables):
    _, _, result = choices(
        authoring,
        context,
        tables,
        'SELECT * FROM "sales table" o WHERE EXISTS (SELECT o.| FROM other)',
    )
    assert any(s.text == '"amount"' for s in result)


def test_cte_table_names_are_offered_at_from_cursor(authoring, context, tables):
    _, _, result = choices(
        authoring, context, tables, 'WITH x AS (SELECT amount FROM "sales table") SELECT * FROM |'
    )
    assert any(s.text == '"x"' and s.kind == "table" for s in result)


@pytest.mark.parametrize("cursor", [-1, 100])
def test_invalid_cursor_does_not_offer_edits(authoring, cursor):
    target = authoring.completion_target("SELECT", cursor)
    assert not target.allowed

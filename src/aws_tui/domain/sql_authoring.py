"""Pure, bounded SQL edits; authoring never executes a statement.

The layout formatter retains the original token and comment bytes. Completion
uses a tolerant cursor lexer because unfinished SQL is the usual input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import Dialect, TokenType, exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

from aws_tui.domain.data_catalog import Column, TableRef
from aws_tui.domain.query import QueryContext
from aws_tui.domain.sql_policy import QueryRejectedError, ReadOnlySqlPolicy, quote_athena_identifier

MAX_SQL_CHARS = 1_048_576
MAX_SUGGESTIONS = 200
_KEYWORDS = (
    "SELECT",
    "FROM",
    "WHERE",
    "JOIN",
    "LEFT JOIN",
    "RIGHT JOIN",
    "INNER JOIN",
    "ON",
    "AS",
    "AND",
    "OR",
    "NOT",
    "NULL",
    "IS NULL",
    "DISTINCT",
    "GROUP BY",
    "ORDER BY",
    "HAVING",
    "LIMIT",
    "WITH",
    "UNION ALL",
    "CASE",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "COUNT",
    "SUM",
    "MIN",
    "MAX",
    "AVG",
    "ASC",
    "DESC",
)
_CLAUSES = frozenset(
    {
        "FROM",
        "WHERE",
        "GROUP BY",
        "HAVING",
        "ORDER BY",
        "LIMIT",
        "UNION",
        "UNION ALL",
        "EXCEPT",
        "INTERSECT",
        "JOIN",
        "LEFT JOIN",
        "RIGHT JOIN",
        "INNER JOIN",
        "FULL JOIN",
    }
)
_DANGLING_CLAUSES = _CLAUSES | {"ON", "OFFSET", "FETCH", "AS", "AND", "OR", "WHEN", "THEN", "ELSE"}
_NUMBER = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _is_clause(token: sqlglot.Token, clauses: frozenset[str]) -> bool:
    return (
        token.token_type not in {TokenType.IDENTIFIER, TokenType.STRING}
        and token.text.upper() in clauses
    )


def _complete_fetches(tokens: list[sqlglot.Token]) -> bool:
    for index, token in enumerate(tokens):
        if token.token_type is not TokenType.FETCH:
            continue
        words: list[str] = []
        depth = 0
        for item in tokens[index + 1 :]:
            if (
                item.token_type is TokenType.R_PAREN and depth == 0
            ) or item.token_type is TokenType.SEMICOLON:
                break
            if item.token_type is TokenType.L_PAREN:
                depth += 1
            elif item.token_type is TokenType.R_PAREN:
                depth -= 1
            words.append(item.text.upper())
        if (
            not words
            or not {"ROW", "ROWS"}.intersection(words)
            or not (words[-1] == "ONLY" or words[-2:] == ["WITH", "TIES"])
        ):
            return False
    return True


@dataclass(frozen=True, slots=True)
class FormatResult:
    sql: str = field(repr=False)
    reason: str | None = None


def format_sql(sql: str) -> FormatResult:
    """Format layout only after proving a complete, read-only parse.

    No exception message contains submitted SQL. Refusal leaves it untouched.
    """
    if len(sql) > MAX_SQL_CHARS:
        return FormatResult(sql, "SQL exceeds the authoring size limit")
    try:
        ReadOnlySqlPolicy().validate(sql)
        statements = sqlglot.parse(sql, read="athena", error_message_context=0)
        if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation)):
            return FormatResult(sql, "Layout formatting supports SELECT and WITH queries")
        before = statements[0]
        tokens = Dialect.get_or_raise("athena").tokenize(sql)
        if not _complete_fetches(tokens):
            return FormatResult(sql, "Complete the FETCH clause before formatting")
        if (
            any(not node.expressions for node in before.find_all(exp.Select, exp.Group, exp.Order))
            or any(
                token.token_type is TokenType.COMMA
                and (
                    index + 1 == len(tokens)
                    or tokens[index + 1].token_type in {TokenType.R_PAREN, TokenType.SEMICOLON}
                    or _is_clause(tokens[index + 1], _CLAUSES)
                )
                for index, token in enumerate(tokens)
            )
            or any(
                _is_clause(token, _DANGLING_CLAUSES)
                and (
                    index + 1 == len(tokens)
                    or tokens[index + 1].token_type in {TokenType.R_PAREN, TokenType.SEMICOLON}
                )
                for index, token in enumerate(tokens)
            )
        ):
            return FormatResult(sql, "Complete the query clauses before formatting")
        pieces: list[str] = []
        previous_end = 0
        depth = 0
        for token in tokens:
            gap = sql[previous_end : token.start]
            # Gaps can contain comments. Keep their complete original spelling,
            # including the newline which terminates a line comment.
            if gap and gap.isspace():
                clause = _is_clause(token, _CLAUSES)
                gap = "\n" if depth == 0 and clause else " "
            pieces.append(gap)
            pieces.append(sql[token.start : token.end + 1])
            if token.token_type is TokenType.L_PAREN:
                depth += 1
            elif token.token_type is TokenType.R_PAREN:
                depth -= 1
            previous_end = token.end + 1
        pieces.append(sql[previous_end:])
        # Do not strip comment bodies or a terminating line-comment newline.
        formatted = "".join(pieces)
        after = sqlglot.parse_one(formatted, read="athena", error_message_context=0)
        if before != after:
            return FormatResult(sql, "Formatting could not preserve query meaning")
        ReadOnlySqlPolicy().validate(formatted)
        return FormatResult(formatted)
    except (SqlglotError, QueryRejectedError, RecursionError, ValueError):
        return FormatResult(sql, "Complete one read-only query before formatting")


@dataclass(frozen=True, slots=True)
class CompletionTable:
    ref: TableRef = field(repr=False)
    columns: tuple[Column, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class CompletionTarget:
    start: int
    end: int
    prefix: str = field(repr=False)
    qualifiers: tuple[str, ...] = field(repr=False)
    table_position: bool
    allowed: bool = True


@dataclass(frozen=True, slots=True)
class SqlCompletion:
    label: str = field(repr=False)
    text: str = field(repr=False)
    kind: str
    detail: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class _Lexeme:
    start: int
    end: int
    value: str = field(repr=False)
    kind: str


def _lex(sql: str) -> list[_Lexeme]:
    """Keep quotes and comment boundaries even when the last token is open."""
    out: list[_Lexeme] = []
    index = 0
    while index < len(sql):
        start = index
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            end = sql.find("\n", index)
            index = len(sql) if end < 0 else end
            out.append(_Lexeme(start, index, "", "comment"))
        elif sql.startswith("/*", index):
            index += 2
            depth = 1
            while index < len(sql) and depth:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            out.append(_Lexeme(start, index, "", "comment"))
        elif char in {'"', "`", "'"}:
            index += 1
            value: list[str] = []
            while index < len(sql):
                if sql[index] == char:
                    index += 1
                    if index < len(sql) and sql[index] == char:
                        value.append(char)
                        index += 1
                    else:
                        break
                else:
                    value.append(sql[index])
                    index += 1
            out.append(_Lexeme(start, index, "".join(value), "string" if char == "'" else "quoted"))
        elif (match := _NUMBER.match(sql, index)) is not None:
            index = match.end()
            out.append(_Lexeme(start, index, sql[start:index], "number"))
        elif char.isalnum() or char in {"_", "$"}:
            index += 1
            while index < len(sql) and (sql[index].isalnum() or sql[index] in {"_", "$"}):
                index += 1
            out.append(_Lexeme(start, index, sql[start:index], "word"))
        else:
            index += 1
            out.append(_Lexeme(start, index, char, "symbol"))
    return out


def completion_target(sql: str, cursor: int) -> CompletionTarget:
    """Find the entire cursor token while retaining its typed prefix."""
    if not 0 <= cursor <= len(sql) or len(sql) > MAX_SQL_CHARS:
        return CompletionTarget(0, 0, "", (), False, False)
    tokens = _lex(sql)
    start = end = cursor
    prefix = ""
    for token in tokens:
        if token.start <= cursor < token.end or (token.end == cursor and token.kind != "symbol"):
            if token.kind in {"comment", "string", "number"}:
                return CompletionTarget(cursor, cursor, "", (), False, False)
            if token.kind in {"word", "quoted"}:
                start, end = token.start, token.end
                prefix = sql[start:cursor]
                if token.kind == "quoted":
                    quote = sql[start]
                    prefix = prefix[1:]
                    if cursor == token.end and sql[token.end - 1] == quote and len(prefix):
                        prefix = prefix[:-1]
                    prefix = prefix.replace(quote * 2, quote)
            break
    before = [t for t in tokens if t.end <= start and t.kind != "comment"]
    qualifiers: list[str] = []
    while len(before) >= 2 and before[-1].value == ".":
        before.pop()
        indexes: list[str] = []
        while before and before[-1].value == "]":
            depth = 1
            before.pop()
            while before and depth:
                item = before.pop()
                if item.value == "]":
                    depth += 1
                elif item.value == "[":
                    depth -= 1
            if depth:
                return CompletionTarget(start, end, prefix, (), False, False)
            indexes.append("[]")
        if not before or before[-1].kind not in {"word", "quoted"}:
            return CompletionTarget(start, end, prefix, (), False, False)
        qualifiers[0:0] = [before.pop().value, *indexes]
    # Closing a nested expression restores its parent's clause. A scalar
    # subquery's FROM must not turn an outer SELECT-list comma into a table.
    clauses = [""]
    for token in before:
        if token.kind == "symbol" and token.value == "(":
            clauses.append("")
        elif token.kind == "symbol" and token.value == ")":
            if len(clauses) > 1:
                clauses.pop()
        elif token.kind == "word" and token.value.upper() in {
            "SELECT",
            "FROM",
            "JOIN",
            "WHERE",
            "ON",
            "GROUP",
            "ORDER",
            "HAVING",
            "LIMIT",
        }:
            clauses[-1] = token.value.upper()
    table_position = bool(before) and (
        (before[-1].kind == "word" and before[-1].value.upper() in {"FROM", "JOIN"})
        or (before[-1].value == "," and clauses[-1] == "FROM")
    )
    return CompletionTarget(start, end, prefix, tuple(qualifiers), table_position)


_Source = tuple[tuple[str, ...], tuple[Column, ...]]


def _scope_sources(
    scope: Scope,
    tables: tuple[CompletionTable, ...],
    context: QueryContext,
    depth: int = 0,
) -> dict[str, _Source]:
    if depth > 16:
        return {}
    sources: dict[str, _Source] = {}
    for alias, (_, source) in scope.selected_sources.items():
        if isinstance(source, exp.Table):
            identity = (
                source.catalog or context.catalog,
                source.db or context.database,
                source.name,
            )
            columns = next(
                (
                    t.columns
                    for t in tables
                    if tuple(
                        part.casefold()
                        for part in (t.ref.catalog_name, t.ref.database_name, t.ref.table_name)
                    )
                    == tuple(part.casefold() for part in identity)
                ),
                (),
            )
            # Retain an unresolved/foreign alias with no columns; it must not
            # accidentally fall back to a same-named local table.
            sources[alias.casefold()] = (identity, columns)
        elif isinstance(source, Scope):
            if not isinstance(source.expression, (exp.Select, exp.SetOperation)):
                continue
            child_sources = _scope_sources(source, tables, context, depth + 1)
            columns_list: list[Column] = []
            for projection in source.expression.selects:
                if isinstance(projection, exp.Star):
                    columns_list.extend(c for _, cols in child_sources.values() for c in cols)
                    continue
                value = projection.this if isinstance(projection, exp.Alias) else projection
                if isinstance(value, exp.Column) and value.is_star:
                    columns_list.extend(child_sources.get(value.table.casefold(), ((), ()))[1])
                    continue
                name = projection.alias_or_name
                if not name:
                    continue
                type_name = "unknown"
                if isinstance(value, exp.Column):
                    relevant = (
                        (child_sources.get(value.table.casefold(), ((), ())),)
                        if value.table
                        else tuple(child_sources.values())
                    )
                    type_name = next(
                        (
                            c.type_name
                            for _, cols in relevant
                            for c in cols
                            if c.name.casefold() == value.name.casefold()
                        ),
                        "unknown",
                    )
                columns_list.append(Column(name, type_name, None, False))
            if source.outer_columns and len(source.outer_columns) == len(columns_list):
                columns_list = [
                    Column(name, c.type_name, c.comment, c.partition_key)
                    for name, c in zip(source.outer_columns, columns_list, strict=True)
                ]
            sources[alias.casefold()] = ((alias,), tuple(columns_list[:2000]))
    return sources


def _cursor_scope(sql: str, target: CompletionTarget) -> Scope | None:
    marker = "__aws_tui_cursor__"
    while marker in sql:
        marker += "_"
    proposed = sql[: target.start] + marker + sql[target.end :]
    try:
        tree = sqlglot.parse_one(proposed, read="athena", error_message_context=0)
        cursor = next((node for node in tree.find_all(exp.Identifier) if node.name == marker), None)
        select = cursor.find_ancestor(exp.Select) if cursor is not None else None
        if select is None:
            return None
        return next((scope for scope in traverse_scope(tree) if scope.expression is select), None)
    except (SqlglotError, RecursionError, ValueError):
        return None


def _cursor_sources(
    sql: str,
    target: CompletionTarget,
    tables: tuple[CompletionTable, ...],
    context: QueryContext,
) -> dict[str, _Source] | None:
    try:
        scope = _cursor_scope(sql, target)
        if scope is None:
            return None
        sources = _scope_sources(scope, tables, context)
        # Correlated explicit aliases may resolve outward, but unqualified
        # completion must not mix unrelated nested SELECT scopes.
        if target.qualifiers:
            parent = scope.parent if scope.can_be_correlated else None
            while target.qualifiers[0].casefold() not in sources and parent is not None:
                for alias, source in _scope_sources(parent, tables, context).items():
                    sources.setdefault(alias, source)
                parent = parent.parent if parent.can_be_correlated else None
        return sources
    except (SqlglotError, RecursionError, ValueError):
        return None


def _nested_fields(type_name: str, path: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    try:
        node = sqlglot.parse_one(
            type_name, into=exp.DataType, read="athena", error_message_context=0
        )
        for part in path:
            if part == "[]":
                index = 1 if str(node.this.value) == "MAP" else 0
                if str(node.this.value) not in {"MAP", "ARRAY"} or len(node.expressions) <= index:
                    return ()
                child = node.expressions[index]
                if not isinstance(child, exp.DataType):
                    return ()
                node = child
                continue
            match = next(
                (
                    child
                    for child in node.expressions
                    if isinstance(child, exp.ColumnDef) and child.name.casefold() == part.casefold()
                ),
                None,
            )
            if match is None:
                return ()
            node = match.args["kind"]
        return tuple(
            (child.name, child.args["kind"].sql(dialect="athena"))
            for child in node.expressions
            if isinstance(child, exp.ColumnDef)
        )
    except (SqlglotError, RecursionError, ValueError):
        return ()


def completion_choices(
    sql: str,
    target: CompletionTarget,
    tables: tuple[CompletionTable, ...],
    context: QueryContext,
) -> tuple[SqlCompletion, ...]:
    """Return at most200 choices scoped to the supplied current AWS identity."""
    if not target.allowed:
        return ()
    rows = tuple(
        table
        for table in tables[:200]
        if (
            table.ref.connection_name == context.connection_name
            and table.ref.region == context.region
            and table.ref.catalog_name == context.catalog
            and table.ref.database_name == context.database
        )
    )
    candidates: list[SqlCompletion] = []
    prefix = target.prefix.casefold()
    qualifiers = target.qualifiers
    if target.table_position:
        expected = (context.catalog, context.database)
        if not qualifiers or tuple(p.casefold() for p in qualifiers) == tuple(
            p.casefold() for p in expected[-len(qualifiers) :]
        ):
            candidates.extend(
                SqlCompletion(t.ref.table_name, quote_athena_identifier(t.ref.table_name), "table")
                for t in rows
            )
        if not qualifiers and (scope := _cursor_scope(sql, target)) is not None:
            candidates.extend(
                SqlCompletion(name, quote_athena_identifier(name), "table", "CTE")
                for name in scope.cte_sources
            )
    else:
        if not qualifiers and sql[target.start : target.start + 1] not in {'"', "`"}:
            candidates.extend(SqlCompletion(word, word, "keyword") for word in _KEYWORDS)
        sources = _cursor_sources(sql, target, rows, context)
        eligible: tuple[_Source, ...] = tuple(sources.values()) if sources else ()
        if sources == {}:
            eligible = tuple(
                ((t.ref.catalog_name, t.ref.database_name, t.ref.table_name), t.columns)
                for t in rows
            )
        field_path = qualifiers
        if qualifiers and sources and qualifiers[0].casefold() in sources:
            eligible = (sources[qualifiers[0].casefold()],)
            field_path = qualifiers[1:]
        elif qualifiers:
            # Resolve database.table and catalog.database.table prefixes while
            # retaining the complete physical identity, then any nested path.
            for size in (3, 2):
                matches = tuple(
                    source
                    for source in eligible
                    if len(qualifiers) >= size
                    and tuple(p.casefold() for p in source[0][-size:])
                    == tuple(p.casefold() for p in qualifiers[:size])
                )
                if matches:
                    eligible = matches
                    field_path = qualifiers[size:]
                    break
        budget = 2000
        for _, columns in eligible:
            for column in columns[:budget]:
                if not field_path:
                    candidates.append(
                        SqlCompletion(
                            column.name,
                            quote_athena_identifier(column.name),
                            "column",
                            column.type_name,
                        )
                    )
                elif column.name.casefold() == field_path[0].casefold():
                    candidates.extend(
                        SqlCompletion(name, quote_athena_identifier(name), "field", kind)
                        for name, kind in _nested_fields(column.type_name, field_path[1:])
                    )
            budget -= min(len(columns), budget)
            if not budget:
                break
    # Stable order, exact single insertion text, no concatenated suggestions.
    unique = {
        (item.kind, item.text): item
        for item in candidates
        if item.label.casefold().startswith(prefix)
    }
    return tuple(
        sorted(unique.values(), key=lambda item: (item.kind, item.label.casefold(), item.text))[
            :MAX_SUGGESTIONS
        ]
    )

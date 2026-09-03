"""Fail-closed validation for Athena query text."""

from __future__ import annotations

import sqlglot
from sqlglot import Dialect, TokenType, exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import traverse_scope

from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.filesystem import ValidationError
from aws_tui.domain.query import QueryContext

_EXPLAIN_FORMATS = frozenset({"GRAPHVIZ", "JSON", "TEXT"})
_EXPLAIN_TYPES = frozenset({"DISTRIBUTED", "IO", "LOGICAL", "VALIDATE"})
_SHOW_SCOPE_TOKENS = frozenset({TokenType.FROM, TokenType.IN})
_DESCRIBE_DOLLAR_SELECTORS = frozenset({"$elem$", "$key$", "$value$"})


class QueryRejectedError(ValidationError):
    """Raised when query text is outside the read-only policy."""


class _TokenCursor:
    def __init__(self, tokens: list[sqlglot.Token], source: str) -> None:
        self.tokens = tokens
        self.source = source
        self.index = 0

    @property
    def at_end(self) -> bool:
        return self.index == len(self.tokens)

    def peek(self) -> sqlglot.Token | None:
        if self.at_end:
            return None
        return self.tokens[self.index]

    def consume_type(self, *token_types: TokenType) -> bool:
        token = self.peek()
        if token is None or token.token_type not in token_types:
            return False
        self.index += 1
        return True

    def consume_word(self, word: str) -> bool:
        token = self.peek()
        if (
            token is None
            or token.token_type in {TokenType.IDENTIFIER, TokenType.STRING}
            or token.text.upper() != word
        ):
            return False
        self.index += 1
        return True

    def consume_one_of(self, words: frozenset[str]) -> bool:
        token = self.peek()
        if (
            token is None
            or token.token_type in {TokenType.IDENTIFIER, TokenType.STRING}
            or token.text.upper() not in words
        ):
            return False
        self.index += 1
        return True

    def consume_qualified_name(self, *, max_parts: int) -> int:
        return len(self.consume_qualified_name_parts(max_parts=max_parts))

    def consume_qualified_name_parts(self, *, max_parts: int) -> tuple[str, ...]:
        first = self._consume_identifier_value()
        if first is None:
            return ()

        parts = [first]
        while self.consume_type(TokenType.DOT):
            if len(parts) == max_parts:
                return ()
            part = self._consume_identifier_value()
            if part is None:
                return ()
            parts.append(part)
        return tuple(parts)

    def _consume_identifier(self) -> bool:
        return self._consume_identifier_value() is not None

    def _consume_identifier_value(self) -> str | None:
        token = self.peek()
        if token is None:
            return None
        if token.token_type in {TokenType.IDENTIFIER, TokenType.VAR}:
            self.index += 1
            return token.text
        if token.token_type is not TokenType.UNKNOWN or token.text != "`":
            return None

        opening = token
        self.index += 1
        content_start = self.index
        while True:
            token = self.peek()
            if token is None:
                return None
            if token.token_type is TokenType.UNKNOWN and token.text == "`":
                if self.index == content_start:
                    return None
                value = self.source[opening.end + 1 : token.start].replace("``", "`")
                self.index += 1
                return value
            self.index += 1


class ReadOnlySqlPolicy:
    def validate(self, sql: str) -> str:
        normalized = sql.strip()
        if not normalized:
            raise QueryRejectedError("query is empty")

        cursor = _TokenCursor(self._tokenize(normalized), normalized)
        statement = cursor.peek()
        if statement is not None and statement.token_type is TokenType.DESCRIBE:
            self._validate_describe_tokens(cursor)
            return normalized

        statements = self._parse(normalized)
        if len(statements) != 1 or statements[0] is None:
            raise QueryRejectedError("exactly one read-only statement is required")

        self._validate_expression(statements[0])
        return normalized

    def table_refs(
        self,
        sql: str,
        context: QueryContext,
    ) -> tuple[TableRef, ...]:
        """Return physical table sources or fail closed on ambiguity."""
        try:
            normalized = self.validate(sql)
            describe_cursor = _TokenCursor(self._tokenize(normalized), normalized)
            first_token = describe_cursor.peek()
            if first_token is not None and first_token.token_type is TokenType.DESCRIBE:
                describe_parts = self._describe_table_parts(describe_cursor)
                if describe_parts is None:
                    return ()
                return self._resolved_table_refs((describe_parts,), context)

            statements = self._parse(normalized)
            if len(statements) != 1 or statements[0] is None:
                return ()
            expression_parts = self._table_parts_for_expression(statements[0])
            return self._resolved_table_refs(expression_parts, context)
        except Exception:
            return ()

    def _table_parts_for_expression(
        self,
        expression: exp.Expr,
    ) -> tuple[tuple[str | None, str | None, str], ...]:
        if isinstance(expression, exp.Describe):
            table = expression.this
            if not isinstance(table, exp.Table):
                return ()
            return ((table.catalog or None, table.db or None, table.name),)

        if isinstance(expression, exp.Command):
            body = expression.expression
            if not isinstance(body, exp.Literal) or not body.is_string:
                return ()
            verb = str(expression.this).upper()
            if verb == "SHOW":
                parts = self._show_columns_table_parts(body.this)
                return (parts,) if parts else ()
            if verb == "EXPLAIN":
                return self._explained_table_parts(body.this)
            return ()

        tables: list[tuple[str | None, str | None, str]] = []
        for scope in traverse_scope(expression):
            for _, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    tables.append(
                        (
                            source.catalog or None,
                            source.db or None,
                            source.name,
                        )
                    )
        return tuple(tables)

    def _resolved_table_refs(
        self,
        parts: tuple[tuple[str | None, str | None, str], ...],
        context: QueryContext,
    ) -> tuple[TableRef, ...]:
        refs: list[TableRef] = []
        seen: set[tuple[str, str, str]] = set()
        for raw_catalog, raw_database, table in parts:
            catalog = raw_catalog or context.catalog
            database = raw_database or context.database
            if not catalog or not database or not table or catalog != context.catalog:
                return ()
            key = (catalog, database, table)
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                TableRef(
                    catalog,
                    database,
                    table,
                    context.connection_name,
                    context.region,
                )
            )
        return tuple(refs)

    def _explained_table_parts(
        self,
        body: str,
    ) -> tuple[tuple[str | None, str | None, str], ...]:
        cursor = _TokenCursor(self._tokenize(body), body)
        if cursor.consume_type(TokenType.ANALYZE):
            return ()
        if cursor.consume_type(TokenType.L_PAREN):
            self._consume_explain_options(cursor)
        statement = cursor.peek()
        if statement is None:
            return ()
        if statement.token_type is TokenType.DESCRIBE:
            parts = self._describe_table_parts(cursor)
            return (parts,) if parts else ()
        explained = self._parse(body[statement.start :])
        if len(explained) != 1 or explained[0] is None:
            return ()
        return self._table_parts_for_expression(explained[0])

    def _describe_table_parts(
        self,
        cursor: _TokenCursor,
    ) -> tuple[str | None, str | None, str] | None:
        if not cursor.consume_type(TokenType.DESCRIBE):
            return None
        cursor.consume_one_of(frozenset({"EXTENDED", "FORMATTED"}))
        parts = cursor.consume_qualified_name_parts(max_parts=2)
        if len(parts) == 1:
            return None, None, parts[0]
        if len(parts) == 2:
            return None, parts[0], parts[1]
        return None

    def _show_columns_table_parts(
        self,
        body: str,
    ) -> tuple[str | None, str | None, str] | None:
        cursor = _TokenCursor(self._tokenize(body), body)
        if not cursor.consume_word("COLUMNS") or not cursor.consume_type(*_SHOW_SCOPE_TOKENS):
            return None
        target = cursor.consume_qualified_name_parts(max_parts=3)
        if not target:
            return None
        scope: tuple[str, ...] = ()
        if cursor.consume_type(*_SHOW_SCOPE_TOKENS):
            if len(target) != 1:
                return None
            scope = cursor.consume_qualified_name_parts(max_parts=2)
            if not scope:
                return None
        if not cursor.at_end:
            return None

        if scope:
            if len(scope) == 1:
                return None, scope[0], target[0]
            return scope[0], scope[1], target[0]
        if len(target) == 1:
            return None, None, target[0]
        if len(target) == 2:
            return None, target[0], target[1]
        return target[0], target[1], target[2]

    def _parse(self, sql: str) -> list[exp.Expr | None]:
        try:
            return sqlglot.parse(sql, read="athena", error_message_context=0)
        except SqlglotError:
            raise QueryRejectedError("query could not be parsed as Athena SQL") from None

    def _validate_expression(self, expression: exp.Expr) -> None:
        if isinstance(expression, (exp.Select, exp.SetOperation)):
            self._reject_write_nodes(expression)
            return
        if isinstance(expression, exp.Describe):
            self._validate_describe(expression)
            return
        if isinstance(expression, exp.Command):
            self._validate_command(expression)
            return
        raise QueryRejectedError(f"statement type {expression.key!r} is not read-only")

    def _validate_describe(self, describe: exp.Describe) -> None:
        self._reject_write_nodes(describe)
        if (
            not isinstance(describe.this, exp.Table)
            or describe.args.get("kind") is not None
            or describe.args.get("style") not in {None, "EXTENDED", "FORMATTED"}
            or describe.args.get("expressions") is not None
            or describe.args.get("format") is not None
            or describe.args.get("as_json") is not False
        ):
            raise QueryRejectedError("DESCRIBE must use Athena table grammar")

        partition = describe.args.get("partition")
        if partition is not None and not isinstance(partition, exp.Partition):
            raise QueryRejectedError("DESCRIBE must use Athena table grammar")

    def _validate_describe_tokens(self, cursor: _TokenCursor) -> None:
        if not cursor.consume_type(TokenType.DESCRIBE):
            raise QueryRejectedError("DESCRIBE must use Athena table grammar")

        cursor.consume_one_of(frozenset({"EXTENDED", "FORMATTED"}))
        if cursor.consume_qualified_name(max_parts=2) == 0:
            raise QueryRejectedError("DESCRIBE must use Athena table grammar")

        if cursor.consume_word("PARTITION") and not self._consume_describe_partition(cursor):
            raise QueryRejectedError("DESCRIBE must use Athena table grammar")

        if cursor._consume_identifier() and not self._consume_describe_column_selectors(cursor):
            raise QueryRejectedError("DESCRIBE must use Athena table grammar")
        if cursor.consume_type(TokenType.SEMICOLON):
            if cursor.at_end:
                return
        elif cursor.at_end:
            return

        raise QueryRejectedError("DESCRIBE must use Athena table grammar")

    def _consume_describe_partition(self, cursor: _TokenCursor) -> bool:
        if not cursor.consume_type(TokenType.L_PAREN):
            return False

        while True:
            if (
                not cursor._consume_identifier()
                or not cursor.consume_type(TokenType.EQ)
                or not cursor.consume_type(TokenType.STRING, TokenType.NUMBER)
            ):
                return False
            if cursor.consume_type(TokenType.R_PAREN):
                return True
            if not cursor.consume_type(TokenType.COMMA):
                return False

    def _consume_describe_column_selectors(self, cursor: _TokenCursor) -> bool:
        while cursor.consume_type(TokenType.DOT):
            selector = cursor.peek()
            if selector is not None and selector.token_type is TokenType.STRING:
                if selector.text not in _DESCRIBE_DOLLAR_SELECTORS:
                    return False
                cursor.index += 1
            elif not cursor._consume_identifier():
                return False
        return True

    def _validate_command(self, command: exp.Command) -> None:
        verb = str(command.this).upper()
        body = command.expression
        if not isinstance(body, exp.Literal) or not body.is_string:
            raise QueryRejectedError(f"statement type {command.key!r} is not read-only")

        if verb == "SHOW":
            self._validate_show(body.this)
            return

        if verb == "EXPLAIN":
            self._validate_explain(body.this)
            return

        raise QueryRejectedError(f"statement type {command.key!r} is not read-only")

    def _validate_show(self, body: str) -> None:
        cursor = _TokenCursor(self._tokenize(body), body)
        token = cursor.peek()
        if token is None or token.token_type is not TokenType.VAR:
            raise QueryRejectedError("SHOW form is not read-only")

        form = token.text.upper()
        cursor.index += 1
        valid = False
        if form in {"DATABASES", "SCHEMAS"}:
            valid = self._validate_show_databases(cursor)
        elif form == "TABLES":
            valid = self._validate_show_tables(cursor)
        elif form == "COLUMNS":
            valid = self._validate_show_columns(cursor)
        elif form == "PARTITIONS":
            valid = cursor.consume_qualified_name(max_parts=3) > 0 and cursor.at_end
        elif form == "TBLPROPERTIES":
            valid = self._validate_show_tblproperties(cursor)
        elif form == "VIEWS":
            valid = self._validate_show_views(cursor)

        if not valid:
            raise QueryRejectedError("SHOW form is not read-only")

    def _validate_show_databases(self, cursor: _TokenCursor) -> bool:
        if cursor.consume_type(TokenType.IN) and cursor.consume_qualified_name(max_parts=1) == 0:
            return False
        if cursor.consume_type(TokenType.LIKE) and not cursor.consume_type(TokenType.STRING):
            return False
        return cursor.at_end

    def _validate_show_tables(self, cursor: _TokenCursor) -> bool:
        if cursor.consume_type(TokenType.IN) and cursor.consume_qualified_name(max_parts=2) == 0:
            return False
        cursor.consume_type(TokenType.STRING)
        return cursor.at_end

    def _validate_show_columns(self, cursor: _TokenCursor) -> bool:
        if not cursor.consume_type(*_SHOW_SCOPE_TOKENS):
            return False
        target_parts = cursor.consume_qualified_name(max_parts=3)
        if target_parts == 0:
            return False
        if cursor.consume_type(*_SHOW_SCOPE_TOKENS) and (
            target_parts != 1 or cursor.consume_qualified_name(max_parts=2) == 0
        ):
            return False
        return cursor.at_end

    def _validate_show_tblproperties(self, cursor: _TokenCursor) -> bool:
        if cursor.consume_qualified_name(max_parts=3) == 0:
            return False
        if cursor.consume_type(TokenType.L_PAREN) and (
            not cursor.consume_type(TokenType.STRING) or not cursor.consume_type(TokenType.R_PAREN)
        ):
            return False
        return cursor.at_end

    def _validate_show_views(self, cursor: _TokenCursor) -> bool:
        if cursor.consume_type(TokenType.IN) and cursor.consume_qualified_name(max_parts=2) == 0:
            return False
        if cursor.consume_type(TokenType.LIKE) and not cursor.consume_type(TokenType.STRING):
            return False
        return cursor.at_end

    def _validate_explain(self, body: str) -> None:
        tokens = self._tokenize(body)
        cursor = _TokenCursor(tokens, body)
        if cursor.consume_type(TokenType.ANALYZE):
            raise QueryRejectedError("EXPLAIN ANALYZE executes its statement")

        if cursor.consume_type(TokenType.L_PAREN):
            self._consume_explain_options(cursor)

        statement = cursor.peek()
        if statement is None or statement.token_type is TokenType.ANALYZE:
            raise QueryRejectedError("EXPLAIN form is not read-only")

        if statement.token_type is TokenType.DESCRIBE:
            self._validate_describe_tokens(cursor)
            return

        explained = self._parse(body[statement.start :])
        if len(explained) != 1 or explained[0] is None:
            raise QueryRejectedError("EXPLAIN form is not read-only")
        self._validate_expression(explained[0])

    def _consume_explain_options(self, cursor: _TokenCursor) -> None:
        seen: set[str] = set()
        while True:
            if cursor.consume_word("FORMAT"):
                option = "FORMAT"
                valid_value = cursor.consume_one_of(_EXPLAIN_FORMATS)
            elif cursor.consume_word("TYPE"):
                option = "TYPE"
                valid_value = cursor.consume_one_of(_EXPLAIN_TYPES)
            else:
                raise QueryRejectedError("EXPLAIN option is not read-only")

            if option in seen or not valid_value:
                raise QueryRejectedError("EXPLAIN option is not read-only")
            seen.add(option)

            if cursor.consume_type(TokenType.R_PAREN):
                return
            if not cursor.consume_type(TokenType.COMMA):
                raise QueryRejectedError("EXPLAIN option is not read-only")

    def _tokenize(self, sql: str) -> list[sqlglot.Token]:
        try:
            return [
                token
                for token in Dialect.get_or_raise("athena").tokenizer().tokenize(sql)
                if token.token_type is not TokenType.HIVE_TOKEN_STREAM
            ]
        except SqlglotError:
            raise QueryRejectedError("query could not be parsed as Athena SQL") from None

    def _reject_write_nodes(self, expression: exp.Expr) -> None:
        forbidden = (
            exp.DDL,
            exp.DML,
            exp.Command,
            exp.Into,
            exp.Lock,
            exp.Analyze,
            exp.Alter,
            exp.Drop,
            exp.Execute,
            exp.Transaction,
        )
        for node in expression.walk():
            if node is not expression and isinstance(node, forbidden):
                raise QueryRejectedError(f"nested statement type {node.key!r} is not read-only")


def quote_athena_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def quote_athena_table_ref(ref: TableRef) -> str:
    return ".".join(
        quote_athena_identifier(part)
        for part in (ref.catalog_name, ref.database_name, ref.table_name)
    )


def quote_athena_query_table_ref(ref: TableRef) -> str:
    """Quote a table relative to the request's resolved catalog context."""
    return ".".join(quote_athena_identifier(part) for part in (ref.database_name, ref.table_name))


def select_starter_sql(
    ref: TableRef,
    snapshot_id: int | None = None,
) -> str:
    if snapshot_id is not None and (
        isinstance(snapshot_id, bool) or not isinstance(snapshot_id, int) or snapshot_id < 0
    ):
        raise ValueError("snapshot ID must be a non-negative integer")
    qualified = quote_athena_query_table_ref(ref)
    travel = f" FOR VERSION AS OF {snapshot_id}" if snapshot_id is not None else ""
    return f"SELECT * FROM {qualified}{travel} LIMIT 5"


__all__ = [
    "QueryRejectedError",
    "ReadOnlySqlPolicy",
    "quote_athena_identifier",
    "quote_athena_query_table_ref",
    "quote_athena_table_ref",
    "select_starter_sql",
]

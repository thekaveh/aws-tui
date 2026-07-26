"""Fail-closed validation for Athena query text."""

from __future__ import annotations

import sqlglot
from sqlglot import Dialect, TokenType, exp
from sqlglot.errors import SqlglotError

from aws_tui.domain.filesystem import ValidationError


class QueryRejectedError(ValidationError):
    """Raised when query text is outside the read-only policy."""


class ReadOnlySqlPolicy:
    def validate(self, sql: str) -> str:
        normalized = sql.strip()
        if not normalized:
            raise QueryRejectedError("query is empty")

        statements = self._parse(normalized)
        if len(statements) != 1 or statements[0] is None:
            raise QueryRejectedError("exactly one read-only statement is required")

        self._validate_expression(statements[0])
        return normalized

    def _parse(self, sql: str) -> list[exp.Expr | None]:
        try:
            return sqlglot.parse(sql, read="athena", error_message_context=0)
        except SqlglotError as exc:
            raise QueryRejectedError("query could not be parsed as Athena SQL") from exc

    def _validate_expression(self, expression: exp.Expr) -> None:
        if isinstance(expression, exp.Select):
            self._reject_write_nodes(expression)
            return
        if isinstance(expression, exp.Describe):
            return
        if isinstance(expression, exp.Command):
            self._validate_command(expression)
            return
        raise QueryRejectedError(f"statement type {expression.key!r} is not read-only")

    def _validate_command(self, command: exp.Command) -> None:
        verb = str(command.this).upper()
        body = command.expression
        if not isinstance(body, exp.Literal) or not body.is_string:
            raise QueryRejectedError(f"statement type {command.key!r} is not read-only")

        if verb == "SHOW":
            tokens = self._tokenize(body.this)
            if any(token.token_type is TokenType.CREATE for token in tokens):
                raise QueryRejectedError("SHOW CREATE TABLE is not read-only")
            return

        if verb == "EXPLAIN":
            tokens = self._tokenize(body.this)
            if tokens and tokens[0].token_type is TokenType.ANALYZE:
                raise QueryRejectedError("EXPLAIN ANALYZE executes its statement")

            explained = self._parse(body.this)
            if len(explained) == 1 and explained[0] is not None:
                self._validate_expression(explained[0])
                return

        raise QueryRejectedError(f"statement type {command.key!r} is not read-only")

    def _tokenize(self, sql: str) -> list[sqlglot.Token]:
        try:
            return Dialect.get_or_raise("athena").tokenizer().tokenize(sql)
        except SqlglotError as exc:
            raise QueryRejectedError("query could not be parsed as Athena SQL") from exc

    def _reject_write_nodes(self, select: exp.Select) -> None:
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
        for node in select.walk():
            if node is not select and isinstance(node, forbidden):
                raise QueryRejectedError(f"nested statement type {node.key!r} is not read-only")


__all__ = ["QueryRejectedError", "ReadOnlySqlPolicy"]

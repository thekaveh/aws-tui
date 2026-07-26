"""Fail-closed validation for Athena query text."""

from __future__ import annotations

import sqlglot
from sqlglot import Dialect, TokenType, exp
from sqlglot.errors import SqlglotError

from aws_tui.domain.filesystem import ValidationError

_EXPLAIN_FORMATS = frozenset({"GRAPHVIZ", "JSON", "TEXT"})
_EXPLAIN_TYPES = frozenset({"DISTRIBUTED", "IO", "LOGICAL", "VALIDATE"})
_SHOW_SCOPE_TOKENS = frozenset({TokenType.FROM, TokenType.IN})


class QueryRejectedError(ValidationError):
    """Raised when query text is outside the read-only policy."""


class _TokenCursor:
    def __init__(self, tokens: list[sqlglot.Token]) -> None:
        self.tokens = tokens
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
        if not self._consume_identifier():
            return 0

        parts = 1
        while self.consume_type(TokenType.DOT):
            if parts == max_parts or not self._consume_identifier():
                return 0
            parts += 1
        return parts

    def _consume_identifier(self) -> bool:
        token = self.peek()
        if token is None:
            return False
        if token.token_type in {TokenType.IDENTIFIER, TokenType.VAR}:
            self.index += 1
            return True
        if token.token_type is not TokenType.UNKNOWN or token.text != "`":
            return False

        self.index += 1
        content_start = self.index
        while True:
            token = self.peek()
            if token is None:
                return False
            if token.token_type is TokenType.UNKNOWN and token.text == "`":
                if self.index == content_start:
                    return False
                self.index += 1
                return True
            self.index += 1


class ReadOnlySqlPolicy:
    def validate(self, sql: str) -> str:
        normalized = sql.strip()
        if not normalized:
            raise QueryRejectedError("query is empty")

        cursor = _TokenCursor(self._tokenize(normalized))
        statement = cursor.peek()
        if statement is not None and statement.token_type is TokenType.DESCRIBE:
            self._validate_describe_tokens(cursor)
            return normalized

        statements = self._parse(normalized)
        if len(statements) != 1 or statements[0] is None:
            raise QueryRejectedError("exactly one read-only statement is required")

        self._validate_expression(statements[0])
        return normalized

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

        cursor._consume_identifier()
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
        cursor = _TokenCursor(self._tokenize(body))
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
        cursor = _TokenCursor(tokens)
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


__all__ = ["QueryRejectedError", "ReadOnlySqlPolicy"]

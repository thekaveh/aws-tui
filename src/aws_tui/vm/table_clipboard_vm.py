from __future__ import annotations

from dataclasses import dataclass

import reactivex as rx
from vmx import ComponentVMOf, Message, MessageHub, RelayCommandOf
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.sql_policy import quote_athena_table_ref


@dataclass(frozen=True, slots=True)
class CopiedTableReference:
    table_ref: TableRef
    sql_identifier: str


class TableClipboardVM:
    """App-lifetime typed clipboard for one replaceable table reference."""

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._inner: ComponentVMOf[CopiedTableReference | None] = (
            ComponentVMOf[CopiedTableReference | None]
            .builder()
            .name("table_clipboard")
            .model(None)
            .services(hub, dispatcher)
            .build()
        )
        self._copy_command: RelayCommandOf[TableRef] = (
            RelayCommandOf[TableRef]
            .builder()
            .predicate(lambda ref: isinstance(ref, TableRef))
            .task(self._copy)
            .build()
        )

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def copied_table(self) -> CopiedTableReference | None:
        return self._inner.model

    @property
    def copy_command(self) -> RelayCommandOf[TableRef]:
        return self._copy_command

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self._inner.property_changed

    def construct(self) -> None:
        self._inner.construct()

    def dispose(self) -> None:
        first_error: BaseException | None = None
        for dispose in (self._copy_command.dispose, self._inner.dispose):
            try:
                dispose()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def _copy(self, table_ref: TableRef | None) -> None:
        if not isinstance(table_ref, TableRef):
            return
        self._inner.model = CopiedTableReference(
            table_ref=table_ref,
            sql_identifier=quote_athena_table_ref(table_ref),
        )


__all__ = ["CopiedTableReference", "TableClipboardVM"]

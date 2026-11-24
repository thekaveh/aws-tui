from __future__ import annotations

from dataclasses import dataclass

import reactivex as rx
from vmx import ComponentVMOf, Dispatcher, Message, MessageHub, RelayCommandOf

from aws_tui.domain.data_catalog import TableRef
from aws_tui.domain.sql_policy import quote_athena_table_ref


@dataclass(frozen=True, slots=True)
class CopiedTableReference:
    table_ref: TableRef
    sql_identifier: str


class TableClipboardVM(ComponentVMOf[CopiedTableReference | None]):
    """App-lifetime typed clipboard for one replaceable table reference."""

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        super().__init__(
            name="table_clipboard",
            hint="",
            initial_model=None,
            modeled_hinter=lambda _model: "",
            on_model_changed=None,
            hub=hub,
            dispatcher=dispatcher,
        )
        self._copy_command: RelayCommandOf[TableRef] = (
            RelayCommandOf[TableRef]
            .builder()
            .predicate(lambda ref: isinstance(ref, TableRef))
            .task(self._copy)
            .build()
        )

    @property
    def copied_table(self) -> CopiedTableReference | None:
        return self.model

    @property
    def copy_command(self) -> RelayCommandOf[TableRef]:
        return self._copy_command

    @property
    def on_property_changed(self) -> rx.Observable[str]:
        return self.property_changed

    def _on_dispose(self) -> None:
        self._copy_command.dispose()

    def _copy(self, table_ref: TableRef | None) -> None:
        if not isinstance(table_ref, TableRef):
            return
        self.model = CopiedTableReference(
            table_ref=table_ref,
            sql_identifier=quote_athena_table_ref(table_ref),
        )


__all__ = ["CopiedTableReference", "TableClipboardVM"]

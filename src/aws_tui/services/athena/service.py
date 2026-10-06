"""Amazon Athena service composition."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, Protocol

from vmx import Message, MessageHub
from vmx.services.dispatcher import Dispatcher

from aws_tui.domain.athena import AthenaClient
from aws_tui.domain.athena_runner import AthenaQueryRunner
from aws_tui.domain.sql_policy import ReadOnlySqlPolicy
from aws_tui.infra.aws_session import AwsSession
from aws_tui.infra.connection_resolver import Connection
from aws_tui.vm.athena.drafts_vm import AthenaDraftsVM
from aws_tui.vm.athena.page_vm import AthenaPageVM
from aws_tui.vm.service_source_vm import SelectionScope, ServiceSelectionStore
from aws_tui.vm.services_protocol import RecoveryServiceVM, ServiceDescriptor


class AthenaClientProtocol(Protocol):
    """Structural boundary consumed by the Athena viewmodels."""

    async def list_workgroups_page(
        self,
        *,
        start_token: str | None = None,
    ) -> tuple[list[Any], str | None]: ...

    async def get_workgroup(self, name: str) -> Any: ...

    async def list_catalogs_page(
        self,
        *,
        workgroup: str | None = None,
        start_token: str | None = None,
    ) -> tuple[list[Any], str | None]: ...

    async def list_databases_page(
        self,
        catalog: str,
        *,
        workgroup: str | None = None,
        start_token: str | None = None,
    ) -> tuple[list[Any], str | None]: ...

    async def list_query_executions_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[Any], str | None]: ...

    async def get_query_execution(self, execution_id: str) -> Any: ...

    async def get_query_executions(self, execution_ids: list[str]) -> tuple[Any, ...]: ...

    async def start_query(
        self,
        sql: str,
        context: Any,
        *,
        request_token: str,
    ) -> Any: ...

    async def stop_query(self, execution_id: str) -> None: ...

    async def get_results_page(
        self,
        execution_id: str,
        *,
        start_token: str | None = None,
    ) -> Any: ...

    async def list_named_queries_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[str], str | None]: ...

    async def get_named_queries(self, ids: list[str]) -> tuple[Any, ...]: ...

    async def list_prepared_statements_page(
        self,
        workgroup: str,
        *,
        start_token: str | None = None,
    ) -> tuple[list[Any], str | None]: ...

    async def get_prepared_statement(self, name: str, workgroup: str) -> Any: ...


AthenaClientFactory = Callable[[Connection], AthenaClientProtocol]
SqlPolicyFactory = Callable[[], ReadOnlySqlPolicy]


class AthenaService:
    descriptor: ClassVar[ServiceDescriptor] = ServiceDescriptor(
        id="athena",
        label="Athena",
        icon="🔎",
    )

    def __init__(
        self,
        *,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
        aws_session: AwsSession,
        athena_client_factory: AthenaClientFactory | None = None,
        sql_policy_factory: SqlPolicyFactory | None = None,
        selection_store: ServiceSelectionStore | None = None,
        drafts: AthenaDraftsVM | None = None,
        source_check_factory: Callable[[Connection], Callable[[], Awaitable[bool]]] | None = None,
    ) -> None:
        self._drafts = drafts
        self._source_check_factory = source_check_factory
        self._hub = hub
        self._dispatcher = dispatcher
        self._aws_session = aws_session
        self._client_factory = athena_client_factory
        self._policy_factory = sql_policy_factory or ReadOnlySqlPolicy
        self._selections = selection_store or ServiceSelectionStore()

    def supports(self, connection: Connection) -> bool:
        return connection.kind == "aws"

    def build_vm(self, connection: Connection) -> AthenaPageVM:
        return self._build_vm(connection, self._selections, drafts_active=True)

    def build_recovery_vm(self, connection: Connection) -> RecoveryServiceVM:
        selections = self._selections.clone()
        scope = SelectionScope(self.descriptor.id, connection.name, connection.region)
        page = self._build_vm(connection, selections, drafts_active=False)

        def commit_selection() -> None:
            self._selections.replace_scope_from(selections, scope)
            page.activate_drafts()

        return RecoveryServiceVM(vm=page, commit_selection=commit_selection)

    def _build_vm(
        self,
        connection: Connection,
        selections: ServiceSelectionStore,
        *,
        drafts_active: bool,
    ) -> AthenaPageVM:
        client: AthenaClientProtocol = (
            self._client_factory(connection)
            if self._client_factory is not None
            else AthenaClient(
                aws_session=self._aws_session,
                connection=connection,
            )
        )
        source_check = (
            self._source_check_factory(connection)
            if self._source_check_factory is not None
            else None
        )
        policy = self._policy_factory()
        return AthenaPageVM(
            client=client,
            policy=policy,
            runner=AthenaQueryRunner(client, policy),
            connection=connection,
            selection_store=selections,
            drafts=self._drafts,
            drafts_active=drafts_active,
            source_is_current=source_check,
            hub=self._hub,
            dispatcher=self._dispatcher,
        )


__all__ = [
    "AthenaClientFactory",
    "AthenaClientProtocol",
    "AthenaService",
    "SqlPolicyFactory",
]

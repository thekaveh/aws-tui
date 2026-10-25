"""Bind service adapters to a connection's mutation admission policy."""

from collections.abc import Callable
from typing import TypeVar, cast

from aws_tui.domain.mutation_guard import GuardedRemote, MutationCheck, fixed_mutation_check
from aws_tui.infra.connection_resolver import Connection

MutationCheckFactory = Callable[[Connection], MutationCheck]
T = TypeVar("T")


def protect_remote(target: T, connection: Connection, factory: MutationCheckFactory | None) -> T:
    if factory is None and not connection.protected:
        return target
    check = (
        factory(connection) if factory is not None else fixed_mutation_check(connection.protected)
    )
    return cast(T, GuardedRemote(target, check))

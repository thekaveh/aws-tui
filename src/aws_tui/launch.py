"""Read-only, client-free validation of session launch selectors."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aws_tui.composition import ServiceDefinition

from aws_tui.domain.filesystem import PathRef
from aws_tui.infra.config_store import ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver


class LaunchError(ValueError):
    """Safe, single-line launch error for the CLI."""


@dataclass(frozen=True, slots=True)
class LaunchRequest:
    connection: str | None = None
    profile: str | None = None
    region: str | None = None
    service: str | None = None
    location: str | None = None

    @property
    def explicit(self) -> bool:
        return any(
            value is not None
            for value in (
                self.connection,
                self.profile,
                self.region,
                self.service,
                self.location,
            )
        )


@dataclass(frozen=True, slots=True)
class LaunchLocation:
    scheme: str
    path: PathRef
    native_path: Path | None = None
    native_root: Path | None = None


@dataclass(frozen=True, slots=True)
class ResolvedLaunch:
    request: LaunchRequest
    underlying: Connection | None
    connection: Connection | None
    service_id: str
    location: LaunchLocation | None
    source_entry: ConnectionEntry | None = None


def service_definitions() -> tuple[ServiceDefinition, ...]:
    # The composition root owns the service definitions used by the registry.
    from aws_tui.composition import service_definitions as definitions

    return definitions()


def _validate_value(value: str, flag: str) -> None:
    if not value or any(unicodedata.category(char) == "Cc" for char in value):
        raise LaunchError(f"{flag} requires a nonempty value without control characters")


def _local_path_ref(native: PurePath) -> tuple[PathRef, str | None]:
    """Preserve native drives; root shares using LocalFS's rooted path contract."""
    if native.drive.startswith("\\\\"):
        return PathRef(native.relative_to(native.anchor).parts), native.anchor
    return PathRef.from_posix(native.as_posix()), None


def _location(value: str) -> LaunchLocation:
    if value.startswith("s3://"):
        literal = value[5:]
        bucket = literal.split("/", 1)[0]
        if not bucket or any(char in bucket for char in "?#\\:"):
            raise LaunchError("--location requires s3://BUCKET[/PREFIX] with a bucket")
        # S3 directory paths omit exactly one listing delimiter. Other empty
        # components are literal key characters: daily// becomes (daily, ""),
        # so joining a listed child addresses daily//child. from_posix would
        # discard those meaningful empty components.
        return LaunchLocation("s3", PathRef(tuple(literal.removesuffix("/").split("/"))))
    if "://" in value or value.startswith("arn:"):
        raise LaunchError("--location supports only s3://BUCKET[/PREFIX] or a local directory")
    try:
        native = Path(value).expanduser().resolve(strict=True)
        if not native.is_dir():
            raise LaunchError("--location must name an existing directory")
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, LaunchError):
            raise
        raise LaunchError("--location must name an existing directory") from None
    path, native_root = _local_path_ref(native)
    return LaunchLocation(
        "local", path, native, Path(native_root) if native_root is not None else None
    )


def resolve_launch(
    request: LaunchRequest,
    *,
    config_store: ConfigStore | None = None,
    resolver: ConnectionResolver | None = None,
) -> ResolvedLaunch:
    """Resolve only the intended local identity; never persist or contact AWS."""
    for flag in ("connection", "profile", "region", "service", "location"):
        value = getattr(request, flag)
        if value is not None:
            _validate_value(value, f"--{flag}")
    if request.connection is not None and request.profile is not None:
        raise LaunchError("--connection and --profile are mutually exclusive")
    if (
        request.region is not None
        and re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*-[0-9]+", request.region) is None
    ):
        raise LaunchError("--region requires a region identifier such as eu-west-1")
    service_id = request.service or "s3"
    definition = next(
        (item for item in service_definitions() if item.descriptor.id == service_id), None
    )
    if definition is None:
        raise LaunchError("--service requires a registered service ID")
    if request.location is not None and service_id != "s3":
        raise LaunchError("--location can only be combined with --service s3")
    location = _location(request.location) if request.location is not None else None
    store = config_store if config_store is not None else ConfigStore(read_only=True)
    selected_resolver = (
        resolver
        if resolver is not None
        else ConnectionResolver(config_store=store, read_credentials=False, quiet_discovery=True)
    )
    underlying: Connection | None
    try:
        if request.connection is not None:
            underlying = selected_resolver.resolve_selected(request.connection)
        elif request.profile is not None:
            underlying = selected_resolver.resolve_profile(request.profile)
        else:
            underlying = selected_resolver.resolve_default()
    except Exception:
        flag = "--profile" if request.profile is not None else "--connection"
        raise LaunchError(f"{flag} could not resolve the selected source") from None
    if underlying is None:
        if location is None or location.scheme != "local" or service_id != "s3":
            raise LaunchError("launch requires an available connection or a local directory")
        connection = None
    else:
        connection = (
            replace(underlying, region=request.region) if request.region is not None else underlying
        )
        if not definition.supports(connection):
            raise LaunchError("--service is unsupported by the selected connection")
    try:
        source_entry = (
            store.load().connections.get(underlying.name)
            if underlying is not None and underlying.source == "config"
            else None
        )
    except Exception:
        raise LaunchError("selected source changed during launch preflight") from None
    return ResolvedLaunch(request, underlying, connection, service_id, location, source_entry)

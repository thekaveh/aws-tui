"""S3ConnectionsVM — CRUD over kind='s3-compatible' connections."""

from __future__ import annotations

from dataclasses import replace

from vmx import ComponentVM, Message, MessageHub
from vmx.lifecycle.status import ConstructionStatus
from vmx.services.dispatcher import Dispatcher

from aws_tui.infra.config_store import ConfigError, ConfigStore, ConnectionEntry
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver
from aws_tui.infra.keychain import (
    KeychainBackend,
    app_keychain_revision_service,
    app_keychain_service,
)
from aws_tui.vm.messages import ConnectionListChangedMessage
from aws_tui.vm.settings.s3_compat_form import S3CompatForm


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def entry_from_s3_form(form: S3CompatForm) -> ConnectionEntry:
    """Convert a filled :class:`S3CompatForm` to a normalized config entry."""
    return ConnectionEntry(
        name=form.name.strip(),
        kind="s3-compatible",
        region=form.region.strip(),
        endpoint_url=form.endpoint_url.strip(),
        credentials="static",
        access_key_id=form.access_key_id.strip(),
        secret_access_key=form.secret_access_key.strip(),
        session_token=_blank_to_none(form.session_token),
        force_path_style=form.force_path_style,
        verify_tls=form.verify_tls,
    )


class S3ConnectionsVM:
    """List + CRUD over the s3-compatible subset of TOML connections.

    The CRUD verbs (``add`` / ``update`` / ``remove``) validate, persist
    via :class:`ConfigStore`, then publish a
    :class:`ConnectionListChangedMessage` on the hub. Subscribers
    (``SettingsVM``, ``NavMenuVM``, ``AwsTuiApp``) react to the
    message; this VM never tells them directly.
    """

    def __init__(
        self,
        *,
        resolver: ConnectionResolver,
        config_store: ConfigStore,
        keychain: KeychainBackend | None = None,
        hub: MessageHub[Message],
        dispatcher: Dispatcher,
    ) -> None:
        self._resolver: ConnectionResolver = resolver
        self._config_store: ConfigStore = config_store
        self._keychain = keychain
        self._hub: MessageHub[Message] = hub
        self._dispatcher: Dispatcher = dispatcher
        self._inner: ComponentVM = (
            ComponentVM.builder().name("s3_connections").services(hub, dispatcher).build()
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def construct(self) -> None:
        self._inner.construct()

    def destruct(self) -> None:
        self._inner.destruct()

    def dispose(self) -> None:
        self._inner.dispose()

    @property
    def status(self) -> ConstructionStatus:
        return self._inner.status

    @property
    def is_constructed(self) -> bool:
        return self._inner.is_constructed

    @property
    def name(self) -> str:
        return self._inner.name

    # ── Read ───────────────────────────────────────────────────────────────

    @property
    def dispatcher(self) -> Dispatcher:
        return self._dispatcher

    @property
    def connections(self) -> tuple[Connection, ...]:
        """All s3-compatible connections, in resolver order.

        Re-derived from the resolver each call — the resolver has no
        cache, so a recent CRUD is reflected immediately.
        """
        return tuple(c for c in self._resolver.list() if c.kind == "s3-compatible")

    @property
    def names(self) -> frozenset[str]:
        return frozenset(c.name for c in self.connections)

    # ── Write ──────────────────────────────────────────────────────────────

    def add(self, entry: ConnectionEntry) -> None:
        """Validate uniqueness, persist via ConfigStore, publish 'added'."""
        self._ensure_writable()
        with self._config_store.transaction():
            current = self._config_store.load()
            if entry.name in current.connections or entry.name in {
                c.name for c in self._resolver.list()
            }:
                raise ValueError(f"connection {entry.name!r} already exists")
            service = app_keychain_service(entry.name)
            old_secrets = self._read_keychain_service(service)
            try:
                persisted = self._persist_credentials(entry, service=service)
                self._config_store.add_connection(persisted)
            except BaseException as exc:
                rollback_errors = self._restore_keychain_service(service, old_secrets)
                if rollback_errors:
                    raise _rollback_failure("add connection", exc, rollback_errors) from exc
                raise
        self._hub.send(ConnectionListChangedMessage(names=(entry.name,), change="added"))

    def update(self, name: str, entry: ConnectionEntry) -> None:
        """Validate rename-disallowed, persist, publish 'updated'."""
        self._ensure_writable()
        if entry.name != name:
            raise ValueError(
                f"connection cannot be renamed in place: old={name!r}, new={entry.name!r}"
            )
        with self._config_store.transaction():
            old_entry = self._config_store.load().connections.get(name)
            old_secrets = self._read_keychain_credentials(old_entry)
            staged_service = self._next_revision_service(name, old_entry)
            try:
                persisted = self._persist_credentials(entry, service=staged_service)
                self._config_store.update_connection(name, persisted)
            except BaseException as exc:
                rollback_errors = self._discard_keychain_service(staged_service)
                if rollback_errors:
                    raise _rollback_failure("update connection", exc, rollback_errors) from exc
                raise
            try:
                self._delete_keychain_credentials(old_entry)
            except BaseException as exc:
                try:
                    if old_entry is not None:
                        self._config_store.update_connection(name, old_entry)
                except Exception as rollback_exc:
                    rollback_errors = [rollback_exc]
                    config_restored = False
                else:
                    rollback_errors = []
                    config_restored = True
                rollback_errors.extend(
                    self._restore_keychain_service(self._service_from_entry(old_entry), old_secrets)
                )
                if config_restored:
                    rollback_errors.extend(self._discard_keychain_service(staged_service))
                if rollback_errors:
                    raise _rollback_failure("update connection", exc, rollback_errors) from exc
                raise
        self._hub.send(ConnectionListChangedMessage(names=(name,), change="updated"))

    def remove(self, name: str) -> None:
        """Persist removal, publish 'deleted'."""
        self._ensure_writable()
        with self._config_store.transaction():
            old_entry = self._config_store.load().connections.get(name)
            old_secrets = self._read_keychain_credentials(old_entry)
            self._config_store.remove_connection(name)
            try:
                self._delete_keychain_credentials(old_entry)
            except BaseException as exc:
                rollback_errors: list[Exception] = []
                if old_entry is not None:
                    try:
                        self._config_store.add_connection(old_entry)
                    except Exception as rollback_exc:
                        rollback_errors.append(rollback_exc)
                rollback_errors.extend(
                    self._restore_keychain_service(self._service_from_entry(old_entry), old_secrets)
                )
                if rollback_errors:
                    raise _rollback_failure("remove connection", exc, rollback_errors) from exc
                raise
        self._hub.send(ConnectionListChangedMessage(names=(name,), change="deleted"))

    # ── Form helpers ───────────────────────────────────────────────────────

    def find_by_name(self, name: str) -> Connection | None:
        """Look up a connection by name; returns None if not found."""
        for c in self.connections:
            if c.name == name:
                return c
        return None

    def entry_from_form(self, form: S3CompatForm) -> ConnectionEntry:
        """Convert a filled :class:`S3CompatForm` to a :class:`ConnectionEntry`.

        Keeps infra types out of the UI layer.
        """
        return entry_from_s3_form(form)

    def _persist_credentials(
        self, entry: ConnectionEntry, *, service: str | None = None
    ) -> ConnectionEntry:
        if self._keychain is None or entry.kind != "s3-compatible":
            return entry
        service = service or app_keychain_service(entry.name)
        self._keychain.set(service, "access_key_id", entry.access_key_id or "")
        self._keychain.set(service, "secret_access_key", entry.secret_access_key or "")
        if entry.session_token:
            self._keychain.set(service, "session_token", entry.session_token)
        else:
            self._keychain.delete(service, "session_token")
        return replace(
            entry,
            credentials=f"keychain:{service}",
            access_key_id=None,
            secret_access_key=None,
            session_token=None,
        )

    def _read_keychain_service(
        self, service: str
    ) -> tuple[str | None, str | None, str | None] | None:
        if self._keychain is None:
            return None
        return (
            self._keychain.get(service, "access_key_id"),
            self._keychain.get(service, "secret_access_key"),
            self._keychain.get(service, "session_token"),
        )

    def _read_keychain_credentials(
        self, entry: ConnectionEntry | None
    ) -> tuple[str | None, str | None, str | None] | None:
        if (
            self._keychain is None
            or entry is None
            or not (entry.credentials or "").startswith("keychain:")
        ):
            return None
        service = (entry.credentials or "").removeprefix("keychain:")
        return (
            self._keychain.get(service, "access_key_id"),
            self._keychain.get(service, "secret_access_key"),
            self._keychain.get(service, "session_token"),
        )

    def _restore_keychain_service(
        self, service: str | None, secrets: tuple[str | None, str | None, str | None] | None
    ) -> list[Exception]:
        errors: list[Exception] = []
        if self._keychain is None or service is None:
            return errors
        if secrets is None:
            return errors
        for key, value in zip(
            ("access_key_id", "secret_access_key", "session_token"),
            secrets,
            strict=True,
        ):
            try:
                if value is None:
                    self._keychain.delete(service, key)
                else:
                    self._keychain.set(service, key, value)
            except Exception as exc:
                errors.append(exc)
        return errors

    def _discard_keychain_service(self, service: str) -> list[Exception]:
        errors: list[Exception] = []
        if self._keychain is None:
            return errors
        for key in ("access_key_id", "secret_access_key", "session_token"):
            try:
                self._keychain.delete(service, key)
            except Exception as exc:
                errors.append(exc)
        return errors

    @staticmethod
    def _next_revision_service(name: str, old_entry: ConnectionEntry | None) -> str:
        active = S3ConnectionsVM._service_from_entry(old_entry)
        slot = 1 if active == app_keychain_revision_service(name, 0) else 0
        return app_keychain_revision_service(name, slot)

    @staticmethod
    def _service_from_entry(entry: ConnectionEntry | None) -> str | None:
        if entry is None or not (entry.credentials or "").startswith("keychain:"):
            return None
        return (entry.credentials or "").removeprefix("keychain:")

    def _delete_keychain_credentials(self, entry: ConnectionEntry | None) -> None:
        if self._keychain is None or entry is None:
            return
        spec = entry.credentials or ""
        if not spec.startswith("keychain:"):
            return
        service = spec.removeprefix("keychain:")
        if self._service_is_still_referenced(service):
            return
        for key in ("access_key_id", "secret_access_key", "session_token"):
            self._keychain.delete(service, key)

    def _service_is_still_referenced(self, service: str) -> bool:
        credential_ref = f"keychain:{service}"
        return any(
            entry.credentials == credential_ref
            for entry in self._config_store.load().connections.values()
        )

    def _ensure_writable(self) -> None:
        if self._config_store.read_only:
            raise ConfigError("configuration is read-only; connection changes are unavailable")


def _rollback_failure(
    operation: str,
    original: BaseException,
    rollback_errors: list[Exception],
) -> RuntimeError:
    details = "; ".join(f"{type(error).__name__}: {error}" for error in rollback_errors)
    return RuntimeError(f"{operation} failed ({original}); rollback incomplete: {details}")


__all__ = ["S3ConnectionsVM", "entry_from_s3_form"]

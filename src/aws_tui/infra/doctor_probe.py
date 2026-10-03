"""Explicit, bounded read-only service probes using the standard SDK chain.

Private provider hooks are confined here and exercised at botocore 1.40.61.
The SDK still chooses and resolves credentials; these adapters only prohibit
processes, MFA and SSO token rotation, and bound/close its HTTP clients.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any

from botocore.config import Config as BotoConfig
from botocore.credentials import (
    AssumeRoleProvider,
    CanonicalNameCredentialSourcer,
    ContainerProvider,
    EnvProvider,
    InstanceMetadataProvider,
    ProcessProvider,
    ProfileProviderBuilder,
    SSOProvider,
    create_credential_resolver,
)
from botocore.exceptions import (
    ClientError,
    ConfigParseError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    InvalidConfigError,
    NoCredentialsError,
    PartialCredentialsError,
    ProfileNotFound,
    ReadTimeoutError,
    SSLError,
    UnauthorizedSSOTokenError,
)
from botocore.httpsession import URLLib3Session
from botocore.session import Session
from botocore.tokens import FrozenAuthToken, SSOTokenProvider
from botocore.utils import (
    ContainerMetadataFetcher,
    InstanceMetadataFetcher,
    JSONFileCache,
    get_environ_proxies,
)

from aws_tui.infra.aws_session import _parse_iso8601
from aws_tui.infra.config_store import ConfigError, ConfigStore
from aws_tui.infra.connection_resolver import Connection, ConnectionResolver
from aws_tui.infra.doctor import DoctorCheck, DoctorPaths, _has_keys, _sso_result, doctor_paths

_PROBE_CONFIG = BotoConfig(
    connect_timeout=5,
    read_timeout=5,
    retries={"total_max_attempts": 1, "mode": "standard"},
)


class _ProbeStop(Exception):
    def __init__(self, result: str, next_step: str) -> None:
        self.result = result
        self.next_step = next_step


class _ReadOnlyTokenCache(JSONFileCache):  # type: ignore[misc]
    def __setitem__(self, cache_key: str, value: Any) -> None:
        raise RuntimeError("Probe token caches are read-only")


class _ReadOnlySSOTokenProvider(SSOTokenProvider):  # type: ignore[misc]
    def _refresher(self) -> FrozenAuthToken:
        token = self._token_loader(
            self._sso_config["sso_start_url"], session_name=self._sso_config["session_name"]
        )
        return FrozenAuthToken(token["accessToken"], expiration=_parse_iso8601(token["expiresAt"]))


class _ReadOnlySSOProvider(SSOProvider):  # type: ignore[misc]
    def __init__(
        self, *args: Any, connection: Connection, paths: DoctorPaths, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._connection = connection
        self._paths = paths

    def load(self) -> Any:
        config = self._load_sso_config()
        if config:
            result = _sso_result(
                self._connection,
                self._profile_name,
                config.get("sso_session") or config["sso_start_url"],
                self._paths,
            )
            if result != "ok":
                raise _ProbeStop(
                    result,
                    "Run aws sso login for the affected profile, or repair its SSO cache, then rerun doctor.",
                )
        return super().load()


class _ReadOnlyProcessProvider(ProcessProvider):  # type: ignore[misc]
    def load(self) -> None:
        if self._credential_process is not None:
            raise _ProbeStop(
                "skipped",
                "This source requires credential_process. Run its authentication tool separately, then use a source with local credentials.",
            )


class _ReadOnlyProfileBuilder(ProfileProviderBuilder):  # type: ignore[misc]
    def __init__(
        self, *args: Any, connection: Connection, paths: DoctorPaths, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._connection = connection
        self._paths = paths

    def _create_sso_provider(self, profile_name: str) -> SSOProvider:
        return _ReadOnlySSOProvider(
            load_config=lambda: self._session.full_config,
            client_creator=self._session.create_client,
            profile_name=profile_name,
            cache=self._cache,
            token_cache=self._sso_token_cache,
            token_provider=_ReadOnlySSOTokenProvider(
                self._session,
                cache=self._sso_token_cache,
                profile_name=profile_name,
            ),
            connection=self._connection,
            paths=self._paths,
        )

    def _create_process_provider(self, profile_name: str) -> ProcessProvider:
        return _ReadOnlyProcessProvider(
            profile_name=profile_name,
            load_config=lambda: self._session.full_config,
        )


class _ReadOnlyAssumeRoleProvider(AssumeRoleProvider):  # type: ignore[misc]
    def _load_creds_via_assume_role(self, profile_name: str) -> Any:
        if self._get_role_config(profile_name).get("mfa_serial") is not None:
            raise _ProbeStop(
                "skipped",
                "This source requires interactive MFA. Authenticate separately, then probe a source with local credentials.",
            )
        return super()._load_creds_via_assume_role(profile_name)


class _BoundedContainerFetcher(ContainerMetadataFetcher):  # type: ignore[misc]
    TIMEOUT_SECONDS = 5
    RETRY_ATTEMPTS = 1


class _ProbeHTTP(URLLib3Session):  # type: ignore[misc]
    def send(self, request: Any) -> Any:
        # SDK metadata fetchers swallow transport failures and can fall back to
        # further requests. Stop before that, retaining an actionable result.
        try:
            return super().send(request)
        except (ConnectTimeoutError, ReadTimeoutError, TimeoutError):
            raise _ProbeStop(
                "timed_out", "Check credential-source network connectivity, then rerun doctor."
            ) from None
        except (EndpointConnectionError, ConnectionClosedError, SSLError, ConnectionError):
            raise _ProbeStop(
                "unreachable",
                "Check credential-source network connectivity and TLS settings, then rerun doctor.",
            ) from None


class _ProbeSession(Session):  # type: ignore[misc]
    def __init__(self, cleanup: ExitStack) -> None:
        super().__init__()
        self._cleanup = cleanup
        self.set_default_client_config(_PROBE_CONFIG)

    def create_client(self, service_name: str, **kwargs: Any) -> Any:
        config = kwargs.pop("config", None)
        # Force these bounds even when SSO supplies an unsigned client config.
        kwargs["config"] = config.merge(_PROBE_CONFIG) if config else _PROBE_CONFIG
        client = super().create_client(service_name, **kwargs)
        self._cleanup.callback(client.close)
        return client


def _configure_credentials(
    session: _ProbeSession,
    connection: Connection,
    paths: DoctorPaths,
    cleanup: ExitStack,
) -> None:
    session.set_config_variable("config_file", str(paths.aws_config_file))
    session.set_config_variable("credentials_file", str(paths.aws_credentials_file))
    session.set_config_variable("region", connection.region)
    session.set_config_variable("metadata_service_timeout", 5)
    session.set_config_variable("metadata_service_num_attempts", 1)
    if connection.profile is not None:
        session.set_config_variable("profile", connection.profile)
    cache: dict[str, Any] = {}
    token_cache = _ReadOnlyTokenCache(str(paths.sso_cache_dir))
    builder = _ReadOnlyProfileBuilder(
        session,
        cache=cache,
        region_name=connection.region,
        sso_token_cache=token_cache,
        connection=connection,
        paths=paths,
    )
    resolver = create_credential_resolver(session, cache=cache, region_name=connection.region)
    cleanup.callback(resolver.get_provider("container-role")._fetcher._session.close)
    cleanup.callback(resolver.get_provider("iam-role")._role_fetcher._session.close)
    # The standard chain's metadata providers are also shared with role
    # credential_source resolution. Replace both references together.
    container_http = _ProbeHTTP(timeout=(5, 5))
    cleanup.callback(container_http.close)
    container = ContainerProvider(
        fetcher=_BoundedContainerFetcher(session=container_http, sleep=lambda _: None)
    )
    metadata = InstanceMetadataFetcher(
        timeout=5,
        num_attempts=1,
        user_agent=session.user_agent(),
        # Reuse the SDK's resolved endpoint mode, IMDSv1 policy and refresh
        # window rather than reconstructing its configuration semantics.
        config=resolver.get_provider("iam-role")._role_fetcher._config,
    )
    cleanup.callback(metadata._session.close)
    metadata._session = _ProbeHTTP(timeout=(5, 5), proxies=get_environ_proxies(metadata._base_url))
    cleanup.callback(metadata._session.close)
    instance = InstanceMetadataProvider(iam_role_fetcher=metadata)
    profile = session.get_config_variable("profile") or "default"
    role = _ReadOnlyAssumeRoleProvider(
        load_config=lambda: session.full_config,
        client_creator=session.create_client,
        cache=cache,
        profile_name=profile,
        credential_sourcer=CanonicalNameCredentialSourcer([EnvProvider(), container, instance]),
        profile_provider_builder=builder,
    )
    replacements = [
        role,
        container,
        instance,
        *builder.providers(
            profile_name=profile,
            disable_env_vars=session.instance_variables().get("profile") is not None,
        ),
    ]
    for provider in replacements:
        # Public resolver methods keep the SDK's original precedence intact.
        resolver.insert_after(provider.METHOD, provider)
        resolver.remove(provider.METHOD)
    session.register_component("credential_provider", resolver)


def _check(result: str, context: dict[str, str], next_step: str) -> DoctorCheck:
    return DoctorCheck("probe", result, context, next_step, result not in {"ok", "skipped"})


def probe_source(name: str, paths: DoctorPaths | None = None) -> DoctorCheck:
    """Probe exactly one named source, discarding every service response."""
    paths = paths if paths is not None else doctor_paths()
    context: dict[str, str] = {}
    try:
        store = ConfigStore(path=paths.config_file, read_only=True)
        resolver = ConnectionResolver(
            config_store=store,
            aws_config_path=paths.aws_config_file,
            aws_credentials_path=paths.aws_credentials_file,
        )
        discovery = resolver.discover()
        selected = next(
            (
                (index, conn)
                for index, conn in enumerate(discovery.connections, 1)
                if conn.name == name
            ),
            None,
        )
        if selected is None:
            return _check(
                "unknown_source",
                {},
                "Choose an exact configured connection or AWS profile name, then rerun doctor --probe NAME.",
            )
        index, connection = selected
        context = {"source": str(index), "kind": connection.kind, "origin": connection.source}
        if discovery.invalid_sources:
            return _check(
                "invalid_config",
                context,
                "Repair unreadable or invalid app/AWS configuration files, then rerun doctor.",
            )
        if connection.kind == "s3-compatible":
            # Install a keychain backend only after exact local selection.
            from aws_tui.infra.keychain import Keyring

            connection = ConnectionResolver(
                config_store=store,
                keychain=Keyring(),
                aws_config_path=paths.aws_config_file,
                aws_credentials_path=paths.aws_credentials_file,
            ).resolve_selected(name)
            if not _has_keys(connection.access_key_id, connection.secret_access_key):
                return _check(
                    "missing_credentials",
                    context,
                    "Add credentials for the selected source in Settings or its configured credential store.",
                )
        with ExitStack() as cleanup:
            session = _ProbeSession(cleanup)
            session.set_config_variable("config_file", str(paths.aws_config_file))
            session.set_config_variable("credentials_file", str(paths.aws_credentials_file))
            if connection.kind == "s3-compatible":
                config = _PROBE_CONFIG.merge(
                    BotoConfig(
                        s3={"addressing_style": "path" if connection.force_path_style else "auto"}
                    )
                )
                client = session.create_client(
                    "s3",
                    region_name=connection.region,
                    endpoint_url=connection.endpoint_url,
                    verify=connection.verify_tls,
                    config=config,
                    aws_access_key_id=connection.access_key_id,
                    aws_secret_access_key=connection.secret_access_key,
                    aws_session_token=connection.session_token,
                )
                client.list_buckets()
            else:
                _configure_credentials(session, connection, paths, cleanup)
                credentials = session.get_credentials()
                if credentials is None:
                    raise NoCredentialsError()
                # Resolve deferred credentials before creating the main client;
                # this also ensures skipped MFA/process profiles create none.
                credentials.get_frozen_credentials()
                client = session.create_client("sts", region_name=connection.region)
                client.get_caller_identity()
        return _check(
            "ok", context, "The read-only operation succeeded. No response details are retained."
        )
    except _ProbeStop as exc:
        return _check(exc.result, context, exc.next_step)
    except (NoCredentialsError, PartialCredentialsError):
        return _check(
            "missing_credentials",
            context,
            "Configure credentials for the selected source or authenticate separately, then rerun doctor.",
        )
    except UnauthorizedSSOTokenError:
        return _check(
            "expired_sso", context, "Run aws sso login for the affected profile, then rerun doctor."
        )
    except (ConfigError, ConfigParseError, InvalidConfigError, ProfileNotFound):
        return _check(
            "invalid_config",
            context,
            "Repair configuration for the selected source, then rerun doctor.",
        )
    except (ConnectTimeoutError, ReadTimeoutError, TimeoutError):
        return _check(
            "timed_out",
            context,
            "Check network connectivity and the source endpoint, then rerun doctor.",
        )
    except (EndpointConnectionError, ConnectionClosedError, SSLError, ConnectionError):
        return _check(
            "unreachable",
            context,
            "Check the source endpoint, TLS settings and network connectivity, then rerun doctor.",
        )
    except ClientError:
        return _check(
            "denied",
            context,
            "Check credentials and permission for the selected read-only operation, then rerun doctor.",
        )
    except Exception:
        # Vendor/keychain/SDK errors can contain tokens, SQL and response data.
        return _check(
            "unverified",
            context,
            "The probe could not verify this source. Check its configuration and authenticate separately before retrying.",
        )

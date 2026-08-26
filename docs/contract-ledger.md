# 1. Consumed Contract Ledger

This ledger records external contracts that aws-tui consumes and the pinned
versions checked during maintenance. It is not a replacement for tests; it is a
durable map of the real upstream surfaces that mocks and adapters must track.

## 1.1. 2026-07-01 maintenance pass

| Integration point | Pinned version / ref | Consumed contract | Verification method |
|---|---:|---|---|
| `aioboto3.Session().client("s3", ...)` via `S3FS` and MinIO seed scripts | `aioboto3==15.5.0`, `aiobotocore==2.25.1` from `uv.lock` | Async S3 client creation accepts botocore `Config`, `endpoint_url`, and `verify`. Production consumes `ListBuckets`, `ListObjectsV2`, `HeadObject`, `GetObject`, `PutObject`, `CopyObject`, `DeleteObject`, `DeleteObjects`, multipart upload, and abort operations. | Source trace through `src/aws_tui/domain/s3_fs.py`, `src/aws_tui/services/s3/service.py`, and `scripts/test-services/s3/seed.py`; focused unit coverage includes partial batch-delete failures and TLS propagation; the Docker-backed tier exercises nine real MinIO flows. |
| Boto credential/profile and S3-compatible options | `boto3==1.40.61`, `botocore==1.40.61` from `uv.lock` | Production creates clients through aioboto3 and configures botocore profiles, regions, endpoint URLs, path-style addressing, TLS verification, six total adaptive attempts, 10-second connect timeouts, 60-second read timeouts, and temporary session tokens. Startup follows botocore's `AWS_DEFAULT_PROFILE` then `AWS_PROFILE` precedence and uses `AWS_DEFAULT_REGION` after connection/profile regions. `AWS_CONFIG_FILE` and `AWS_SHARED_CREDENTIALS_FILE` override expanded shared-file paths. Direct `boto3` consumption is transitive/test tooling rather than a production import. | Source trace through `src/aws_tui/infra/aws_session.py`, `src/aws_tui/infra/connection_resolver.py`, and S3 provider construction; config parsing rejects invalid string and boolean shapes before values reach botocore. Environment precedence and path overrides have integration coverage. |
| EMR Serverless client and log-discovery contracts | `botocore==1.40.61` service model, EMR Serverless API `2021-07-13`, and AWS EMR Serverless logging documentation | Operations `ListApplications`, `ListJobRuns`, `GetJobRun`, and `StartJobRun`; consumed request fields `applicationId`, `jobRunId`, list-valued `states` (up to eight values), and `nextToken`; response fields `applications`, `jobRuns`, `jobRun`, and pagination `nextToken`; `ApplicationState` and `JobRunState` enums. S3 log discovery accepts retry prefixes (`attempts/<n>`), Spark driver and `SPARK_EXECUTOR/<id>` paths, Hive `HIVE_DRIVER` and `TEZ_TASK/<id>` paths, and active or rotated stdout/stderr gzip objects. The SDK supplies the modeled idempotency token for `StartJobRun`. | Installed botocore model introspection, AWS's public S3 logging layout, and source trace through `src/aws_tui/domain/emr_serverless.py`, `src/aws_tui/domain/emr_logs.py`, and `src/aws_tui/services/emr_serverless/service.py`; request-shape and log-layout tests cover mapping, pagination, retries, worker identity, rotation, detail/log panes, clone-from-run, and bounded streaming. |
| AWS Glue read contracts | `botocore==1.40.61` service model, Glue API `2017-03-31` | Operations `GetDatabases`, `GetTables`, `GetTable`, `GetPartitions`, `GetColumnStatisticsForTable`, `GetJobs`, `GetJobRuns`, `GetCrawlers`, `GetCrawler`, `GetCrawlerMetrics`, and `GetTags`, plus STS `GetCallerIdentity` for crawler tag ARNs; Glue pagination tokens and optional response fields are normalized into immutable domain values. | Installed botocore model introspection against the locked environment; source trace through `src/aws_tui/domain/glue.py` and `src/aws_tui/services/glue/service.py`; mapper, paginator, error, VM, integration, demo, and snapshot tests cover the shipped read-only views. |
| Single-context AWS service source identity | Internal `ServiceSourceContext` / `RootVM` contract | EMR, Glue, and Athena use a bordered selector for the active AWS connection's name, optional distinct profile, and region; remembered selections are scoped by service id, connection name, and region. `Shift+S` rebuilds the active service under the next supported AWS connection, while `Shift+A` cycles the EMR application. Service-level `AccessDenied` is not a connection-reachability signal. `OpenS3LocationRequest` carries the exact connection name and region; the app rejects missing identities or region drift instead of substituting another profile. | `tests/unit/vm/test_service_source_vm.py`, message tests, EMR/Glue/Athena integration coverage, source-header snapshots across shipped themes, and E2E source-switch/S3-handoff journeys verify identity rendering, scope isolation, key routing, remount behavior, and cross-service identity preservation. |
| Textual app/runtime API | `textual==8.2.8` from `uv.lock` and the exact runtime requirement | App launch, bindings, modal/screen stack, widgets, pilot tests, and snapshot rendering. Compatibility code is isolated to Textual 8.2.8 behavior: priority binding replacement touches `_bindings.key_to_bindings`, app crash recovery overrides `_handle_exception`, and the content-host mount boundary interprets lifecycle tracebacks rooted in `_pre_process`. | Full integration/snapshot tests exercise startup, priority bindings, lifecycle recovery, modal controls, focus cycling, settings, themes, and demo mode. The installed 8.2.8 source was inspected; supporting another Textual release requires an explicit adapter re-audit before broadening the pin. |
| VMx view-model helpers | `vmx==3.1.0` from `uv.lock` | VM lifecycle, observable state, message protocol, command disposal, and form/composite/dialog/pagination helper contracts referenced by the VM layer and docs. | Import-level compatibility smoke plus focused VM tests against the locked environment; this branch also records the VMx 3.1.0 adoption audit and keeps larger adapter replacements deferred behind that report. |
| MinIO local S3 harness | Docker image `minio/minio:RELEASE.2025-09-07T16-13-09Z@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`; `testcontainers==4.15.0` from `uv.lock` | S3-compatible endpoint, readiness probe, seeded buckets/objects, path-style config, local credentials, and host port exposure. | Manual trace of `scripts/test-services/s3/docker-compose.yml`, `seed.py`, and `config-snippet.toml`; snippet updated to match current Settings/default-connection flows, and Compose ports now bind to `127.0.0.1` only. The MinIO integration fixture now passes the same explicit image ref to `testcontainers.minio.MinioContainer(image=...)`, avoiding the older package default image. The Python `minio==7.2.20` package is consumed by the separate `testcontainers[minio]` integration fixture. |
| SQL parsing and Iceberg identifier extraction | `sqlglot==30.14.0` from `uv.lock` | Public `parse` and `parse_one` entry points with the Athena dialect, AST traversal, statement classification, and quoted table-identifier extraction. | Source trace through `src/aws_tui/domain/sql_policy.py` and `src/aws_tui/domain/iceberg.py`; SQL-policy and Iceberg suites cover accepted read-only forms, rejected mutations, complexity bounds, and identifier quoting. |
| Config, path, and secret-storage helpers | `keyring==25.7.0`, `tomli-w==1.2.0`, `platformdirs==4.11.0` from `uv.lock` | OS keychain get/set/delete behavior, TOML serialization for `config.toml`, and platform-native config/cache path resolution with legacy fallback directories. Production Settings saves store only `keychain:<service>` references in TOML; key material stays in the OS-backed keyring. Atomic edits alternate between two bounded revision slots and publish the newly written service reference before deleting the superseded secrets. Legacy static entries remain readable and migrate when edited; no first-run credential form is currently shipped. | Source trace through `src/aws_tui/infra/keychain.py`, `src/aws_tui/infra/config_store.py`, `src/aws_tui/infra/paths.py`, and `src/aws_tui/vm/settings/s3_connections_vm.py`; unit coverage exercises keyring CRUD, rollback, resolver integration, strict TOML parsing, private permissions, and platformdirs behavior. |
| Python package build backend | `hatchling==1.31.0` from `uv.lock`; `build-system.requires` constrained to `hatchling>=1.31.0,<2` | PEP 517 wheel/sdist build behavior, package metadata, and version-file inclusion. | Build backend installed in the locked dev environment; CI and release run `uv build --no-build-isolation` so artifacts do not resolve an untracked hatchling version at build time. |
| GitHub Actions CI/release/publish workflow | Workflow refs at the 2026-07-01 pass: `actions/checkout` v4, `astral-sh/setup-uv` v8.3.2, `actions/upload-artifact` v7, `actions/download-artifact` v7, `pypa/gh-action-pypi-publish` v1, `peter-evans/create-pull-request` v6, `actions/configure-pages` v5, `actions/upload-pages-artifact` v3, and `actions/deploy-pages` v5.0.0; `uv==0.11.19` | Checkout, pinned uv installation, CI/build artifact upload, release artifact download, Sigstore/OIDC PyPI publishing, TestPyPI rehearsal, GitHub Release asset upload, Homebrew tap PR creation, and Pages build/deploy. | Historical workflow source review; superseded current pins are recorded in §1.4. |
| Pre-commit hooks | Immutable refs resolved on 2026-08-02: `pre-commit-hooks@3e8a8703264a2f4a69428a0aa4dcb512790b2c8c` (`v6.0.0`), `ruff-pre-commit@39d9ac5938dadb73df0564a45f163e25ff9fa6e2` (`v0.16.1`), `taplo-pre-commit@ade0f95ddcf661c697d4670d2cfcbe95d0048a0a` (`v0.9.3` peeled commit); local `mypy` via locked env | Formatting, linting, type checking, TOML validation, trailing whitespace, EOF, and large-file hygiene. | `git ls-remote` verification of each tag before pinning; local equivalent checks run through the locked `uv 0.11.19` environment, and CI uses setup-uv v9.0.0 with `uv sync --frozen`. |

## 1.2. 2026-07-26 Athena service pass

| Integration point | Pinned version / ref | Consumed contract | Verification method |
|---|---:|---|---|
| Amazon Athena client | `botocore==1.40.61` service model, Athena API `2017-05-18` | The `AthenaClient` uses exactly the 15 boto operation methods listed below, including `get_prepared_statement` for prepared-query detail. It always supplies `WorkGroup`, `QueryExecutionContext` (catalog/database), and a client request token to `start_query_execution`. `AthenaClient.start_query(...)` accepts an optional `output_location` and adds `ResultConfiguration.OutputLocation` only when its caller supplies that value. The shipped `AthenaQueryVM` query runner does not supply `output_location`; it relies on the selected workgroup's enforced customer S3 or Athena managed-results configuration. List/result continuations are opaque `NextToken` values. | Source trace through `src/aws_tui/domain/athena.py`, `src/aws_tui/vm/athena/query_vm.py`, `src/aws_tui/domain/query.py`, and `src/aws_tui/domain/sql_policy.py`; unit tests cover every operation, both start-query request shapes, response mapping, pagination, output-configuration failures, error normalization, and result-header handling. Documentation tests compare this exact ledger with the minimum IAM action set and pin the facade-versus-query-runner distinction. |
| Athena page and result handoff | Internal `AthenaPageVM` / `OpenS3LocationRequest` contract | Query, History, Results, and Saved are connection- and region-scoped. The client accepts one read-only parsed statement, tracks only app-started active executions for stopping, and loads results a page at a time. A result handoff reloads the selected execution and requires a succeeded execution, matching active connection/region/context, and a valid `s3://` output location before handing the exact identity to S3. Full SQL, raw boto responses, and result values do not enter logs or crash dumps. | `tests/unit/domain/test_athena.py`, `tests/unit/domain/test_sql_policy.py`, Athena VM tests, `tests/integration/test_athena_page.py`, `tests/integration/test_athena_s3_handoff.py`, demo tests, and Athena snapshots. |

Exact boto operation ledger (15):

```text
list_work_groups
get_work_group
list_data_catalogs
list_databases
list_table_metadata
list_query_executions
get_query_execution
get_query_runtime_statistics
start_query_execution
stop_query_execution
get_query_results
list_named_queries
batch_get_named_query
list_prepared_statements
get_prepared_statement
```

## 1.3. 2026-07-28 Glue, Athena, and Iceberg integration pass

| Integration point | Pinned version / ref | Consumed contract | Verification method |
|---|---:|---|---|
| AWS Glue client | `botocore==1.40.61` service model, Glue API `2017-03-31` | `GlueClient` uses exactly the 11 Glue boto operations below. `get_caller_identity` is the sole STS call and supplies the account ID used to construct a crawler ARN before `get_tags`. All pages preserve opaque continuation tokens and map raw SDK responses into immutable domain records. | `tests/unit/domain/test_glue.py`, Glue VM/service/integration tests, and documentation source comparison cover operations, mapping, pagination, tags, errors, and exact record shapes. |
| Shared catalog/query records | Internal `domain.data_catalog` and `domain.query` contracts | `TableRef(catalog_name, database_name, table_name, connection_name, region)` is the complete cross-service table identity. `QueryContext(connection_name, region, workgroup, catalog, database)` is the Athena execution identity. `TableFormat` classifies Iceberg, Hive, Hudi, Delta, and Other from normalized Glue metadata. | Domain tests cover exact format markers, malformed values, quoting, scope equality, and foreign-context rejection. Navigation integration tests exercise exact and missing connection/region cases. |
| Iceberg metadata inspection | Internal `IcebergInspector` over `AthenaQueryRunner`; Apache Iceberg metadata tables exposed by Athena | `IcebergInspector` reads `snapshots`, `history`, `manifests`, `files`, `partitions`, and `refs` with hard row limits of 100/100/500/1000/500/100. The five fixed-schema views use explicit projections and `ORDER BY`; `partitions` intentionally uses `SELECT *` with `LIMIT 500` without `ORDER BY` because its metadata schema is dynamic. It uses the same public Athena operations already listed in §1.2; no private SDK API or extra boto operation is introduced. `GlueService` revalidates a remembered enabled workgroup in the same connection/region before constructing the `QueryContext`. | Iceberg domain, runner, Glue service/VM/UI, integration, demo, snapshot, and E2E tests cover shape validation, pagination, partial failure, retry, cancellation, profile isolation, time travel, and result artifacts. |
| Cross-service messages | Internal immutable VM message contract | `OpenAthenaTableRequest(TableRef, snapshot_id?)`, `CopyTableReferenceRequest(TableRef)`, `OpenGlueTableRequest(TableRef)`, and `OpenS3LocationRequest(connection_name, region, uri, preferred_pane, reveal_object)` are exact runtime-validated envelopes. `CopyTableReferenceRequest` stores the canonical identifier and source identity in the authoritative typed in-app clipboard; OS clipboard delivery is best effort. The composition root serializes table handoffs, rejects missing/changed identities, and restores prior state when a mutation fails or is superseded. | Message unit tests plus Glue↔Athena and Athena/Glue→S3 integration tests cover runtime validation, typed clipboard delivery, bounded discovery, rollback, cancellation, shutdown, and stale request suppression. |
| Public actions | Internal `ActionRegistry`, `KeymapStore`, and command palette contract | The complete source-derived ledger below includes Glue/Athena view commands, selector commands, execution controls, and cross-service actions. Glue copy requires a selected Catalog table. Athena insert requires a typed clipboard value from the active Athena connection and region; a missing or foreign-source value leaves the editor and clipboard unchanged. Athena remains a single-context service, and generated SQL is quoted and bounded without auto-execution. | Keymap fidelity, command-palette, action-routing, Glue/Athena navigation, and E2E tests pin availability, selector routing, source guards, and no-auto-run behavior. |
| Demo providers and journeys | Internal in-memory Glue/Athena/S3 contracts | `demo-dev`, `demo-prod`, and `demo-shared` expose disjoint Iceberg records and resolvable metadata/data/result S3 URIs. In-memory Athena validates exact contexts, request token bounds, canonical S3 output locations, opaque operation/context-bound pagination tokens, collection fingerprints, and UTF-8 before mutation. Result CSV bytes match the displayed `ResultPage`. | Demo provider boundary tests, three-profile content/snapshot guards, navigation tests, and Journey 9 verify Glue → Athena → explicit execution → S3 without network access. |

Exact Glue boto operation ledger (11):

```text
get_databases
get_tables
get_table
get_partitions
get_column_statistics_for_table
get_jobs
get_job_runs
get_crawlers
get_crawler
get_crawler_metrics
get_tags
```

Exact STS boto operation ledger (1):

```text
get_caller_identity
```

Public Glue and Athena action ledger:

```text
athena.cancel
athena.choose_catalog
athena.choose_database
athena.choose_workgroup
athena.execute
athena.history
athena.insert_table_ref
athena.load_more
athena.open_in_glue
athena.open_result_location
athena.query
athena.results
athena.saved
glue.catalog
glue.choose_crawler_state
glue.choose_run_state
glue.copy_table_ref
glue.crawlers
glue.jobs
glue.open_s3_location
glue.query_in_athena
glue.time_travel_in_athena
```

Public cross-service message ledger:

```text
OpenS3LocationRequest
OpenAthenaTableRequest
CopyTableReferenceRequest
OpenGlueTableRequest
```

## 1.4. 2026-08-02 maintenance pass

| Integration point | Pinned version / ref | Consumed contract | Verification method |
|---|---:|---|---|
| Python runtime dependency ownership | `vmx==3.1.0`, `textual==8.2.8`, `reactivex==4.1.0`, and `rich==15.0.0` from `uv.lock` | Every production import is directly declared: VMx provides lifecycle/composition primitives, Textual owns the TUI runtime, Reactivex owns observable protocols/subjects, and Rich provides markup escaping. `boto3` is no longer declared directly because production creates clients through `aioboto3`; Botocore remains direct for configuration, models, and exceptions. VMx's documented deep module paths remain supported public imports; root-facade imports are preferred for newly touched code, without a mechanical 46-module churn. | Built wheel metadata inspection, import inventory over `src/aws_tui`, exact installed-source inspection, isolated wheel smoke, and full unit/integration/snapshot/E2E coverage. |
| GitHub Actions CI/release/publish workflow | `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` (`v7.0.1`), `astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9` (`v9.0.0`), `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` (`v7.0.1`), `actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` (`v8.0.1`), `pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33` (`v1.14.2` peeled commit), `peter-evans/create-pull-request@5f6978faf089d4d20b00c7766989d076bb2fc7f1` (`v8.1.1`), `actions/configure-pages@45bfe0192ca1faeb007ade9deae92b16b8254a0d` (`v6.0.0`), `actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9` (`v5.0.0`), and `actions/deploy-pages@cd2ce8fcbc39b97be8ca5fce6e763baed58fa128` (`v5.0.0`) | Current major-version contracts for checkout, uv installation/cache, artifact exchange, trusted PyPI publishing, Homebrew PR creation, and Pages deployment. CI's stable `ci gate` now includes an unconditional documentation-contract job. | GitHub release API plus peeled `git ls-remote` tag verification on 2026-08-02; workflow guard tests, YAML parsing, local docs build, package build, and installed-wheel smoke. |

## 1.5. 2026-08-25 maintenance pass

| Integration point | Pinned version / ref | Consumed contract | Verification method |
|---|---:|---|---|
| Python runtime and AWS SDK graph | `aioboto3==15.5.0`, `aiobotocore==2.25.1`, `botocore==1.40.61`, `textual==8.2.8`, `vmx==3.23.0`, `reactivex==4.1.0`, `rich==15.0.0`, `keyring==25.7.0`, `tomli-w==1.2.0`, `platformdirs==4.11.4`, `sqlglot==30.17.0`, `anyio==4.14.2`, and `aiofiles==25.1.0` from `uv.lock` | The application uses only each package's public facade or documented module path. VMx owns command admission/cancellation, modal focus restoration, immutable form construction, observable state, component lifecycle, filtering, and paging. Textual remains exactly pinned because the binding and mount-recovery adapters are audited against 8.2.8. AWS operations and request members are validated against the locked Botocore models. | Full unit, integration, snapshot, and E2E tiers; minimum-direct-dependency tests; import/layer checks; VMx compatibility regressions for command cancellation, retired pager generations, form validation, and modal restoration; source-derived Botocore operation and input-member tests. |
| VMx 3.23 compatibility and specialization | `vmx==3.23.0` from `uv.lock`; runtime requirement `vmx>=3.23.0,<4.0.0` | `FocusCoordinatorVM` delegates modal save/restore behavior to public `DiscriminatorVM.modal_open()` / `modal_close()`. `S3ConnectionFormVM` supplies complete field and model validation through `FormVMBuilder` at construction time. Athena drains public `AsyncRelayCommand.is_executing` admission state after cancellation and tracks the provider task behind nested command execution so shutdown waits for cancellation-resistant I/O. No VM reaches into VMx private fields. | Focus, Settings form, Athena query/results, VMx smoke, mypy, and lifecycle tests run against the locked package. The dated VMx 3.23 maintenance report records adopted and rejected candidates plus production-line metrics. |
| Packaging and developer tooling | `hatchling==1.32.0`, `testcontainers==4.15.0`, and `textual-dev==1.8.0` from `uv.lock`; `build-system.requires` constrained to `hatchling>=1.31.0,<2` | CI, release, Pages, and bootstrap sync/export with `--locked`, so a stale lock fails instead of silently installing it. Bootstrap installs all dependency groups. The Textual development CLI used by `scripts/dev.sh` is declared explicitly. Wheel and sdist members must exclude repository metadata, tests, local caches, and traversal paths. | `uv lock --check`, script/workflow guard tests, real wheel/sdist builds, `scripts.check_dist`, Twine metadata checks, isolated install smoke, and a Textual CLI invocation. |
| GitHub Actions and pre-commit toolchain | `astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d` (`v10.0.1`), `ruff-pre-commit@aab412d509121cb5f7533134b7e67f9fab59c682` (`v0.16.4`), and the other immutable action refs listed in §1.4 | Workflow jobs retain least-privilege permissions and bounded timeouts. The wiki deploy key is checked before it is written. The Homebrew checkout does not persist its cross-repository token. Artifact contents are checked before upload or publication. | Official tag/ref verification, YAML guard tests, pre-commit, shellcheck, and local workflow-equivalent package/docs commands. |

Exact S3 boto operation ledger (14):

```text
AbortMultipartUpload
CompleteMultipartUpload
CopyObject
CreateMultipartUpload
DeleteObject
DeleteObjects
GetObject
GetObjectTagging
HeadObject
ListBuckets
ListObjectsV2
PutObject
UploadPart
UploadPartCopy
```

## 1.6. Deferred contract checks

- External upstream documentation was not exhaustively re-queried for every
  library API. The concrete code paths above were checked against the locked
  dependency graph and strengthened with tests where this pass changed behavior.

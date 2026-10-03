# Read-only S3 object details design

## 1. Intent, authority and scope

Issue #250 lets a user diagnose one focused S3 object's properties without leaving the terminal. All eleven current acceptance criteria remain binding. Work is authorized by the standing unsupervised ticket goal; routine design, plan and execution decisions proceed without another approval menu. Applicable local checks replace hosted checks, Actions stays disabled, and verification never mutates live AWS resources.

This is an architectural change: it adds a typed optional provider capability, asynchronous selection ownership and a dedicated modal. It does not change the mandatory filesystem protocol or listing entries. Existing transfer, revision-token, rename/move, credential-recovery, source navigation, Quick Look and modal containment contracts remain intact. Editing properties/tags, ACL changes, storage transitions, presigned URLs and historical-version actions are excluded. Cross-reference #251 explicitly describes separate version browsing/recovery; it is not a prerequisite.

## 2. Chosen approach

Use an optional S3 details capability, a modal-lifetime viewmodel and a separate read-only inspector. Extending `FileEntry` would couple every provider/listing to S3 and invite metadata requests on the listing hot path. Extending Quick Look would couple metadata permissions to content streaming and complicate ownership. The selected approach follows existing provider capability protocols, VMx lifecycle, `OperationOwner`, deferred workers and the app's clipboard path.

Reuse the clean primary checkout on `codex/issue-250-s3-object-details`, based on freshly fetched develop `226d6a54baec7f0f29d101b02bd404a867aaa3a3`. Its complete source tree equals the already validated #239 tree. Preserve unrelated dependency PRs and workspaces.

## 3. Provider boundary and immutable result

Add `domain/s3_object_details.py` with a frozen, slotted `S3ObjectDetails` result and a runtime-checkable optional `S3ObjectDetailsProvider` protocol. Its only operation is `async read_object_details(path: PathRef) -> S3ObjectDetails`. `S3FS` implements it; `FileSystemProvider` and `FileEntry` remain unchanged. Providers without the capability are refused visibly, without a metadata read. Supporting the capability does not authorize inspection of local or connection rows.

The result carries exact bucket/key, optional content type/encoding, size, aware modification timestamp, storage class, ETag and version ID; immutable encryption-summary pairs, user-metadata pairs, tag pairs and checksum pairs; optional checksum type and separate optional-read error text. A missing scalar/section is `None`; a returned empty metadata/tag collection is an empty tuple. Preserve zero size, false bucket-key flags, empty string values and literal version ID `null`. Do not infer an omitted storage class, unencrypted state, ETag digest or successful integrity check.

Encryption summarizes the reported server-side algorithm, KMS key identifier, bucket-key flag and customer-key algorithm when returned. Never expose or invent customer key material. User metadata and tags preserve returned values exactly; deterministic ordering is for display, not mutation. Keep checksum values separate from ETag and checksum type. Capture every returned checksum algorithm field supported by the locked SDK, including CRC64NVME; do not confuse `ChecksumType` with an algorithm value.

## 4. On-demand reads, partial results and resource ownership

Resolve paths using existing S3FS fixed-bucket/prefix and bucketless rules. Reject roots and bucket-only paths before a client call, even when a configured prefix makes the resolved key nonempty. UI/VM eligibility rejects directory/prefix/parent rows before this method is invoked. Do not modify listing, stat, byte-streaming or mutation paths and do not probe each listing row.

Read `HeadObject` with `ChecksumMode="ENABLED"`. A successful response supplies the base properties and any reported checksums together. If that read is denied because of checksum access or the endpoint rejects checksum mode, attempt one ordinary HEAD without checksum mode. If the ordinary HEAD succeeds, retain its base fields and mark the checksum section unavailable with the first read's error. The ordinary HEAD failure remains a typed whole-read error. Transport/throttling failures may use the same one-fallback strategy to preserve readable metadata; authentication failures and not-found/conflict failures do not trigger credential recovery, retries outside the shared SDK policy or generic fallback loops. A successful checksum-mode HEAD with no checksum fields yields an explicit not-returned state, not a verified match.

After a successful HEAD, issue `GetObjectTagging` for the same resolved bucket/key. If HEAD returned a nonempty version ID, send that exact ID, including `null`, to tagging. A tag read failure retains the other details and marks tags unavailable, distinct from a successfully returned empty tag set. Do not claim an atomic metadata/tag snapshot for an unversioned key. Do not implement version browsing or downloading.

Use the existing S3 client configuration, including endpoint, TLS, addressing, signing, retries and timeouts. Keep every request inside the client context. Caller cancellation must propagate and close that context; do not start detached requests or mutate an S3 object. Existing typed credential, transport and ClientError mapping applies. No automatic source switch or page credential-recovery mutation is initiated by an inspector read.

## 5. Viewmodel, target identity and stale response rules

Add `vm/file_manager/s3_object_details_vm.py`. The VM owns its immutable projected state and `OperationOwner`, uses VMx construction/disposal and value-free observable notifications, and subscribes to the captured pane's property changes. No Textual, concrete S3FS or clipboard implementation is imported into the VM.

Eligibility requires a currently live, idle S3 pane, an actual selected FILE that is not a parent link, and the optional details capability. Local, missing, connection, bucket and prefix targets return clear visible refusal reasons. Capture pane identity, provider object identity, connection key, pane path, selected entry path and listing revision. A request generation changes when target eligibility/identity changes, including an away-and-back sequence. Ignore unrelated pane notifications with an unchanged target; marking a row must not cause another metadata read.

Changing the selected object while the view is open clears old values immediately, names the new target, and requests its details. A late old success or failure cannot replace the newer object's fields or error. Provider/source replacement, navigation, refresh, invalid selection or pane disposal invalidates the prior result. An invalid target clears its details and displays its reason rather than retaining the old object's data. Closing/disposal invalidates the generation before cancelling outstanding work; async shutdown durably drains owned tasks and disposes subscriptions.

The UI schedules loads only when the requested generation changes, using deferred callables. Each load receives its requested generation, checks it before I/O and again before publishing, and checks actual current target ownership at delivery. Subscribe to pane observable completion as well as changes: pane disposal completes that stream and must invalidate/close the inspector even before a held provider reply arrives. No data should be published after the VM is closed. Convert read failures into safe outcomes inside the owned operation where needed so teardown does not send raw provider exception text through cleanup logging.

Project fields into immutable label/value rows. Every required field has a nonblank value: unavailable, returned empty and failed optional read are distinct states. Format size and timestamps explicitly; metadata/tags may use lossless readable JSON. Apply `redact_text` to every error string before any display/copy path. Do not redact legitimate object values or log their contents. ETag is an opaque identifier; checksum values are reported metadata. Add an explicit checksum-verification field stating that content verification was not performed.

## 6. Read-only modal, action and copy behavior

Add `ui/widgets/s3_object_details.py` with `S3ObjectDetailsModal`, backed by the modal-lifetime VM. Use existing theme variables, modal buttons, focus containment and deferred workers. Present a literal field/value table plus a wrapping, read-only full-value viewer for the selected field, so long values remain inspectable and copyable without truncation. Use Rich `Text` or markup-disabled widgets for all user-controlled labels, keys, identities and values. Empty user values have an explicit display state while copying preserves the original empty value.

Provide Copy value and Close controls. Copy acts on the current loaded selected field and sends its complete value through an async callback to the existing app `_put_on_clipboard(value, label)` path, preserving native/terminal outcome reporting. Use a fixed safe label such as `S3 object detail`, never a user-supplied metadata key or path as the toast/log label. Ctrl+C inside this modal can invoke the same copy action; it must not quit the app or mutate the hidden pane. Escape and Close dismiss. Loading, stale/invalid targets and unavailable fields cannot copy previous object data. Clipboard failures are reported safely; never include copied values in a toast or log. Metadata/tag JSON preserves all string values and can be round-tripped by the copy test.

For unchanged selection, dismissal restores the exact focused row identity present before opening. If the pane/row has legitimately changed, restore only valid current focus and never resurrect a detached row or source. Closing during mount or a held request must remove the screen, release subscriptions and leave keyboard/command-palette navigation usable.

Register action `pane.object_details`, label `S3 object details`, default key `ctrl+o`. This key is unused by the current global default map and existing remap fixtures. Route keyboard, Commands and command palette through the same action. Resolve the actual focused widget's owning pane/row, rather than inspecting a previous logical pane selection while a connection/navigation/input control has focus. Commands and palette invoke after dismissal with the originating row restored or its captured identity revalidated. Add it to contextual pane/modal protection so shortcuts cannot dispatch into a hidden pane or open stacked inspectors. Refusals are visible for connection, bucket, prefix, local and absent targets, with no metadata calls. Preserve strict global collision checks and explain remapping the new action if a custom map already uses Ctrl+O.

## 7. Acceptance and verification map

| AC | Required evidence |
|---|---|
| 1 | Real app Pilot opens the modal from the contextual action on a focused S3 object. |
| 2 | Separate actual-app connection, bucket and prefix target tests assert a visible refusal and zero details reads; local/parent/empty targets are covered too. |
| 3 | Pilot captures exact focused-row identity and verifies it after dismissal. |
| 4 | Stubbed SDK HEAD with all requested fields, plus tagging response, verifies typed data and rendered fields. |
| 5 | Minimal HEAD/omitted tag response tests explicit unavailable labels, distinguishing zero/false/empty from missing. |
| 6 | ETag/checksum label/value tests and missing-checksum/no-verification text; no body download. |
| 7 | Denied tagging and denied checksum-mode HEAD with readable ordinary HEAD preserve other details and show unavailable sections. |
| 8 | Real PaneVM/Pilot barriers resolve old replies after selecting another object, provider/source changes, away-and-back and closure; only current object is displayed. |
| 9 | New focused snapshots at normal/narrow widths with long bracket-containing values; literal rendering and complete copy-value/metadata round-trip tests. |
| 10 | Secret-bearing provider and optional-read errors pass through redaction before displayed/copied text; stale errors are silent. |
| 11 | Recording SDK stub proves reads only; ordinary pane listing before/after inspector adds no per-row HEAD/tag calls. |

Task workers use meaningful focused RED/GREEN tests followed by one appropriate covering scope. Controller runs the complete default tests/coverage on the final clean reviewed commit, all-file hooks, docs/site/wiki parity and strict build, locked dependency audit, package/content/Twine checks and actual-wheel synthetic inspector evidence. No hosted dispatch, live AWS mutation, blind golden replacement, assertion weakening or timeout inflation. Python remains >=3.11,<3.14; coverage floor stays 70%.

## 8. Delivery and source references

All task and full-branch review findings are resolved under the existing sequential SDD process. Protected develop PR then main promotion, whole-tree parity, post-promotion local verification, safe owned cleanup, substantive issue conclusion, eleven checked ACs and board Done precede the next ticket. Preserve external dependency PRs and primary checkout.

Primary references: [object metadata](https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingMetadata.html), [HeadObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html), [GetObjectTagging](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObjectTagging.html). Current locked botocore models were checked for `ChecksumMode`, checksum response fields and tagging `VersionId`; tagging has no IfMatch parameter. Error/permission/consistency behavior follows these read APIs; ETag is not treated as universally MD5.

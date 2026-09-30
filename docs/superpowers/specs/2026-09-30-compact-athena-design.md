# Full-app compact Athena layout

## 1. Goal and evidence

Issue #233 requires an editable Athena surface in the real application at
80×24, with keyboard access and state preserved through resizing. On develop
`9f6a9b36`, the full-app probe measured an eight-row banner, an eleven-row
content host, a five-row query view and a zero-row editor. Catalog and database
pickers extended beyond the viewport. Query status was not focusable.

The first regression mounts `DemoModeApp(theme="carbon")`, switches to Athena
and checks editor height. It fails at 80×24 with `0 >= 3` and passes at 120×40.

## 2. Chosen presentation

Use a compact height mode below 34 terminal rows. Replace the decorative banner
rendering with one line retaining the application name, explicit demo status,
active service and source identity. The identity follows committed content and
connection notifications; it does not infer successful adoption from navigation
intent. This also keeps EMR source names readable when its narrow picker truncates
them.
Keep the existing service navigation and source selectors visible. Keep the
spacious banner at 120×40. Change presentation on existing widgets, without
remounting pages, resetting models or replacing the SQL editor.

For constrained query-view heights, reduce the controls and execution-detail
tracks while preserving editor space. Detail remains scrollable and keyboard
accessible. Ensure compact context-selector widths fit the content viewport,
including pagination controls when present. Preserve dropdown overlays and their
Escape behavior. Make query status a deliberate focus destination, with visible
focus styling and consistent forward/reverse Tab routing.

Execute and Cancel retain their existing command availability. Keyboard tests
reach Execute when a valid query can run and Cancel while a controlled in-memory
query is active; disabled commands must not be enabled merely to satisfy a test.

## 3. Alternatives considered

Hiding the navigation rail would reclaim width but change established service
navigation. Making the entire page scroll would retain the fixed layout but
leave the editor or execution controls outside the visible working area. Compact
chrome and locally scrollable secondary detail address the measured size budget
while retaining the existing workflow.

## 4. Acceptance evidence required

1. Full-app editor region is at least three rows at 80×24; rendered typed SQL is
   also present, so geometry alone cannot conceal an unusable editor.
2. Genuine Tab cycling reaches workgroup, catalog, database, query status and
   each available execution control. Their regions remain within the viewport.
3. A 120×40 → 80×24 → 120×40 pilot journey preserves typed SQL, all three context
   selections and focus. Preserve widget/model identity across resize.
4. Parameterize over every registered service plus Settings: each has nonzero
   content at 80×24. Open available ContextPickers and close them with Escape.
5. Exported compact screenshots contain the active service, active source and
   demo status. Check service/source transitions as well as initial Athena.
6. Add full-app Athena snapshots at 80×24 and 120×40 for carbon and github-light,
   with rendered-editor-text content guards. Review changed goldens visually.

## 5. Scope boundaries

No new service workflow, AWS call, model recreation, dependency, configurable
breakpoint or support guarantee below 80×24. Existing themes and the spacious
presentation remain supported. Test all relevant error and running states; do
not conceal status, errors, picker options or execution details to save space.

The user has authorized routine design decisions and unsupervised execution of
the issue's acceptance criteria. No separate design approval is needed.

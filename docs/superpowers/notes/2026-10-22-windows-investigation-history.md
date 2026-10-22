# Windows investigation history captured 2026-10-22

## 1. Scope

This maintainer record preserves CONTRIBUTING section 5.1 from
`ae21bd6bb37277ac8e44dfb203cc072b76168588` before relocation.
The recorded runs are historical evidence, not current Windows/Linux verification.
Their dates and outcomes must not be inferred as coverage for later revisions.
The [original decision issue](https://github.com/thekaveh/aws-tui/issues/283)
retains the investigation context.
See [current contributor requirements](../../../CONTRIBUTING.md#51-platform-evidence-and-ui-waits) for applicable checks.

## 2. Original Markdown

The protected source retains its diagnosis, uncertainty, original run links and pass table unchanged.
It does not authorize hosted workflow execution or establish a current native-platform pass.

`````markdown
### 5.1. Windows unit reliability

The unit workflow retains its manual OS/Python matrix: `macos-14`, `ubuntu-24.04`, and `windows-latest`, each on Python 3.11, 3.12, and 3.13. It uses `fail-fast: false` and sets no explicit `strategy.max-parallel` cap; actual capacity depends on runner availability. No capacity measurement supports a speculative change.

The `PermissionError` in #284 came from the test predicate racing atomic TOML replacement; #284 fixed that Settings polling/read race. The retained Windows Peek failure reached `the first preview scan finished` after the selected table's detail and preview availability waits passed. The old click had no rendered-geometry readiness check. #282 adds display, positive-geometry, and hit-test waits, asserts click delivery, and covers deferred projection with a controlled Pilot regression.

Historical click geometry and interleaving were not logged, so the exact cause of that occurrence is not established.

The following consecutive main promotions had all three Windows Python jobs pass on attempt 1:

| Promotion | Main CI run | Windows 3.11 / 3.12 / 3.13 |
| --- | --- | --- |
| [#304](https://github.com/thekaveh/aws-tui/pull/304) | [36764453874](https://github.com/thekaveh/aws-tui/actions/runs/36764453874) | pass / pass / pass |
| [#310](https://github.com/thekaveh/aws-tui/pull/310) | [36807953838](https://github.com/thekaveh/aws-tui/actions/runs/36807953838) | pass / pass / pass |
| [#315](https://github.com/thekaveh/aws-tui/pull/315) | [37036579717](https://github.com/thekaveh/aws-tui/actions/runs/37036579717) | pass / pass / pass |

The final PR-head push and pull-request runs also passed on attempt 1. These historical results do not establish current Windows or Linux verification or permanent reliability. See [#283](https://github.com/thekaveh/aws-tui/issues/283) for the diagnosis and decision record.

`````

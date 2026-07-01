# 1. Contributing to aws-tui

Thanks for your interest. aws-tui is pre-release; the API and config schema may change before v1.0.

## 1.1. Quickstart

```bash
git clone https://github.com/thekaveh/aws-tui.git
cd aws-tui
./scripts/bootstrap.sh           # uv guard, Python 3.11 hook runtime, sync + hooks
uv run pytest                    # default non-Docker suite
uv run pytest tests/unit         # unit-only fast path
uv run pytest tests/unit tests/integration --cov=aws_tui --cov-report=term-missing
./scripts/dev.sh                 # launch with Textual dev tools (live-reload .tcss)
```

## 1.2. Layout

This repo follows a strict layer architecture; see [docs/architecture.md](docs/architecture.md):

```
View (Textual)  →  ViewModel (VMx)  →  Service plugins  →  Domain ops  →  Infrastructure
```

`scripts/check-layers.sh` parses imports with `ast`, resolves relative imports, and fails CI on any forbidden edge.

## 1.3. Commits

We use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>(<scope>): <subject>
```

Types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `perf`, `ci`, `build`.
Scopes follow the layer names (`infra`, `domain`, `vm`, `services`, `ui`, `app`, `ci`, etc.).

## 1.4. Pull requests

- Branch from `main`. Open the PR early; mark draft until ready.
- CI must be green. Snapshot test changes need explicit review of the goldens diff.
- New services go under `src/aws_tui/services/<name>/` and register in `src/aws_tui/composition.py`. See [docs/adding-a-service.md](docs/adding-a-service.md).
- Adding an AWS API call? Run integration tests against `moto`. For S3-compatible quirks, add a note in [docs/connections.md](docs/connections.md).

## 1.5. Code of conduct

Participation in this project is governed by the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).

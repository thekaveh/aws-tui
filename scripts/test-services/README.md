# Test-services harness

Local AWS-compatible service mocks for aws-tui development. Each service
sub-directory is self-contained (Docker Compose + seed script + config
snippet + up/down lifecycle scripts) so you can spin up the ones you need
and leave the rest alone.

## What's here today

### `s3/` — MinIO-backed S3

S3-compliant local backend via the official MinIO container. Realistic
seeded dataset (5 buckets, ~90 objects: nested folder trees, unicode +
long filenames, small files + an 8MB+ object that exercises the
multipart upload path).

```
scripts/test-services/s3/up.sh           # start + seed (idempotent)
scripts/test-services/s3/down.sh         # stop, data preserved
scripts/test-services/s3/down.sh --purge # stop AND wipe the data volume
```

Then add the snippet at `scripts/test-services/s3/config-snippet.toml`
to `~/.config/aws-tui/config.toml` and launch `aws-tui`.

| Bucket | Shape |
|---|---|
| `aws-tui-dev-photos` | 3-level nested folders (year/month/day), ~30 small files |
| `aws-tui-dev-logs` | Flat list of ~50 rotated log files |
| `aws-tui-dev-archive` | Nested + a 1MB and an 8MB+ file (multipart trigger) |
| `aws-tui-dev-empty` | Empty (exercises the empty pane state) |
| `aws-tui-dev-unicode` | Unicode + spaces + long filenames |

Edit the seed dataset in `s3/seed.py` (the `_BUCKETS` dict at the bottom
of the module) and re-run `up.sh` to refresh.

## Extending to other AWS services

When aws-tui ships its second service (EC2, IAM, Lambda, …), add a
sibling sub-directory and follow the s3/ layout:

```
scripts/test-services/<service>/
├── docker-compose.yml    # the test container (LocalStack, fake-ec2, etc.)
├── seed.py               # the dataset seeder
├── up.sh                 # start + seed (calls docker compose + seed.py)
├── down.sh               # stop (--purge to wipe data)
└── config-snippet.toml   # paste-into-config.toml block
```

**For services without a dedicated mock (EC2, IAM, Lambda, …), use
[LocalStack](https://docs.localstack.cloud/) as the backend.** Single
container that mocks 60+ AWS services; heavier than MinIO so we keep it
optional. The directory pattern still applies — `scripts/test-services/ec2/docker-compose.yml`
would pin the localstack image and seed.py would talk to
`http://localhost:4566`.

## Why this layout (and not `docker-compose.yml` at the repo root)

- Keeps dev tooling out of the way of users who don't need it.
- Lets each service evolve independently (different containers, different
  seed shapes, different healthchecks).
- The repo's existing `tests/integration/` MinIO tier uses
  `testcontainers/minio` programmatically; this harness is for **manual
  exploratory dev**, not the CI test suite. The two paths intentionally
  don't share runtime so a flaky harness can't break CI.

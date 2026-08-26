#!/usr/bin/env bash
# Start the local Adobe S3Mock container and seed it with the dev dataset.
#
# Idempotent: re-running starts the existing S3Mock instance and re-seeds.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../../.."

echo "==> starting S3Mock via docker compose"
docker compose -f scripts/test-services/s3/docker-compose.yml up -d

echo "==> waiting for S3Mock to report ready"
# docker compose's healthcheck already covers readiness; this block makes
# the wait visible to the user.
for _ in $(seq 1 30); do
    state=$(docker inspect -f '{{.State.Health.Status}}' aws-tui-dev-s3mock 2>/dev/null || echo "starting")
    if [ "$state" = "healthy" ]; then
        break
    fi
    sleep 1
done

echo "==> seeding buckets"
./scripts/run-with-uv.sh python scripts/test-services/s3/seed.py

cat <<EOF

==> dev S3 is up:
    S3 API:     http://localhost:9000
    Credentials: test / test (S3Mock accepts arbitrary test credentials)

==> point aws-tui at it by adding the snippet at
       scripts/test-services/s3/config-snippet.toml
    to your <config-dir>/config.toml (see docs/platforms.md), then launch:
       ./scripts/run-with-uv.sh aws-tui

==> teardown when done:
       scripts/test-services/s3/down.sh           # stop (preserves data)
       scripts/test-services/s3/down.sh --purge   # stop AND wipe data volume

EOF

#!/usr/bin/env bash
# Start the local MinIO container and seed it with the dev dataset.
#
# Idempotent: re-running tops the existing MinIO instance and re-seeds.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../../.."

echo "==> starting MinIO via docker compose"
docker compose -f scripts/test-services/s3/docker-compose.yml up -d

echo "==> waiting for MinIO to report ready"
# docker compose's healthcheck already covers readiness; this block makes
# the wait visible to the user.
for i in $(seq 1 30); do
    state=$(docker inspect -f '{{.State.Health.Status}}' aws-tui-dev-minio 2>/dev/null || echo "starting")
    if [ "$state" = "healthy" ]; then
        break
    fi
    sleep 1
done

echo "==> seeding buckets"
uv run python scripts/test-services/s3/seed.py

cat <<EOF

==> dev S3 is up:
    S3 API:     http://localhost:9000
    Console:    http://localhost:9001 (login: minioadmin / minioadmin)

==> point aws-tui at it by adding the snippet at
       scripts/test-services/s3/config-snippet.toml
    to your ~/.config/aws-tui/config.toml, then launch:
       uv run aws-tui

==> teardown when done:
       scripts/test-services/s3/down.sh           # stop (preserves data)
       scripts/test-services/s3/down.sh --purge   # stop AND wipe data volume

EOF

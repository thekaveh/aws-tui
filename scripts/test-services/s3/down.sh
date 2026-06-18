#!/usr/bin/env bash
# Stop the local MinIO container.
#
# By default the named volume (aws-tui-dev-minio-data) is preserved, so the
# next `up.sh` reuses the seeded data. Pass --purge to also remove the
# volume — re-seeding will then start from scratch.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../../.."

PURGE=0
for arg in "$@"; do
    case "$arg" in
        --purge|--volumes|-v)
            PURGE=1
            ;;
        *)
            echo "unknown arg: $arg" >&2
            echo "usage: $0 [--purge]" >&2
            exit 2
            ;;
    esac
done

if [ "$PURGE" -eq 1 ]; then
    echo "==> stopping MinIO + removing data volume (--purge)"
    docker compose -f scripts/test-services/s3/docker-compose.yml down -v
else
    echo "==> stopping MinIO (data volume preserved)"
    docker compose -f scripts/test-services/s3/docker-compose.yml down
fi

echo "==> done."

#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
banned() {
  local folder=$1; shift
  local label=$1; shift
  for pat in "$@"; do
    matches=$(grep -RnE "^\s*(from|import)\s+${pat}(\\b|\\.)" "src/aws_tui/${folder}" 2>/dev/null || true)
    if [ -n "$matches" ]; then
      echo "::error::layer rule violation in ${folder}: must not import ${pat}"
      echo "$matches"
      fail=1
    fi
  done
}

banned vm        "ViewModel"     "textual" "boto3" "aioboto3" "botocore" "aws_tui\\.ui" "aws_tui\\.services"
banned domain    "Domain"        "textual" "aws_tui\\.vm" "aws_tui\\.ui" "aws_tui\\.services"
banned infra     "Infrastructure" "textual" "aws_tui\\.vm" "aws_tui\\.ui" "aws_tui\\.services" "aws_tui\\.domain"
banned ui        "View"          "boto3" "aioboto3" "botocore" "aws_tui\\.infra\\.aws_session" "aws_tui\\.infra\\.connection_resolver"
# services/ is allowed to import vm/ for the Service / ServiceDescriptor
# protocols that live in vm/services_protocol.py (the protocol home is
# vm/ so that the VM layer can satisfy it without violating the
# one-way arrow — see docs/architecture.md §2). Everything else stays
# banned. ui/ is a hard upward dependency.
banned services  "Services"      "textual" "aws_tui\\.ui"

# demo/ is the runtime-mock layer. It implements domain interfaces
# and produces fake clients consumed by the composition root only.
# Every production layer is banned from importing it so accidentally
# wiring fake clients into prod code fails CI immediately.
banned vm        "ViewModel"     "aws_tui\\.demo"
banned domain    "Domain"        "aws_tui\\.demo"
banned infra     "Infrastructure" "aws_tui\\.demo"
banned ui        "View"          "aws_tui\\.demo"
banned services  "Services"      "aws_tui\\.demo"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "layer rules clean"

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
banned services  "Services"      "textual"

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "layer rules clean"

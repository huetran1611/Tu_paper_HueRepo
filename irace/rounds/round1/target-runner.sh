#!/usr/bin/env bash
set -euo pipefail
ROUND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export IRACE_FIXED_ARGS_FILE="${ROUND_DIR}/fixed.args"
export IRACE_TIME_LIMIT_SEC="${IRACE_TIME_LIMIT_SEC:-30}"
exec "${ROUND_DIR}/../../target-runner.sh" "$@"

#!/usr/bin/env bash
set -euo pipefail

CONFIG_ID="${1:-}"
INSTANCE_ID="${2:-}"
SEED="${3:-}"
INSTANCE="${4:-}"
shift 4 || true
BOUND=""
if [[ $# -gt 0 && "${1}" != --* ]]; then
  BOUND="${1}"
  shift
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${ROOT_DIR}/build/tabubu_TimeDependent_v2"
LOG_DIR="${ROOT_DIR}/irace/logs"
mkdir -p "${LOG_DIR}"

if [[ "${INSTANCE}" != /* ]]; then
  INSTANCE="${ROOT_DIR}/${INSTANCE}"
fi

ARGS=(
  "${INSTANCE}"
  "--seed=${SEED}"
  "--attempts=1"
  "--time-limit=${IRACE_TIME_LIMIT_SEC:-30}"
  "--truck-capacity=400"
  "--drone-capacity=2.27"
)

if [[ -n "${IRACE_FIXED_ARGS_FILE:-}" && -f "${IRACE_FIXED_ARGS_FILE}" ]]; then
  while IFS= read -r line; do
    [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
    ARGS+=("${line}")
  done < "${IRACE_FIXED_ARGS_FILE}"
fi

while [[ $# -gt 0 ]]; do
  name="$1"
  value="${2:-}"
  shift 2 || true
  case "${name}" in
    --iters) ARGS+=("--iters=${value}") ;;
    --segment-iters) ARGS+=("--segment-iters=${value}") ;;
    --no-improve) ARGS+=("--no-improve=${value}") ;;
    --knn-k) ARGS+=("--knn-k=${value}") ;;
    --knn-window) ARGS+=("--knn-window=${value}") ;;
    --alpha) ARGS+=("--alpha=${value}") ;;
    --T0) ARGS+=("--T0=${value}") ;;
    --c-tabu) ARGS+=("--c-tabu=${value}") ;;
    --h-mode) ARGS+=("--h-mode=${value}") ;;
    --h-div) ARGS+=("--h-div=${value}") ;;
    --gamma1) ARGS+=("--gamma1=${value}") ;;
    --gamma2) ARGS+=("--gamma2=${value}") ;;
    --gamma3) ARGS+=("--gamma3=${value}") ;;
    --gamma4) ARGS+=("--gamma4=${value}") ;;
    --kappa) ARGS+=("--kappa=${value}") ;;
    --tau-v) ARGS+=("--tau-v=${value}") ;;
    --r-destroy) ARGS+=("--r-destroy=${value}") ;;
    *) ;;
  esac
done

H_MODE_VAL=""
H_DIV_VAL=""
GAMMA1_VAL=""
GAMMA2_VAL=""
GAMMA3_VAL=""
for arg in "${ARGS[@]}"; do
  case "${arg}" in
    --h-mode=*) H_MODE_VAL="${arg#*=}" ;;
    --h-div=*) H_DIV_VAL="${arg#*=}" ;;
    --gamma1=*) GAMMA1_VAL="${arg#*=}" ;;
    --gamma2=*) GAMMA2_VAL="${arg#*=}" ;;
    --gamma3=*) GAMMA3_VAL="${arg#*=}" ;;
  esac
done

if [[ -n "${H_MODE_VAL}" && -n "${H_DIV_VAL}" ]]; then
  if (( H_DIV_VAL <= H_MODE_VAL )); then
    echo 1000000000000
    exit 0
  fi
fi

if [[ -n "${GAMMA1_VAL}" && -n "${GAMMA2_VAL}" && -n "${GAMMA3_VAL}" ]]; then
  if ! awk -v g1="${GAMMA1_VAL}" -v g2="${GAMMA2_VAL}" -v g3="${GAMMA3_VAL}" 'BEGIN { exit (g1 > g2 && g2 > g3) ? 0 : 1 }'; then
    echo 1000000000000
    exit 0
  fi
fi

OUT="${LOG_DIR}/run-c${CONFIG_ID}-i${INSTANCE_ID}-s${SEED}.log"
if ! "${BIN}" "${ARGS[@]}" > "${OUT}" 2>&1; then
  echo 1000000000000
  exit 0
fi

cost="$(awk -F': ' '/Improved Solution Cost:/ {v=$2} END {print v}' "${OUT}")"
feas="$(awk -F': ' '/Final solution feasibility:/ {v=$2} END {print v}' "${OUT}")"

if [[ -z "${cost}" ]]; then
  echo 1000000000000
elif [[ "${feas}" == "INFEASIBLE" ]]; then
  awk -v c="${cost}" 'BEGIN { printf "%.6f\n", c + 1000000000 }'
else
  echo "${cost}"
fi

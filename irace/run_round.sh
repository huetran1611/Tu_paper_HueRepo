#!/usr/bin/env bash
set -euo pipefail

ROUND="${1:?Usage: irace/run_round.sh round1|round2|round3|round4|round5}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROUND_DIR="${ROOT_DIR}/irace/rounds/${ROUND}"

if [[ ! -d "${ROUND_DIR}" ]]; then
  echo "Unknown round: ${ROUND}" >&2
  exit 1
fi

cd "${ROOT_DIR}"
mkdir -p build
g++ -O3 -std=c++20 src_v2/tabubu_TimeDependent_v2.cpp -o build/tabubu_TimeDependent_v2

conda run -n base Rscript -e ".libPaths(c('irace/Rlib', .libPaths())); library(irace); irace.cmdline(c('--scenario', 'irace/rounds/${ROUND}/scenario.txt'))"


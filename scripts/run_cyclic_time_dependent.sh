#!/usr/bin/env bash
set -uo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 SOLVER INSTANCE_BASE REPETITION OUTPUT_DIR" >&2
  exit 2
fi

solver=$(realpath "$1")
instance_base=$2
repetition=$3
output_dir=$4
repo_root=${GITHUB_WORKSPACE:-$(pwd)}
instance_dir="$repo_root/instance_time_dependent"
seed=$((41 + repetition))

instance_file="$instance_dir/$instance_base.txt"
vmax_file="$instance_dir/$instance_base.vmax_ij.txt"
theta_file="$instance_dir/$instance_base.theta_ijl.txt"

for required_file in "$solver" "$instance_file" "$vmax_file" "$theta_file"; do
  if [[ ! -f "$required_file" ]]; then
    echo "Missing required file: $required_file" >&2
    exit 2
  fi
done

mkdir -p "$output_dir"
output_dir=$(realpath "$output_dir")
work_dir=$(mktemp -d "${RUNNER_TEMP:-/tmp}/cyclic-${instance_base}-run${repetition}-XXXXXX")
trap 'rm -rf "$work_dir"' EXIT

result_file="$output_dir/${instance_base}_run_${repetition}.txt"
log_file="$output_dir/${instance_base}_run_${repetition}.log"

pushd "$work_dir" >/dev/null
set +e
"$solver" "$instance_file" \
  --truck-vmax-file="$vmax_file" \
  --truck-theta-file="$theta_file" \
  --neighborhood-selection=cyclic \
  --attempts=1 \
  --seed="$seed" >"$log_file" 2>&1
solver_exit_code=$?
set -e

{
  echo "Instance: $instance_base"
  echo "Customer group: ${instance_base%%.*}"
  echo "Repetition: $repetition"
  echo "Experiment strategy: cyclic"
  echo "Experiment seed: $seed"
  echo "Solver exit code: $solver_exit_code"
  if [[ -f output_solution_best.txt ]]; then
    cat output_solution_best.txt
  else
    echo "Neighborhood selection: cyclic"
    echo "Random seed: $seed"
    echo "Simulated annealing: disabled"
    echo "Diversification: disabled"
    echo "Initial solution cost:"
    echo "Improved solution cost:"
    echo "Worst solution cost:"
    echo "Mean solution cost:"
    echo "Mean elapsed time:"
    echo "Final solution feasibility: FAILED"
  fi
} >"$result_file"
popd >/dev/null

echo "Completed $instance_base repetition $repetition with exit code $solver_exit_code"


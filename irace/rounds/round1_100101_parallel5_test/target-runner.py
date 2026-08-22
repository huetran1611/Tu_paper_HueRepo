#!/usr/bin/env python3
import os
import re
import subprocess
import sys
import time
from pathlib import Path


IMPROVED_RE = re.compile(r"Improved Solution Cost:\s*([0-9.eE+-]+)")
FEAS_RE = re.compile(r"Final solution feasibility:\s*(FEASIBLE|INFEASIBLE)")
PENALTY = 1_000_000_000_000.0


def parse_pairs(args):
    pairs = []
    i = 0
    while i < len(args):
        name = args[i]
        value = args[i + 1] if i + 1 < len(args) else ""
        pairs.append((name, value))
        i += 2
    return pairs


def main():
    if len(sys.argv) < 5:
        print(f"{PENALTY:.6f}")
        return 0

    config_id, instance_id, seed, instance = sys.argv[1:5]
    remaining = sys.argv[5:]
    if remaining and not remaining[0].startswith("--"):
        remaining = remaining[1:]
    target_args = remaining

    round_dir = Path(__file__).resolve().parent
    root_dir = round_dir.parents[2]
    binary = root_dir / "build" / "tabubu_TimeDependent_v2"
    log_dir = round_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    instance_path = Path(instance)
    if not instance_path.is_absolute():
        instance_path = root_dir / instance_path

    args = [
        str(binary),
        str(instance_path),
        "--attempts=1",
        "--time-limit=30",
        "--truck-capacity=400",
        "--drone-capacity=2.27",
        f"--seed={seed}",
    ]

    fixed_args = round_dir / "fixed.args"
    if fixed_args.exists():
        for line in fixed_args.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                args.append(line)

    chosen = {}
    for name, value in parse_pairs(target_args):
        chosen[name] = value
        if name in {
            "--iters",
            "--segment-iters",
            "--no-improve",
            "--knn-k",
            "--knn-window",
            "--alpha",
            "--T0",
            "--c-tabu",
            "--h-mode",
            "--h-div",
            "--gamma1",
            "--gamma2",
            "--gamma3",
            "--gamma4",
            "--kappa",
            "--tau-v",
            "--r-destroy",
        }:
            args.append(f"{name}={value}")

    try:
        if int(chosen.get("--h-div", "999")) <= int(chosen.get("--h-mode", "-1")):
            print(f"{PENALTY:.6f}")
            return 0
    except ValueError:
        print(f"{PENALTY:.6f}")
        return 0

    log_path = log_dir / f"run-c{config_id}-i{instance_id}-s{seed}.log"
    started = time.monotonic()
    timeout_sec = int(os.environ.get("IRACE_WALL_TIMEOUT_SEC", "120"))
    try:
        completed = subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_sec,
            check=False,
        )
        output = completed.stdout
        log_path.write_text(output, encoding="utf-8")
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        log_path.write_text(output + f"\\nTIMEOUT after {timeout_sec}s\\n", encoding="utf-8")
        print(f"{PENALTY:.6f}")
        return 0

    cost = None
    feasible = None
    for match in IMPROVED_RE.finditer(output):
        cost = float(match.group(1))
    for match in FEAS_RE.finditer(output):
        feasible = match.group(1)

    if cost is None:
        objective = PENALTY
    elif feasible == "INFEASIBLE":
        objective = cost + 1_000_000_000.0
    else:
        objective = cost

    elapsed = time.monotonic() - started
    with (log_dir / "runs.tsv").open("a", encoding="utf-8") as fh:
        fh.write(f"{config_id}\\t{instance_id}\\t{seed}\\t{objective:.6f}\\t{elapsed:.3f}\\t{' '.join(target_args)}\\n")

    print(f"{objective:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

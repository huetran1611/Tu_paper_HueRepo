#!/usr/bin/env python3
"""Create 500-customer time-dependent instances from 100/200 pools.

For each distance bucket n in {10, 20, 30, 40}, each 500.n.m instance samples
500 unique-coordinate customers from the union of 100.n.1..4 and 200.n.1..4.
The generated vmax_ij and theta_ijl files follow instance_time_dependent/README.md.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TD_DIR = ROOT / "instance_time_dependent"
DISTANCE_BUCKETS = (10, 20, 30, 40)
RUNS = (1, 2, 3, 4)
SOURCE_SIZES = (100, 200)
CUSTOMERS = 500
TRUCKS = 10
DRONES = 10
BASE_SEED = 20260626
LW_VALUES = (1800, 2700, 3600)
THETA_BASE_L = (0.8, 0.5, 0.7, 0.8, 0.9, 0.7, 0.7, 0.9, 0.9, 0.7, 0.6, 0.5)


@dataclass(frozen=True)
class Customer:
    x: str
    y: str
    dronable: str
    demand: str
    drone_service: str
    truck_service: str
    lw: str
    source: str
    source_line: int

    @property
    def coord_key(self) -> tuple[str, str]:
        return (self.x, self.y)


def seed_for_name(name: str) -> int:
    return BASE_SEED + sum((idx + 1) * ord(ch) for idx, ch in enumerate(name))


def read_customers(path: Path) -> list[Customer]:
    customers: list[Customer] = []
    with path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    data_started = False
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("X") and "Demand" in stripped:
            data_started = True
            continue
        if not data_started:
            continue
        parts = stripped.split()
        if len(parts) != 7:
            raise ValueError(f"Unexpected customer row in {path}:{line_no}: {stripped}")
        customers.append(Customer(*parts, source=path.name, source_line=line_no))
    return customers


def unique_pool(distance_bucket: int) -> list[Customer]:
    seen: set[tuple[str, str]] = set()
    pool: list[Customer] = []
    for source_size in SOURCE_SIZES:
        for run in RUNS:
            path = TD_DIR / f"{source_size}.{distance_bucket}.{run}.txt"
            for customer in read_customers(path):
                if customer.coord_key in seen:
                    continue
                seen.add(customer.coord_key)
                pool.append(customer)
    if len(pool) < CUSTOMERS:
        raise ValueError(
            f"Pool for n={distance_bucket} has only {len(pool)} unique coordinates; "
            f"need {CUSTOMERS}."
        )
    return pool


def adjusted_demand(original: str, rng: random.Random) -> str:
    value = float(original)
    if value <= 2:
        return original
    return f"{rng.uniform(1.5, 2.0):.14g}"


def shuffled_lw_values(count: int, rng: random.Random) -> list[int]:
    values = [LW_VALUES[idx % len(LW_VALUES)] for idx in range(count)]
    rng.shuffle(values)
    return values


def write_instance(path: Path, selected: list[Customer], rng: random.Random) -> None:
    lw_values = shuffled_lw_values(len(selected), rng)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(f"trucks_count {TRUCKS}\n")
        f.write(f"drones_count {DRONES}\n")
        f.write(f"customers {len(selected)}\n")
        f.write("depot 0 0\n")
        f.write("Coordinate X         Coordinate Y         Dronable Demand\n")
        f.write("X\tY\tDronable\tDemand\tDrone_service\tTruck_service\tLw\n")
        for customer, lw in zip(selected, lw_values):
            demand = adjusted_demand(customer.demand, rng)
            f.write(
                f"{customer.x}\t{customer.y}\t{customer.dronable}\t{demand}\t"
                f"{customer.drone_service}\t{customer.truck_service}\t{lw}\n"
            )


def write_vmax(path: Path, node_count: int, rng: random.Random) -> None:
    speeds: dict[tuple[int, int], float] = {}
    for i in range(node_count):
        for j in range(i + 1, node_count):
            speed = rng.uniform(0.7, 0.95) * 15.6
            speeds[(i, j)] = speed
            speeds[(j, i)] = speed
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("# i j vmax_ij_m_per_s; symmetric, no self-loops\n")
        for i in range(node_count):
            for j in range(node_count):
                if i == j:
                    continue
                f.write(f"{i} {j} {speeds[(i, j)]:.6f}\n")


def write_theta(path: Path, node_count: int, rng: random.Random) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("# i j l theta_ijl; 12 hourly segments, no self-loops\n")
        for i in range(node_count):
            for j in range(node_count):
                if i == j:
                    continue
                for l, base in enumerate(THETA_BASE_L):
                    theta = round(rng.uniform(0.9, 1.0) * base, 2)
                    f.write(f"{i} {j} {l} {theta:.2f}\n")


def ensure_output_clear(paths: list[Path], overwrite: bool) -> None:
    existing = [path for path in paths if path.exists()]
    if existing and not overwrite:
        names = ", ".join(path.name for path in existing[:5])
        if len(existing) > 5:
            names += ", ..."
        raise FileExistsError(f"Output files already exist: {names}. Use --overwrite.")


def generate(overwrite: bool) -> None:
    output_paths = []
    for distance_bucket in DISTANCE_BUCKETS:
        for run in RUNS:
            stem = f"500.{distance_bucket}.{run}"
            output_paths.extend(
                [
                    TD_DIR / f"{stem}.txt",
                    TD_DIR / f"{stem}.vmax_ij.txt",
                    TD_DIR / f"{stem}.theta_ijl.txt",
                ]
            )
    ensure_output_clear(output_paths, overwrite)

    for distance_bucket in DISTANCE_BUCKETS:
        pool = unique_pool(distance_bucket)
        print(f"n={distance_bucket}: unique pool={len(pool)}")
        for run in RUNS:
            stem = f"500.{distance_bucket}.{run}"
            rng = random.Random(seed_for_name(f"{stem}.txt"))
            selected = rng.sample(pool, CUSTOMERS)
            if len({customer.coord_key for customer in selected}) != CUSTOMERS:
                raise AssertionError(f"Duplicate coordinates selected for {stem}")

            write_instance(TD_DIR / f"{stem}.txt", selected, rng)
            write_vmax(TD_DIR / f"{stem}.vmax_ij.txt", CUSTOMERS + 1, rng)
            write_theta(TD_DIR / f"{stem}.theta_ijl.txt", CUSTOMERS + 1, rng)
            print(f"created {stem}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing 500.* outputs")
    args = parser.parse_args()
    generate(overwrite=args.overwrite)


if __name__ == "__main__":
    main()

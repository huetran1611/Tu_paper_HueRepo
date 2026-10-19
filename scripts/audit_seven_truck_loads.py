"""Compute exact per-depot-trip truck loads for all 1,600 saved solutions."""
from collections import defaultdict
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "results/seven_github_experiments_20261016"


def trips(route):
    current = []
    for node in route:
        if node == 0:
            if current:
                yield current
                current = []
        else:
            current.append(node)
    if current:
        raise ValueError("Truck route does not end at depot")


def demands(path, customers):
    values = [Decimal(0)]
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) != 7:
            continue
        try:
            numeric = [Decimal(value) for value in fields]
        except ArithmeticError:
            continue
        values.append(numeric[3])
    if len(values) != customers+1 or any(value < 0 for value in values):
        raise ValueError(f"Unexpected demand data in {path}")
    return values


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    source = OUTPUT / "all_solutions_and_parameters.csv"
    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    tree = {item["path"]: item["sha"] for item in json.loads((OUTPUT / "source_tree.json").read_text())["tree"]
            if item["type"] == "blob"}
    cached = {}
    all_trips = []
    groups = defaultdict(list)
    for row in rows:
        filename = row["input_file"]
        if filename not in cached:
            path = ROOT / filename
            content = path.read_bytes()
            git_hash = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
            if git_hash != tree.get(filename):
                raise ValueError(f"Input differs from experiment commit: {filename}")
            cached[filename] = demands(path, int(row["customers"]))
        weights = cached[filename]
        truck_routes = json.loads(row["truck_routes"])
        per_truck, case_trips = [], []
        for truck_index, route in enumerate(truck_routes, 1):
            truck_loads = []
            for trip_index, customers in enumerate(trips(route), 1):
                load = sum((weights[node] for node in customers), Decimal(0))
                if load > Decimal(row["truck_capacity_kg"]):
                    raise ValueError(f"Capacity exceeded in {row['global_id']}")
                record = {"global_id": row["global_id"], "experiment": row["experiment"],
                          "instance": row["instance"], "scenario": row["scenario"], "mode": row["mode"],
                          "drone_capacity_kg": row["drone_capacity_kg"], "variant": row["variant"],
                          "run": row["run"], "customers": row["customers"], "truck": truck_index,
                          "trip": trip_index, "load_kg": str(load), "customer_count": len(customers),
                          "customer_ids": json.dumps(customers), "source_url": row["source_url"]}
                all_trips.append(record)
                case_trips.append(record)
                truck_loads.append(load)
                groups[(row["experiment"], row["customers"], row["mode"], row["scenario"],
                        row["drone_capacity_kg"], row["variant"])].append(record)
            per_truck.append(str(max(truck_loads, default=Decimal(0))))
        if len(case_trips) != int(row["truck_trips"]):
            raise ValueError("Trip count disagrees with saved route summary")
        best = max(case_trips, key=lambda t: Decimal(t["load_kg"]), default=None)
        row.update(max_truck_trip_load_kg=best["load_kg"] if best else "0",
                   max_load_truck=best["truck"] if best else "", max_load_trip=best["trip"] if best else "",
                   max_load_customer_count=best["customer_count"] if best else 0,
                   max_load_customer_ids=best["customer_ids"] if best else "[]",
                   truck_max_trip_loads_kg_json=json.dumps(per_truck), input_git_hash_verified=True)
    if len(rows) != 1600 or len(cached) != 96:
        raise ValueError("Unexpected case or input coverage")
    largest = max(Decimal(t["load_kg"]) for t in all_trips)
    winners = [t for t in all_trips if Decimal(t["load_kg"]) == largest]
    summary = []
    for group, records in sorted(groups.items()):
        best = max(records, key=lambda t: Decimal(t["load_kg"]))
        summary.append({**dict(zip(("experiment", "customers", "mode", "scenario", "drone_capacity_kg", "variant"), group)),
                        "truck_trips_checked": len(records), "max_trip_load_kg": best["load_kg"],
                        "instance": best["instance"], "run": best["run"], "truck": best["truck"],
                        "trip": best["trip"], "global_id": best["global_id"]})
    write_csv(OUTPUT / "truck_trip_loads.csv", all_trips)
    write_csv(OUTPUT / "max_truck_load_by_configuration.csv", summary)
    temporary = source.with_suffix(".tmp.csv")
    write_csv(temporary, rows)
    temporary.replace(source)
    report = {"solutions": len(rows), "source_input_files_verified": len(cached),
              "truck_trips_checked": len(all_trips), "maximum_trip_load_kg": str(largest),
              "maximizing_trips": winners, "calculation": "sum original input demands between consecutive depot visits, Decimal arithmetic"}
    (OUTPUT / "maximum_truck_load.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

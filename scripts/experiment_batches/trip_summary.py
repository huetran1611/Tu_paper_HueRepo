import csv
import json
from pathlib import Path

fields = [
    "instance",
    "mode",
    "drone_capacity",
    "gridsize",
    "number_of_customer",
    "run",
    "truck_route",
    "drone_route",
    "drone_ratio",
    "best_objective",
    "solution_status",
    "process_status",
    "return_code",
    "elapsed_seconds",
]
rows = []
for path in Path("all_td_trip_capacity_artifacts").rglob("result.json"):
    rows.append(json.loads(path.read_text(encoding="utf-8")))
rows.sort(key=lambda r: (
    int(r["number_of_customer"]),
    int(r["gridsize"]),
    str(r["instance"]),
    str(r["mode"]),
    float(r["drone_capacity"]),
    int(r["run"]),
))
with open("time_dependent_trip_capacity_summary.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
print(f"rows={len(rows)}")
bad_rows = [r for r in rows if r.get("process_status") != "success"]
if len(rows) != 960:
    print("Warning: expected 960 rows")
if bad_rows:
    print(f"Warning: {len(bad_rows)} rows have non-success process_status")

import csv
import re
from pathlib import Path

from summarize_static_td_all_instance_new import (
    compute_drone_route_detail,
    compute_truck_route_detail,
    distance_matrix,
    load_td_speed_model,
    merge_violation,
    parse_instance,
    parse_routes,
    read_objective,
)


ROOT = Path(__file__).resolve().parents[1]
INSTANCE_ROOT = ROOT / "instance_new"
OUT_CSV = INSTANCE_ROOT / "truck1trip_summary_all_sets.csv"
OUT_DETAIL_CSV = INSTANCE_ROOT / "truck1trip_waiting_violations_detail_all_sets.csv"


def read_feasibility(solution_path):
    for line in solution_path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^Final solution feasibility:\s+(.+)$", line)
        if m:
            return m.group(1).strip()
    return ""


def read_route_lines(solution_path, vehicle_type):
    prefix = f"{vehicle_type} "
    return [
        line.strip()
        for line in solution_path.read_text(encoding="utf-8-sig").splitlines()
        if line.startswith(prefix)
    ]


def find_solution(instance_path):
    solution = instance_path / "truck1trip_solution_best.txt"
    return solution if solution.exists() else None


def validate_truck1trip_solution(instance_path, solution_path):
    _, _, n, loc, demand, deadline, serve_drone, serve_truck = parse_instance(instance_path)
    dist = distance_matrix(loc)
    vmax, theta = load_td_speed_model(instance_path, n)

    wait_violations = {}
    capacity_bad = False
    energy_bad = False
    max_route_time = 0.0
    detail_rows = []
    truck_middle_depot_count = 0

    for vehicle_type, vehicle_idx, route in parse_routes(solution_path):
        if vehicle_type == "truck":
            for pos, node in enumerate(route[1:-1], start=1):
                if node == 0:
                    truck_middle_depot_count += 1
            route_time, violations, route_detail, _ = compute_truck_route_detail(
                route, dist, vmax, theta, serve_truck, deadline
            )
            merge_violation(wait_violations, violations)
        else:
            route_time, violations, cap_bad, en_bad, route_detail = compute_drone_route_detail(
                route, dist, demand, serve_drone, deadline
            )
            merge_violation(wait_violations, violations)
            capacity_bad = capacity_bad or cap_bad
            energy_bad = energy_bad or en_bad

        max_route_time = max(max_route_time, route_time)
        for row in route_detail:
            row["vehicle_id"] = vehicle_idx
            detail_rows.append(row)

    wait_total_sec = sum(wait_violations.values())
    feasible_by_recheck = (
        not wait_violations
        and not capacity_bad
        and not energy_bad
        and truck_middle_depot_count == 0
    )
    return (
        feasible_by_recheck,
        len(wait_violations),
        wait_total_sec,
        capacity_bad,
        energy_bad,
        truck_middle_depot_count,
        max_route_time,
        detail_rows,
    )


def main():
    rows = []
    detail_rows = []
    set_dirs = sorted(p for p in INSTANCE_ROOT.iterdir() if p.is_dir() and p.name.startswith("n"))
    for set_dir in set_dirs:
        for instance_path in sorted(p for p in set_dir.iterdir() if p.is_dir() and p.name.startswith("instance")):
            trucks, drones, _, _, _, _, _, _ = parse_instance(instance_path)
            solution = find_solution(instance_path)
            if solution is None:
                continue

            (
                feasible_by_recheck,
                violated_count,
                wait_sec,
                cap_bad,
                energy_bad,
                truck_middle_depot_count,
                td_makespan,
                instance_detail,
            ) = validate_truck1trip_solution(instance_path, solution)

            truck_routes = " || ".join(read_route_lines(solution, "Truck"))
            drone_routes = " || ".join(read_route_lines(solution, "Drone"))
            rows.append({
                "benchmark_set": set_dir.name,
                "instance": instance_path.name,
                "instance_full_name": f"{set_dir.name}/{instance_path.name}",
                "trucks_count": trucks,
                "drones_count": drones,
                "truck1trip_objective": f"{read_objective(solution):.6f}",
                "truck1trip_solution_rechecked_td_makespan": f"{td_makespan:.6f}",
                "solver_reported_feasibility": read_feasibility(solution),
                "rechecked_is_feasible": "YES" if feasible_by_recheck else "NO",
                "waiting_violated_customer_count": violated_count,
                "waiting_violation_total_sec": f"{wait_sec:.6f}",
                "waiting_violation_total_min": f"{wait_sec / 60.0:.6f}",
                "capacity_violation": "YES" if cap_bad else "NO",
                "energy_violation": "YES" if energy_bad else "NO",
                "truck_middle_depot_count": truck_middle_depot_count,
                "truck_routes": truck_routes,
                "drone_routes": drone_routes,
                "best_solution_file": str(solution),
            })

            for detail in instance_detail:
                detail_rows.append({
                    "benchmark_set": set_dir.name,
                    "instance": instance_path.name,
                    "instance_full_name": f"{set_dir.name}/{instance_path.name}",
                    "vehicle_type": detail["vehicle_type"],
                    "vehicle_id": detail["vehicle_id"],
                    "customer": detail["customer"],
                    "route_return_time_sec": f"{detail['route_return_time_sec']:.6f}",
                    "pickup_to_return_duration_sec": f"{detail['pickup_to_return_duration_sec']:.6f}",
                    "deadline_sec": f"{detail['deadline_sec']:.6f}",
                    "violation_sec": f"{detail['violation_sec']:.6f}",
                    "route_nodes": detail["route_nodes"],
                    "best_solution_file": str(solution),
                })

    if not rows:
        raise SystemExit("No truck1trip_solution_best.txt files found under instance_new")

    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with OUT_DETAIL_CSV.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "benchmark_set",
            "instance",
            "instance_full_name",
            "vehicle_type",
            "vehicle_id",
            "customer",
            "route_return_time_sec",
            "pickup_to_return_duration_sec",
            "deadline_sec",
            "violation_sec",
            "route_nodes",
            "best_solution_file",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(detail_rows)

    infeasible = sum(1 for r in rows if r["rechecked_is_feasible"] == "NO")
    wait_count = sum(int(r["waiting_violated_customer_count"]) for r in rows)
    wait_sec = sum(float(r["waiting_violation_total_sec"]) for r in rows)
    print(f"Written {OUT_CSV}")
    print(f"Written {OUT_DETAIL_CSV}")
    print(f"Rows: {len(rows)}")
    print(f"Rechecked infeasible: {infeasible}/{len(rows)}")
    print(f"Total waiting violated customers: {wait_count}")
    print(f"Total waiting violation: {wait_sec:.6f} sec = {wait_sec / 60.0:.6f} min")


if __name__ == "__main__":
    main()

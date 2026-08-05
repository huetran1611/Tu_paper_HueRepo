import csv
import math
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTANCE_ROOT = ROOT / "instance_new"
OUT_CSV = INSTANCE_ROOT / "static_td_feasibility_summary_all_sets.csv"

V_FLY_DRONE = 60.0 * 1000.0 / 3600.0
V_TAKE_OFF = 30.0 * 1000.0 / 3600.0
V_LANDING = 15.0 * 1000.0 / 3600.0
HEIGHT = 50.0
DRONE_CAPACITY = 5.0
DRONE_BATTERY_J = 1.59 * 3600000.0
POWER_BETA = 66.0
POWER_GAMMA = 397.0

TIME_SEGMENT = list(range(13))
DEFAULT_SIGMA = [0.9, 0.8, 0.4, 0.6, 0.9, 0.8, 0.6, 0.8, 0.8, 0.7, 0.5, 0.8]


def get_time_segment(t_hours):
    period_hr = TIME_SEGMENT[-1] - TIME_SEGMENT[0]
    t_hours = math.fmod(t_hours - TIME_SEGMENT[0], period_hr)
    if t_hours < 0:
        t_hours += period_hr
    t_hours += TIME_SEGMENT[0]
    for idx in range(len(TIME_SEGMENT) - 1):
        if TIME_SEGMENT[idx] <= t_hours < TIME_SEGMENT[idx + 1]:
            return idx
    return len(TIME_SEGMENT) - 2


def read_objective(solution_path):
    if not solution_path.exists():
        return None
    for line in solution_path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^Improved solution cost:\s+(.+)$", line)
        if m:
            return float(m.group(1))
    return None


def parse_instance(instance_path):
    name = instance_path.name
    solver_path = instance_path / f"{name}_solver.txt"
    lines = solver_path.read_text(encoding="utf-8-sig").splitlines()
    trucks = int(lines[0].split()[1])
    drones = int(lines[1].split()[1])
    n = int(lines[2].split()[1])
    loc = [(0.0, 0.0)]
    demand = [0.0]
    deadline = [0.0]
    serve_drone = [0.0]
    serve_truck = [0.0]
    for line in lines[6:6 + n]:
        p = line.split()
        loc.append((float(p[0]), float(p[1])))
        demand.append(float(p[3]))
        serve_drone.append(float(p[4]))
        serve_truck.append(float(p[5]))
        deadline.append(float(p[6]))
    return trucks, drones, n, loc, demand, deadline, serve_drone, serve_truck


def parse_routes(solution_path):
    routes = []
    pattern = re.compile(r"^(Truck|Drone)\s+(\d+):\s+(.*?)\s+\|")
    for line in solution_path.read_text(encoding="utf-8-sig").splitlines():
        m = pattern.match(line)
        if not m:
            continue
        routes.append((m.group(1).lower(), int(m.group(2)), [int(x) for x in m.group(3).split()]))
    return routes


def load_td_speed_model(instance_path, n):
    name = instance_path.name
    vmax = [[15.6464 for _ in range(n + 1)] for _ in range(n + 1)]
    theta = [[[1.0 for _ in range(n + 1)] for _ in range(n + 1)] for _ in range(12)]
    for l, default in enumerate(DEFAULT_SIGMA):
        for i in range(n + 1):
            for j in range(n + 1):
                theta[l][i][j] = default

    for line in (instance_path / f"{name}_solver.vmax_ij.txt").read_text(encoding="utf-8-sig").splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split()
        vmax[int(p[0])][int(p[1])] = float(p[2])

    for line in (instance_path / f"{name}_solver.theta_ijl.txt").read_text(encoding="utf-8-sig").splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split()
        i, j, l = int(p[0]), int(p[1]), int(p[2])
        if 0 <= l < len(theta):
            theta[l][i][j] = float(p[3])
    return vmax, theta


def distance_matrix(loc):
    n = len(loc) - 1
    dist = [[0.0 for _ in range(n + 1)] for _ in range(n + 1)]
    for i in range(n + 1):
        xi, yi = loc[i]
        for j in range(n + 1):
            xj, yj = loc[j]
            dist[i][j] = math.hypot(xi - xj, yi - yj)
    return dist


def truck_edge_time(u, v, start_sec, dist, vmax, theta):
    dist_left = dist[u][v]
    time = start_sec
    guard = 0
    while dist_left > 1e-8:
        guard += 1
        if guard > 1000000:
            seg = get_time_segment(time / 3600.0)
            time += dist_left / max(1e-8, vmax[u][v] * theta[seg][u][v])
            break
        seg = get_time_segment(time / 3600.0)
        speed = max(1e-8, vmax[u][v] * theta[seg][u][v])
        t_hr = time / 3600.0
        period_hr = TIME_SEGMENT[-1] - TIME_SEGMENT[0]
        local_t = math.fmod(t_hr - TIME_SEGMENT[0], period_hr)
        if local_t < 0:
            local_t += period_hr
        local_t += TIME_SEGMENT[0]
        time_to_boundary = max(0.0, (TIME_SEGMENT[seg + 1] - local_t) * 3600.0)
        if time_to_boundary <= 1e-9:
            time += 1e-6
            continue
        can_travel = speed * time_to_boundary
        if can_travel >= dist_left:
            time += dist_left / speed
            dist_left = 0.0
        else:
            dist_left -= can_travel
            time += time_to_boundary
    return time - start_sec


def merge_violation(target, source):
    for cust, amount in source.items():
        target[cust] = target.get(cust, 0.0) + amount


def compute_truck_route(route, dist, vmax, theta, serve_truck, deadline):
    time = 0.0
    visit_times = [0.0 for _ in deadline]
    customers_since_depot = []
    violations = {}
    for k in range(1, len(route)):
        u, v = route[k - 1], route[k]
        if u == v:
            continue
        time += truck_edge_time(u, v, time, dist, vmax, theta)
        if v != 0:
            time += serve_truck[v]
            customers_since_depot.append(v)
        visit_times[v] = time
        if v == 0 and k != 1:
            for cust in customers_since_depot:
                duration = time - visit_times[cust]
                if duration > deadline[cust] + 1e-8:
                    violations[cust] = violations.get(cust, 0.0) + duration - deadline[cust]
            for cust in customers_since_depot:
                visit_times[cust] = time
            customers_since_depot.clear()
    return violations


def compute_drone_route(route, dist, demand, serve_drone, deadline):
    time = 0.0
    current_weight = 0.0
    energy_used = 0.0
    capacity_bad = False
    energy_bad = False
    visit_times = [0.0 for _ in deadline]
    customers_since_depot = []
    violations = {}
    for k in range(1, len(route)):
        u, v = route[k - 1], route[k]
        if u == v:
            continue
        leg_time = dist[u][v] / V_FLY_DRONE + HEIGHT / V_TAKE_OFF + HEIGHT / V_LANDING
        energy_used += (POWER_BETA * current_weight + POWER_GAMMA) * leg_time
        if energy_used > DRONE_BATTERY_J + 1e-8:
            energy_bad = True
        time += leg_time
        if v != 0:
            current_weight += demand[v]
            if current_weight > DRONE_CAPACITY + 1e-8:
                capacity_bad = True
            time += serve_drone[v]
            customers_since_depot.append(v)
        else:
            for cust in customers_since_depot:
                duration = time - visit_times[cust]
                if duration > deadline[cust] + 1e-8:
                    violations[cust] = violations.get(cust, 0.0) + duration - deadline[cust]
            for cust in customers_since_depot:
                visit_times[cust] = time
            customers_since_depot.clear()
            current_weight = 0.0
            energy_used = 0.0
        visit_times[v] = time
    return violations, capacity_bad, energy_bad


def find_solution(instance_path, model):
    if model == "td":
        candidates = sorted(instance_path.glob("*solution_best.txt"))
        candidates = [p for p in candidates if "static" not in p.name]
    else:
        candidates = sorted(instance_path.glob("*static_solution_best.txt"))
    if not candidates:
        return None
    # Prefer labeled results for renamed configuration folders, otherwise the ordinary result.
    ordinary = instance_path / ("static_solution_best.txt" if model == "static" else "solution_best.txt")
    if ordinary.exists():
        return ordinary
    return candidates[0]


def validate_static_on_td(instance_path, static_solution):
    _, _, n, loc, demand, deadline, serve_drone, serve_truck = parse_instance(instance_path)
    dist = distance_matrix(loc)
    vmax, theta = load_td_speed_model(instance_path, n)
    wait_violations = {}
    capacity_bad = False
    energy_bad = False
    for vehicle_type, _, route in parse_routes(static_solution):
        if vehicle_type == "truck":
            merge_violation(wait_violations, compute_truck_route(route, dist, vmax, theta, serve_truck, deadline))
        else:
            violations, cap_bad, en_bad = compute_drone_route(route, dist, demand, serve_drone, deadline)
            merge_violation(wait_violations, violations)
            capacity_bad = capacity_bad or cap_bad
            energy_bad = energy_bad or en_bad
    wait_total_sec = sum(wait_violations.values())
    feasible = not wait_violations and not capacity_bad and not energy_bad
    return feasible, len(wait_violations), wait_total_sec, capacity_bad, energy_bad


def main():
    rows = []
    set_dirs = sorted(p for p in INSTANCE_ROOT.iterdir() if p.is_dir() and p.name.startswith("n"))
    for set_dir in set_dirs:
        for instance_path in sorted(p for p in set_dir.iterdir() if p.is_dir() and p.name.startswith("instance")):
            trucks, drones, _, _, _, _, _, _ = parse_instance(instance_path)
            td_solution = find_solution(instance_path, "td")
            static_solution = find_solution(instance_path, "static")
            if not td_solution or not static_solution:
                continue
            feasible, violated_count, wait_sec, cap_bad, energy_bad = validate_static_on_td(instance_path, static_solution)
            rows.append({
                "benchmark_set": set_dir.name,
                "instance": instance_path.name,
                "instance_full_name": f"{set_dir.name}/{instance_path.name}",
                "trucks_count": trucks,
                "drones_count": drones,
                "static_objective": f"{read_objective(static_solution):.6f}",
                "td_objective": f"{read_objective(td_solution):.6f}",
                "static_to_td_is_feasible": "YES" if feasible else "NO",
                "static_to_td_waiting_violated_customer_count": violated_count,
                "static_to_td_waiting_violation_total_sec": f"{wait_sec:.6f}",
                "static_to_td_waiting_violation_total_min": f"{wait_sec / 60.0:.6f}",
                "static_to_td_capacity_violation": "YES" if cap_bad else "NO",
                "static_to_td_energy_violation": "YES" if energy_bad else "NO",
                "td_solution_file": str(td_solution),
                "static_solution_file": str(static_solution),
            })

    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {OUT_CSV}")
    print(f"Rows: {len(rows)}")
    infeasible = sum(1 for r in rows if r["static_to_td_is_feasible"] == "NO")
    wait_count = sum(int(r["static_to_td_waiting_violated_customer_count"]) for r in rows)
    wait_sec = sum(float(r["static_to_td_waiting_violation_total_sec"]) for r in rows)
    print(f"Static-to-TD infeasible: {infeasible}/{len(rows)}")
    print(f"Total waiting violated customers: {wait_count}")
    print(f"Total waiting violation: {wait_sec:.6f} sec = {wait_sec / 60.0:.6f} min")


if __name__ == "__main__":
    main()

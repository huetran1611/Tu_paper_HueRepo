import json
import math
import random
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "instance_new"

MIN_NODE_DISTANCE_KM = 0.5
DEPOT = (0.0, 0.0)
SERVICE_TIME_SEC = 60
TRUCK_CAPACITY_KG = 150.0
DRONE_CAPACITY_KG = 5.0

BENCHMARK_SETS = [
    {"folder": "n50_1truck_1drone", "customers": 50, "trucks": 1, "drones": 1, "seed_base": 2026080501},
    {"folder": "n100_2truck_2drone", "customers": 100, "trucks": 2, "drones": 2, "seed_base": 2026080401},
    {"folder": "n200_3truck_3drone", "customers": 200, "trucks": 3, "drones": 3, "seed_base": 2026081501},
    {"folder": "n500_7truck_7drone", "customers": 500, "trucks": 7, "drones": 7, "seed_base": 2026082501},
]

ROAD_CLASSES = {
    "urban": {"max_radius_km": 4.0, "speed_kmh": 30.0},
    "suburban": {"max_radius_km": 7.0, "speed_kmh": 35.0},
    "arterial": {"max_radius_km": None, "speed_kmh": 40.0},
}

TRAFFIC_PROFILE = [
    {"time": "07:00-07:30", "urban": 0.65, "suburban": 0.72, "arterial": 0.80},
    {"time": "07:30-08:00", "urban": 0.50, "suburban": 0.60, "arterial": 0.70},
    {"time": "08:00-08:30", "urban": 0.40, "suburban": 0.50, "arterial": 0.60},
    {"time": "08:30-09:00", "urban": 0.45, "suburban": 0.55, "arterial": 0.65},
    {"time": "09:00-09:30", "urban": 0.60, "suburban": 0.68, "arterial": 0.75},
    {"time": "09:30-10:00", "urban": 0.70, "suburban": 0.78, "arterial": 0.82},
    {"time": "10:00-10:30", "urban": 0.80, "suburban": 0.86, "arterial": 0.90},
    {"time": "10:30-11:00", "urban": 0.85, "suburban": 0.90, "arterial": 0.95},
    {"time": "11:00-11:30", "urban": 0.80, "suburban": 0.86, "arterial": 0.90},
    {"time": "11:30-12:00", "urban": 0.70, "suburban": 0.78, "arterial": 0.82},
]


def dist_km(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def road_class(a, b):
    mx = (a[0] + b[0]) / 2.0
    my = (a[1] + b[1]) / 2.0
    r = math.hypot(mx, my)
    if r <= ROAD_CLASSES["urban"]["max_radius_km"]:
        return "urban"
    if r <= ROAD_CLASSES["suburban"]["max_radius_km"]:
        return "suburban"
    return "arterial"


def speed_mps(class_name):
    return ROAD_CLASSES[class_name]["speed_kmh"] * 1000.0 / 3600.0


def generate_coordinates(rng, customer_count):
    coords = [DEPOT]
    attempts = 0
    while len(coords) <= customer_count:
        attempts += 1
        if attempts > 5_000_000:
            raise RuntimeError(f"Unable to place {customer_count} customers with min distance {MIN_NODE_DISTANCE_KM} km")
        candidate = (rng.uniform(-10.0, 10.0), rng.uniform(-10.0, 10.0))
        if all(dist_km(candidate, existing) >= MIN_NODE_DISTANCE_KM for existing in coords):
            coords.append(candidate)
    return coords


def waiting_limits_by_distance(coords):
    customer_ids = list(range(1, len(coords)))
    customer_ids.sort(key=lambda i: dist_km(DEPOT, coords[i]))
    n = len(customer_ids)
    limits = {}
    for rank, customer_id in enumerate(customer_ids):
        if rank < n / 3.0:
            limits[customer_id] = 1800
        elif rank < 2.0 * n / 3.0:
            limits[customer_id] = 2700
        else:
            limits[customer_id] = 3600
    return limits


def build_instance(seed, customer_count):
    rng = random.Random(seed)
    coords = generate_coordinates(rng, customer_count)
    waiting_limits = waiting_limits_by_distance(coords)
    eligible_count = int(round(0.8 * customer_count))
    eligible_ids = set(rng.sample(range(1, customer_count + 1), eligible_count))

    customers = []
    for customer_id in range(1, customer_count + 1):
        is_drone = customer_id in eligible_ids
        demand = rng.uniform(0.5, 5.0) if is_drone else rng.uniform(5.1, 10.0)
        customers.append(
            {
                "id": customer_id,
                "x_km": coords[customer_id][0],
                "y_km": coords[customer_id][1],
                "dronable": 1 if is_drone else 0,
                "demand_kg": round(demand, 2),
                "drone_service_sec": SERVICE_TIME_SEC,
                "truck_service_sec": SERVICE_TIME_SEC,
                "deadline_sec": waiting_limits[customer_id],
            }
        )
    return coords, customers


def write_customers_file(path, customers):
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("id x_km y_km x_m y_m dronable demand_kg drone_service_sec truck_service_sec deadline_sec\n")
        for c in customers:
            f.write(
                f"{c['id']} {c['x_km']:.6f} {c['y_km']:.6f} "
                f"{c['x_km'] * 1000.0:.6f} {c['y_km'] * 1000.0:.6f} "
                f"{c['dronable']} {c['demand_kg']:.2f} "
                f"{c['drone_service_sec']} {c['truck_service_sec']} {c['deadline_sec']}\n"
            )


def write_solver_file(path, customers, trucks, drones):
    with path.open("w", encoding="ascii", newline="\n") as f:
        f.write(f"trucks_count {trucks}\n")
        f.write(f"drones_count {drones}\n")
        f.write(f"customers {len(customers)}\n")
        f.write("depot 0 0\n")
        f.write("Coordinate X         Coordinate Y         Dronable Demand\n")
        f.write("X\tY\tDronable\tDemand\tDrone_service\tTruck_service\tLw\n")
        for c in customers:
            f.write(
                f"{c['x_km'] * 1000.0:.6f}\t{c['y_km'] * 1000.0:.6f}\t"
                f"{c['dronable']}\t{c['demand_kg']:.2f}\t"
                f"{c['drone_service_sec']}\t{c['truck_service_sec']}\t{c['deadline_sec']}\n"
            )


def write_vmax_file(path, coords):
    with path.open("w", encoding="ascii", newline="\n") as f:
        f.write("# i j vmax_ij_m_per_s road_class free_flow_speed_kmh\n")
        for i in range(len(coords)):
            for j in range(len(coords)):
                if i == j:
                    continue
                klass = road_class(coords[i], coords[j])
                f.write(f"{i} {j} {speed_mps(klass):.6f} {klass} {ROAD_CLASSES[klass]['speed_kmh']:.1f}\n")


def write_theta_file(path, coords):
    with path.open("w", encoding="ascii", newline="\n") as f:
        f.write("# i j l theta_ijl interval road_class\n")
        for i in range(len(coords)):
            for j in range(len(coords)):
                if i == j:
                    continue
                klass = road_class(coords[i], coords[j])
                for l, profile in enumerate(TRAFFIC_PROFILE):
                    f.write(f"{i} {j} {l} {profile[klass]:.2f} {profile['time']} {klass}\n")


def write_metadata(path, seed, customers, trucks, drones):
    metadata = {
        "seed": seed,
        "depot": {"x_km": DEPOT[0], "y_km": DEPOT[1], "x_m": 0.0, "y_m": 0.0},
        "customers": len(customers),
        "trucks_count": trucks,
        "drones_count": drones,
        "truck_capacity_kg": TRUCK_CAPACITY_KG,
        "drone_capacity_kg": DRONE_CAPACITY_KG,
        "drone_parameters": {
            "horizontal_speed_kmh": 60.0,
            "takeoff_speed_kmh": 30.0,
            "landing_speed_kmh": 15.0,
            "battery_capacity_kwh": 1.59,
            "energy_model": "E=(gamma+beta*m)t",
            "gamma_kw": 0.397,
            "beta_kw_per_kg": 0.066,
        },
        "time_intervals": [profile["time"] for profile in TRAFFIC_PROFILE],
        "traffic_profile": TRAFFIC_PROFILE,
        "demand": {
            "drone_eligible_count": sum(c["dronable"] for c in customers),
            "truck_only_count": sum(1 - c["dronable"] for c in customers),
            "drone_eligible_uniform_kg": [0.5, 5.0],
            "truck_only_uniform_kg": [5.1, 10.0],
        },
        "waiting_time_groups_sec": {"nearest": 1800, "middle": 2700, "farthest": 3600},
        "notes": "Solver .txt stores coordinates in meters to match tabubu_TimeDependent_new_instance.cpp.",
    }
    path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def generate_set(spec):
    set_dir = OUT_DIR / spec["folder"]
    set_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for idx in range(1, 11):
        seed = spec["seed_base"] + idx - 1
        name = f"instance{idx:03d}"
        instance_dir = set_dir / name
        instance_dir.mkdir(exist_ok=True)
        coords, customers = build_instance(seed, spec["customers"])
        solver_stem = f"{name}_solver"

        write_metadata(instance_dir / "metadata.json", seed, customers, spec["trucks"], spec["drones"])
        write_customers_file(instance_dir / "customers.txt", customers)
        write_solver_file(instance_dir / f"{solver_stem}.txt", customers, spec["trucks"], spec["drones"])
        write_vmax_file(instance_dir / "v_ij.txt", coords)
        write_theta_file(instance_dir / "theta_ij.txt", coords)
        write_vmax_file(instance_dir / f"{solver_stem}.vmax_ij.txt", coords)
        write_theta_file(instance_dir / f"{solver_stem}.theta_ijl.txt", coords)
        manifest.append(
            {
                "instance": name,
                "seed": seed,
                "customers": spec["customers"],
                "trucks_count": spec["trucks"],
                "drones_count": spec["drones"],
                "solver_input": str((instance_dir / f"{solver_stem}.txt").relative_to(ROOT)),
                "vmax_file": str((instance_dir / f"{solver_stem}.vmax_ij.txt").relative_to(ROOT)),
                "theta_file": str((instance_dir / f"{solver_stem}.theta_ijl.txt").relative_to(ROOT)),
            }
        )
    (set_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return set_dir


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    generated = []
    for spec in BENCHMARK_SETS:
        generated.append(generate_set(spec))
    print("Generated benchmark sets:")
    for path in generated:
        print(path)


if __name__ == "__main__":
    main()

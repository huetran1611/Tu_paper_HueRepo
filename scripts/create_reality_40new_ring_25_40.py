#!/usr/bin/env python3
import csv
import json
import math
import random
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path("/Users/huetran/Tu_paper_HueRepo")
REALITY = ROOT / "instance_hanoi" / "reality"
TARGET_DIRS = [REALITY / "heavy"]
DEPOT_LAT = 20.977
DEPOT_LON = 105.845
MILE_M = 1609.344
INNER_SIDE_MILE = 25.0
OUTER_SIDE_MILE = 40.0
INNER_HALF_M = INNER_SIDE_MILE * MILE_M / 2.0
OUTER_HALF_M = OUTER_SIDE_MILE * MILE_M / 2.0
OSM_FRACTION = 0.25
USE_OSRM_FOR_OSM_EDGES = False
DATASETS = [(200, i) for i in range(1, 5)] + [(500, i) for i in range(1, 5)]
BASE_HEAVY_L = [0.6, 0.4, 0.5, 0.6, 0.7, 0.5, 0.6, 0.8, 0.7, 0.65, 0.4, 0.6]


def xy_from_latlon(lat, lon):
    y = (lat - DEPOT_LAT) * 111320.0
    x = (lon - DEPOT_LON) * 111320.0 * math.cos(math.radians(DEPOT_LAT))
    return x, y


def in_square(x, y, half_m):
    return -half_m <= x <= half_m and -half_m <= y <= half_m


def in_ring_25_40(x, y):
    return in_square(x, y, OUTER_HALF_M) and not in_square(x, y, INNER_HALF_M)


def point_key(row):
    return (round(float(row["lat_f"]), 7), round(float(row["lon_f"]), 7), str(row.get("name", "")))


def overpass_query():
    cache_path = REALITY / "overpass_25_40mile_ring_cache.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    lat_half = OUTER_HALF_M / 111320.0
    lon_half = OUTER_HALF_M / (111320.0 * math.cos(math.radians(DEPOT_LAT)))
    south = DEPOT_LAT - lat_half
    north = DEPOT_LAT + lat_half
    west = DEPOT_LON - lon_half
    east = DEPOT_LON + lon_half
    bbox = f"{south},{west},{north},{east}"
    query = f"""
[out:json][timeout:180];
(
  node["amenity"]({bbox});
  node["shop"]({bbox});
  node["office"]({bbox});
  node["tourism"]({bbox});
  node["leisure"]({bbox});
  node["healthcare"]({bbox});
  node["public_transport"]({bbox});
  way["amenity"]({bbox});
  way["shop"]({bbox});
  way["office"]({bbox});
  way["tourism"]({bbox});
  way["leisure"]({bbox});
  way["healthcare"]({bbox});
  way["public_transport"]({bbox});
);
out center tags 8000;
"""
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    urls = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
    ]
    last_error = None
    for url in urls:
        for attempt in range(3):
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "Tu_paper_HueRepo/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=240) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                return payload
            except Exception as exc:
                last_error = exc
                time.sleep(5.0 * (attempt + 1))
    raise last_error


def osm_type(tags):
    for key in ("amenity", "shop", "office", "tourism", "leisure", "healthcare", "public_transport"):
        if tags.get(key):
            return f"{key}:{tags[key]}"
    return "osm_poi"


def build_osm_pool(existing_keys):
    seen = set(existing_keys)
    candidates = []
    for el in overpass_query().get("elements", []):
        tags = el.get("tags", {}) or {}
        lat = el.get("lat")
        lon = el.get("lon")
        if lat is None or lon is None:
            center = el.get("center") or {}
            lat = center.get("lat")
            lon = center.get("lon")
        if lat is None or lon is None:
            continue
        lat = float(lat)
        lon = float(lon)
        x, y = xy_from_latlon(lat, lon)
        if not in_ring_25_40(x, y):
            continue
        name = tags.get("name") or tags.get("brand") or tags.get("operator") or "Không có tên"
        key = (round(lat, 7), round(lon, 7), name)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "name": name,
            "zone": "",
            "type": osm_type(tags),
            "district": tags.get("addr:district", ""),
            "lat_f": lat,
            "lon_f": lon,
            "x": x,
            "y": y,
            "source_dataset": "openstreetmap_25_40_ring",
            "old_id": f"{el.get('type')}:{el.get('id')}",
            "osm_tags": tags,
        })
    candidates.sort(key=lambda r: (math.hypot(r["x"], r["y"]), r["name"], r["old_id"]))
    return candidates


def read_nodes(path):
    rows = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["id"] == "0":
                depot = dict(row)
                continue
            row = dict(row)
            row["old_new_id"] = int(row["id"])
            row["old_id"] = row.get("original_id", row["id"])
            row["lat_f"] = float(row["lat"])
            row["lon_f"] = float(row["lon"])
            row["x"] = float(row["x_m"])
            row["y"] = float(row["y_m"])
            rows.append(row)
    return depot, rows


def read_txt_attrs(path):
    attrs = {}
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines()[6:], start=1):
        parts = line.split()
        if len(parts) >= 7:
            attrs[idx] = parts[2:7]
    return attrs


def read_matrix(path, value_index=2):
    values = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            values[(int(parts[0]), int(parts[1]))] = float(parts[value_index])
    return values


def read_theta(path):
    values = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            i, j, l, val = s.split()
            values[(int(i), int(j), int(l))] = float(val)
    return values


def empirical_vmax(path):
    vals = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                val = float(s.split()[2])
                if val > 0:
                    vals.append(val)
    return vals or [35.0 / 3.6]


def estimate_road_factor(existing_rows, old_truck):
    ratios = []
    rows = existing_rows[:]
    for i, a in enumerate(rows):
        ai = a["old_new_id"]
        for b in rows[i + 1:i + 31]:
            bi = b["old_new_id"]
            euclid = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
            if euclid <= 1.0:
                continue
            ratios.append(old_truck[(ai, bi)] / euclid)
            ratios.append(old_truck[(bi, ai)] / euclid)
    usable = [r for r in ratios if 1.0 <= r <= 4.0]
    return statistics.median(usable) if usable else 1.35


def osrm_table_partial(coords_latlon, osm_indices):
    n = len(coords_latlon)
    matrix = [[None for _ in range(n)] for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 0.0
    block = 50
    osm_set = set(osm_indices)
    for si in range(0, n, block):
        src_idx = list(range(si, min(si + block, n)))
        for di in range(0, n, block):
            dst_idx = list(range(di, min(di + block, n)))
            if not (osm_set.intersection(src_idx) or osm_set.intersection(dst_idx)):
                continue
            block_coords = [coords_latlon[i] for i in src_idx] + [coords_latlon[j] for j in dst_idx]
            coord_str = ";".join(f"{lon:.7f},{lat:.7f}" for lat, lon in block_coords)
            sources = ";".join(str(i) for i in range(len(src_idx)))
            destinations = ";".join(str(len(src_idx) + i) for i in range(len(dst_idx)))
            params = urllib.parse.urlencode({
                "sources": sources,
                "destinations": destinations,
                "annotations": "distance",
            })
            url = f"http://router.project-osrm.org/table/v1/driving/{coord_str}?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "Tu_paper_HueRepo/1.0"})
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        payload = json.loads(resp.read().decode("utf-8"))
                    if payload.get("code") != "Ok":
                        raise RuntimeError(payload)
                    for a, old_i in enumerate(src_idx):
                        for b, old_j in enumerate(dst_idx):
                            val = payload["distances"][a][b]
                            matrix[old_i][old_j] = 0.0 if val is None else float(val)
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(2.0 * (attempt + 1))
            time.sleep(0.2)
    return matrix


def make_selected(existing_rows, osm_pool, n, seed):
    rng = random.Random(seed)
    osm_count = int(round(n * OSM_FRACTION))
    keep_count = n - osm_count
    kept = rng.sample(existing_rows, keep_count)
    if len(osm_pool) < osm_count:
        raise RuntimeError(f"Need {osm_count} OSM ring points, got {len(osm_pool)}")
    osm_selected = rng.sample(osm_pool, osm_count)
    selected = [{"kind": "existing", **r} for r in kept] + [{"kind": "osm", **r} for r in osm_selected]
    rng.shuffle(selected)
    return selected, keep_count, osm_count


def write_dataset(target_dir, n, m, osm_pool):
    old_suffix = f"{n}.40.{m}"
    new_suffix = f"{n}.40new.{m}"
    seed = n * 1000 + 400 + m
    depot, existing_rows = read_nodes(target_dir / f"reality.{old_suffix}.nodes.csv")
    selected, keep_count, osm_count = make_selected(existing_rows, osm_pool, n, seed)

    txt_attrs = read_txt_attrs(target_dir / f"reality.{old_suffix}.txt")
    old_truck = read_matrix(target_dir / f"reality.{old_suffix}.truck_distance_m.txt")
    old_vmax = read_matrix(target_dir / f"reality.{old_suffix}.vmax_ij.txt")
    old_theta = read_theta(target_dir / f"reality.{old_suffix}.theta_ijl.txt")
    vmax_samples = empirical_vmax(target_dir / f"reality.{old_suffix}.vmax_ij.txt")
    rng = random.Random(seed + 20260705)
    theta_rng = random.Random(seed + 20260706)

    coords_xy = [(0.0, 0.0)] + [(p["x"], p["y"]) for p in selected]
    coords_latlon = [(DEPOT_LAT, DEPOT_LON)] + [(p["lat_f"], p["lon_f"]) for p in selected]
    osm_indices = [idx for idx, p in enumerate(selected, start=1) if p["kind"] == "osm"]
    osrm_partial = osrm_table_partial(coords_latlon, osm_indices) if USE_OSRM_FOR_OSM_EDGES else None
    road_factor = estimate_road_factor(existing_rows, old_truck)

    def old_id(new_index):
        if new_index == 0:
            return 0
        p = selected[new_index - 1]
        return p["old_new_id"] if p["kind"] == "existing" else None

    def truck_distance(i, j):
        oi = old_id(i)
        oj = old_id(j)
        if oi is not None and oj is not None:
            return old_truck[(oi, oj)]
        val = osrm_partial[i][j] if osrm_partial is not None else None
        if val is None:
            xi, yi = coords_xy[i]
            xj, yj = coords_xy[j]
            return math.hypot(xi - xj, yi - yj) * road_factor
        return val

    truck = [[truck_distance(i, j) for j in range(n + 1)] for i in range(n + 1)]

    attrs = []
    for p in selected:
        if p["kind"] == "existing":
            attrs.append(txt_attrs[p["old_new_id"]])
        else:
            attrs.append(["1", f"{rng.uniform(0.25, 2.00):.2f}", "60", "60", str(rng.choice([1800, 2700, 3600]))])

    with (target_dir / f"reality.{new_suffix}.txt").open("w", encoding="utf-8", newline="") as f:
        header = (target_dir / f"reality.{old_suffix}.txt").read_text(encoding="utf-8").splitlines()[:6]
        header[2] = f"customers {n}"
        f.write("\n".join(header) + "\n")
        for (x, y), row_attr in zip(coords_xy[1:], attrs):
            f.write(f"{x:.6f}\t{y:.6f}\t" + "\t".join(row_attr) + "\n")

    with (target_dir / f"reality.{new_suffix}.truck_distance_m.txt").open("w", encoding="utf-8", newline="") as f:
        f.write(f"# i j distance_m; {new_suffix} keeps {keep_count} points from {old_suffix} and adds {osm_count} OSM points in 25-40 mile ring\n")
        for i in range(n + 1):
            for j in range(n + 1):
                f.write(f"{i} {j} {truck[i][j]:.3f}\n")

    with (target_dir / f"reality.{new_suffix}.drone_distance_m.txt").open("w", encoding="utf-8", newline="") as f:
        f.write(f"# i j distance_m; Euclidean drone distance matrix computed from reality.{new_suffix}.txt coordinates\n")
        for i, (xi, yi) in enumerate(coords_xy):
            for j, (xj, yj) in enumerate(coords_xy):
                f.write(f"{i} {j} {math.hypot(xi - xj, yi - yj):.6f}\n")

    with (target_dir / f"reality.{new_suffix}.vmax_ij.txt").open("w", encoding="utf-8", newline="") as f:
        f.write(f"# i j vmax_ij_m_per_s; existing edges sliced from {old_suffix}, OSM edges sampled empirically\n")
        for i in range(n + 1):
            for j in range(n + 1):
                oi = old_id(i)
                oj = old_id(j)
                if i == j:
                    val = 0.0
                elif oi is not None and oj is not None:
                    val = old_vmax[(oi, oj)]
                else:
                    val = vmax_samples[rng.randrange(len(vmax_samples))]
                f.write(f"{i} {j} {val:.6f}\n")

    with (target_dir / f"reality.{new_suffix}.theta_ijl.txt").open("w", encoding="utf-8", newline="") as f:
        f.write(f"# i j l theta_ijl; existing edges sliced from {old_suffix}, OSM edges generated with heavy base_L\n")
        for i in range(n + 1):
            for j in range(n + 1):
                oi = old_id(i)
                oj = old_id(j)
                for l in range(12):
                    if oi is not None and oj is not None:
                        val = old_theta[(oi, oj, l)]
                    else:
                        val = round(theta_rng.uniform(0.9, 1.0) * BASE_HEAVY_L[l], 2)
                    f.write(f"{i} {j} {l} {val:.2f}\n")

    with (target_dir / f"reality.{old_suffix}.json").open(encoding="utf-8") as f:
        old_json = json.load(f)
    old_customers = {int(c["id"]): c for c in old_json.get("customers", [])}
    customers = []
    fieldnames = [
        "id", "name", "zone", "type", "district", "lat", "lon", "x_m", "y_m",
        "dronable", "demand", "drone_service_s", "truck_service_s", "lw_s",
        "depot_osrm_distance_m", "to_depot_osrm_distance_m", "road_reachable",
        "osrm_snap_distance_m", "source_dataset", "original_id", "inside_40mile_square",
        "outside_25mile_square",
    ]
    with (target_dir / f"reality.{new_suffix}.nodes.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({
            "id": 0, "name": "depot", "zone": "", "type": "depot",
            "district": depot.get("district", ""), "lat": DEPOT_LAT, "lon": DEPOT_LON,
            "x_m": "0.000000", "y_m": "0.000000", "source_dataset": "depot",
            "original_id": 0, "inside_40mile_square": "", "outside_25mile_square": "",
        })
        for new_id, p in enumerate(selected, start=1):
            row_attr = attrs[new_id - 1]
            in_outer = in_square(p["x"], p["y"], OUTER_HALF_M)
            outside_inner = not in_square(p["x"], p["y"], INNER_HALF_M)
            rec = {
                "id": new_id,
                "name": p.get("name", ""),
                "zone": p.get("zone", ""),
                "type": p.get("type", ""),
                "district": p.get("district", ""),
                "lat": f"{p['lat_f']:.7f}",
                "lon": f"{p['lon_f']:.7f}",
                "x_m": f"{p['x']:.6f}",
                "y_m": f"{p['y']:.6f}",
                "dronable": row_attr[0],
                "demand": row_attr[1],
                "drone_service_s": row_attr[2],
                "truck_service_s": row_attr[3],
                "lw_s": row_attr[4],
                "depot_osrm_distance_m": f"{truck[0][new_id]:.3f}",
                "to_depot_osrm_distance_m": f"{truck[new_id][0]:.3f}",
                "road_reachable": p.get("road_reachable", "true"),
                "osrm_snap_distance_m": p.get("osrm_snap_distance_m", ""),
                "source_dataset": p["source_dataset"],
                "original_id": p["old_id"],
                "inside_40mile_square": str(in_outer).lower(),
                "outside_25mile_square": str(outside_inner).lower(),
            }
            writer.writerow(rec)
            if p["kind"] == "existing":
                cust = dict(old_customers[p["old_new_id"]])
            else:
                cust = {
                    "name": p.get("name", ""),
                    "type": p.get("type", ""),
                    "district": p.get("district", ""),
                    "lat": p["lat_f"],
                    "lon": p["lon_f"],
                    "osm_tags": p.get("osm_tags", {}),
                }
            cust.update({
                "id": new_id,
                "original_id": p["old_id"],
                "source_dataset": p["source_dataset"],
                "x_m": p["x"],
                "y_m": p["y"],
                "inside_40mile_square": in_outer,
                "outside_25mile_square": outside_inner,
                "depot_osrm_distance_m": truck[0][new_id],
                "to_depot_osrm_distance_m": truck[new_id][0],
            })
            customers.append(cust)

    data = dict(old_json)
    data["meta"] = dict(old_json.get("meta", {}))
    data["meta"].update({
        "description": f"{n}-customer 40new dataset: 75% kept from {old_suffix}, 25% OSM points in 25x25 to 40x40 mile ring",
        "version": new_suffix,
        "source_reality_dataset": f"reality.{old_suffix}",
        "source_reality_dir": str(target_dir),
        "selection_rule": "Remove 1/4 customers from the current 40-mile dataset and replace them with OSM POIs inside the 40x40 mile square but outside the 25x25 mile square.",
        "square_side_mile": OUTER_SIDE_MILE,
        "inner_exclusion_square_side_mile": INNER_SIDE_MILE,
        "square_bounds_xy_m": {"x_min": -OUTER_HALF_M, "x_max": OUTER_HALF_M, "y_min": -OUTER_HALF_M, "y_max": OUTER_HALF_M},
        "inner_exclusion_bounds_xy_m": {"x_min": -INNER_HALF_M, "x_max": INNER_HALF_M, "y_min": -INNER_HALF_M, "y_max": INNER_HALF_M},
        "random_seed": seed,
        "total_customers": n,
        "kept_customers_from_current_40": keep_count,
        "osm_ring_customers": osm_count,
        "synthetic_customers": 0,
        "truck_distance_rule": (
            "Existing-existing edges are sliced from the current 40-mile matrix; any edge involving an OSM point "
            "uses OSRM table when USE_OSRM_FOR_OSM_EDGES=true, otherwise Euclidean distance multiplied by the "
            "median road/euclidean factor from the current 40-mile matrix."
        ),
        "truck_distance_osm_edges_use_osrm": USE_OSRM_FOR_OSM_EDGES,
        "truck_distance_osm_edges_road_factor": road_factor,
        "vmax_rule": "Existing-existing edges are sliced from the current 40-mile file; any edge involving an OSM point is sampled from the current 40-mile empirical vmax distribution.",
        "theta_rule": "Existing-existing edges are sliced from the current 40-mile file; any edge involving an OSM point uses heavy theta=round(Uniform(0.9,1.0)*base_L[l],2).",
    })
    data["customers"] = customers
    with (target_dir / f"reality.{new_suffix}.json").open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    ring_count = sum(1 for p in selected if p["kind"] == "osm")
    max_radius = max(math.hypot(x, y) for x, y in coords_xy[1:])
    return {
        "dataset": new_suffix,
        "customers": n,
        "kept": keep_count,
        "osm_ring": ring_count,
        "truck_depot_mean": statistics.mean(truck[0][1:]),
        "max_radius_mile": max_radius / MILE_M,
    }


def main():
    existing_keys = set()
    for target_dir in TARGET_DIRS:
        for n, m in DATASETS:
            _, rows = read_nodes(target_dir / f"reality.{n}.40.{m}.nodes.csv")
            for row in rows:
                existing_keys.add(point_key(row))
    osm_pool = build_osm_pool(existing_keys)
    print(f"OSM ring candidates between {INNER_SIDE_MILE:g}x{INNER_SIDE_MILE:g} and {OUTER_SIDE_MILE:g}x{OUTER_SIDE_MILE:g} miles: {len(osm_pool)}")
    needed_per_dataset = max(int(round(n * OSM_FRACTION)) for n, _ in DATASETS)
    if len(osm_pool) < needed_per_dataset:
        raise RuntimeError(f"Need {needed_per_dataset} OSM ring points per dataset, got {len(osm_pool)}")

    for target_dir in TARGET_DIRS:
        for n, m in DATASETS:
            summary = write_dataset(target_dir, n, m, osm_pool)
            print(
                f"created {target_dir.name}/reality.{summary['dataset']}: "
                f"kept={summary['kept']}, osm_ring={summary['osm_ring']}, "
                f"max_radius={summary['max_radius_mile']:.2f} miles"
            )


if __name__ == "__main__":
    main()

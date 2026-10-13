import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

root = Path.cwd()
solver_dir = root / "build" / "time_dependent_trip_capacity"
out_root = root / "td_trip_capacity_results"
tasks = json.loads(os.environ["TASKS_JSON"])
time_limit = os.environ.get("TIME_LIMIT_SEC", "7200")
failed_processes = 0

def solver_path(mode, drone_capacity):
    suffix = drone_capacity.replace(".", "")
    return solver_dir / f"tabubu_{mode}_dd{suffix}"

def parse_routes(text, section_name):
    lines = text.splitlines()
    routes = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == f"{section_name} Routes:":
            in_section = True
            continue
        if in_section and stripped.endswith("Routes:") and stripped != f"{section_name} Routes:":
            break
        if in_section and stripped.startswith(section_name):
            route = stripped.split(":", 1)[1].split("|", 1)[0].strip()
            if route:
                routes.append(route)
    return routes

def parse_result_text(text, number_of_customer):
    improved = ""
    solution_status = "UNKNOWN"
    m = re.search(r"Improved solution cost:\s*([0-9.eE+-]+)", text)
    if m:
        improved = m.group(1)
    m = re.search(r"Final solution feasibility:\s*(\S+)", text)
    if m:
        solution_status = m.group(1).upper()
    truck_routes = parse_routes(text, "Truck")
    drone_routes = parse_routes(text, "Drone")
    drone_customers = set()
    for route in drone_routes:
        for token in route.split():
            try:
                customer = int(token)
            except ValueError:
                continue
            if customer != 0:
                drone_customers.add(customer)
    drone_ratio = len(drone_customers) / number_of_customer if number_of_customer else 0.0
    return {
        "best_objective": improved,
        "solution_status": solution_status,
        "truck_route": " || ".join(truck_routes),
        "drone_route": " || ".join(drone_routes),
        "drone_ratio": f"{drone_ratio:.6f}",
    }

for task in tasks:
    base = task["base"]
    mode = task["mode"]
    dd = task["drone_capacity"]
    run_id = str(task["run"])
    run_dir = out_root / f"{base}" / mode / f"Dd{dd}" / f"run{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    solver = solver_path(mode, dd)
    required_paths = [
        root / task["file"],
        root / task["vmax_file"],
        root / task["theta_file"],
        solver,
    ]
    missing_paths = [str(path) for path in required_paths if not path.exists()]
    command = [
        str(solver),
        str(root / task["file"]),
        f"--truck-vmax-file={root / task['vmax_file']}",
        f"--truck-theta-file={root / task['theta_file']}",
        "--attempts=1",
        f"--time-limit={time_limit}",
    ]

    start = time.time()
    process_status = "success"
    stdout = ""
    stderr = ""
    return_code = 0
    if missing_paths:
        return_code = 2
        process_status = "missing_input"
        stderr = "Missing required input paths:\n" + "\n".join(missing_paths)
    else:
        try:
            completed = subprocess.run(
                command,
                cwd=run_dir,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=int(float(time_limit)) + 300,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            return_code = completed.returncode
            if return_code != 0:
                process_status = "failed"
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            return_code = 124
            process_status = "timeout"

    elapsed = time.time() - start
    raw_output = stdout + "\n--- STDERR ---\n" + stderr
    (run_dir / "solver_stdout_stderr.txt").write_text(raw_output, encoding="utf-8")

    solution_file = run_dir / "output_solution_best.txt"
    if solution_file.exists():
        solution_text = solution_file.read_text(encoding="utf-8", errors="replace")
    else:
        solution_text = raw_output

    parsed = parse_result_text(solution_text, int(task["number_of_customer"]))
    result = {
        "instance": base,
        "mode": mode,
        "drone_capacity": dd,
        "gridsize": task["gridsize"],
        "number_of_customer": task["number_of_customer"],
        "run": task["run"],
        "truck_route": parsed["truck_route"],
        "drone_route": parsed["drone_route"],
        "drone_ratio": parsed["drone_ratio"],
        "best_objective": parsed["best_objective"],
        "solution_status": parsed["solution_status"],
        "process_status": process_status,
        "return_code": return_code,
        "elapsed_seconds": f"{elapsed:.3f}",
    }
    (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if solution_file.exists():
        shutil.copy2(solution_file, run_dir / "solution_best.txt")
    print(f"{base} | {mode} | Dd={dd} | run={run_id} | {process_status} | {parsed['solution_status']} | {parsed['best_objective']}")
    if process_status != "success":
        failed_processes += 1

if failed_processes:
    raise SystemExit(f"{failed_processes} solver process(es) failed in this job")

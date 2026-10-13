import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

root = Path.cwd()
solver = root / "build" / "instance_vehicle_sweep" / "tabubu_TimeDependent"
out_root = root / "instance_vehicle_sweep_results"
tasks = json.loads(os.environ["TASKS_JSON"])
time_limit = os.environ.get("TIME_LIMIT_SEC", "7200")

def patched_instance(source_path, destination_path, trucks, drones):
    text = source_path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"^trucks_count\s+\d+", f"trucks_count {trucks}", text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^drones_count\s+\d+", f"drones_count {drones}", text, count=1, flags=re.MULTILINE)
    destination_path.write_text(text, encoding="utf-8")

for task in tasks:
    base = task["base"]
    variant = task["variant"]
    run_id = str(task["run"])
    trucks = int(task["trucks"])
    drones = int(task["drones"])
    result_dir = out_root / str(task["customer_count"]) / base / variant / f"run{run_id}"
    result_dir.mkdir(parents=True, exist_ok=True)
    log_path = result_dir / "run.log"
    result_path = result_dir / f"{base}.{variant}.run{run_id}.txt"

    with tempfile.TemporaryDirectory(prefix=f"instance-{base}-{variant}-run{run_id}-") as tmp:
        tmp_path = Path(tmp)
        patched_input = tmp_path / f"{base}.{variant}.txt"
        patched_instance(root / task["file"], patched_input, trucks, drones)

        cmd = [
            str(solver),
            str(patched_input),
            f"--truck-vmax-file={root / task['vmax_file']}",
            f"--truck-theta-file={root / task['theta_file']}",
            "--attempts=1",
        ]
        if time_limit:
            cmd.append(f"--time-limit={time_limit}")

        with log_path.open("w", encoding="utf-8") as log:
            log.write("Command: " + " ".join(cmd) + "\n")
            log.write(f"Instance: {base}\n")
            log.write(f"Customer count: {task['customer_count']}\n")
            log.write(f"Variant: {variant}\n")
            log.write(f"Trucks: {trucks}\n")
            log.write(f"Drones: {drones}\n")
            log.write(f"Run: {run_id}\n\n")
            completed = subprocess.run(
                cmd,
                cwd=tmp_path,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )

        best_file = tmp_path / "output_solution_best.txt"
        with result_path.open("w", encoding="utf-8") as result:
            result.write(f"Instance: {base}\n")
            result.write(f"Customer count: {task['customer_count']}\n")
            result.write(f"Variant: {variant}\n")
            result.write(f"Trucks: {trucks}\n")
            result.write(f"Drones: {drones}\n")
            result.write(f"Run: {run_id}\n")
            result.write(f"Solver exit code: {completed.returncode}\n")
            result.write(f"Input file: {task['file']}\n\n")
            result.write(f"Truck vmax file: {task['vmax_file']}\n")
            result.write(f"Truck theta file: {task['theta_file']}\n\n")
            if best_file.exists():
                result.write(best_file.read_text(encoding="utf-8", errors="replace"))
            else:
                result.write("Final solution feasibility: FAILED\n")

        for filename in ("output_solution_best.txt", "output_mode_0.txt"):
            src = tmp_path / filename
            if src.exists():
                shutil.copy2(src, result_dir / f"{base}.{variant}.run{run_id}.{filename}")

        if completed.returncode != 0:
            raise SystemExit(
                f"Task failed: {base}, {variant}, run={run_id}; see {log_path}"
            )

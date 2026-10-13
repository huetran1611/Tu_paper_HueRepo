import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

root = Path.cwd()
solver = root / "build" / "big_instance_dd227" / "tabubu_big_instance_dd227"
out_root = root / "big_instance_dd227_results"
tasks = json.loads(os.environ["TASKS_JSON"])
time_limit = os.environ.get("TIME_LIMIT_SEC", "7200")

if not tasks:
    print("No task assigned to this bucket.")
    raise SystemExit(0)

for task in tasks:
    scenario = task["scenario"]
    base = task["base"]
    run_id = str(task["run"])
    result_dir = out_root / scenario / base / f"run{run_id}"
    result_dir.mkdir(parents=True, exist_ok=True)
    log_path = result_dir / "run.log"
    result_path = result_dir / f"{scenario}.{base}.Dd227.run{run_id}.txt"

    cmd = [
        str(solver),
        str(root / task["file"]),
        f"--truck-vmax-file={root / task['vmax_file']}",
        f"--truck-theta-file={root / task['theta_file']}",
        "--attempts=1",
    ]
    if time_limit:
        cmd.append(f"--time-limit={time_limit}")

    with tempfile.TemporaryDirectory(prefix=f"big-{scenario}-{base}-run{run_id}-") as tmp:
        tmp_path = Path(tmp)
        with log_path.open("w", encoding="utf-8") as log:
            log.write("Command: " + " ".join(cmd) + "\n")
            log.write(f"Scenario: {scenario}\n")
            log.write(f"Instance: {base}\n")
            log.write("Drone capacity kg: 2.27\n")
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
            result.write(f"Scenario: {scenario}\n")
            result.write(f"Instance: {base}\n")
            result.write("Drone capacity kg: 2.27\n")
            result.write(f"Run: {run_id}\n")
            result.write(f"Solver exit code: {completed.returncode}\n")
            result.write(f"Input file: {task['file']}\n")
            result.write(f"Truck vmax file: {task['vmax_file']}\n")
            result.write(f"Truck theta file: {task['theta_file']}\n\n")
            if best_file.exists():
                result.write(best_file.read_text(encoding="utf-8", errors="replace"))
            else:
                result.write("Final solution feasibility: FAILED\n")

        for filename in ("output_solution_best.txt", "output_mode_0.txt"):
            src = tmp_path / filename
            if src.exists():
                shutil.copy2(src, result_dir / f"{scenario}.{base}.Dd227.run{run_id}.{filename}")

        if completed.returncode != 0:
            raise SystemExit(
                f"Task failed: {scenario}/{base}, Dd=2.27, run={run_id}; see {log_path}"
            )

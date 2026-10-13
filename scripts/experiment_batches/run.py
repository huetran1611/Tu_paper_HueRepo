import json
import os
import subprocess
import sys
from pathlib import Path

from plan import RESULT_DIRS, SCRIPT_DIR


task = json.loads(os.environ["TASK_JSON"])
kind = task["experiment"]
result_dir = Path(RESULT_DIRS[kind])
result_dir.mkdir(parents=True, exist_ok=True)
environment = dict(os.environ, TASKS_JSON=json.dumps([task]))
completed = subprocess.run([sys.executable, str(SCRIPT_DIR / f"{kind}_run.py")],
                           env=environment)
manifest = {
    "task": task,
    "batch": int(os.environ["BATCH_NUMBER"]),
    "plan_hash": os.environ["PLAN_HASH"],
    "commit": os.environ["GITHUB_SHA"],
    "time_limit_sec": os.environ["TIME_LIMIT_SEC"],
    "return_code": completed.returncode,
}
(result_dir / "task_manifest.json").write_text(
    json.dumps(manifest, indent=2), encoding="utf-8")
sys.exit(completed.returncode)

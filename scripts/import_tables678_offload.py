"""Validate GitHub artifacts and add delegated results to the v4 campaign."""

import argparse
from collections import Counter
from pathlib import Path
from shutil import copytree

import paper_tables_678 as design
import run_tables678_local as local


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("run", type=Path)
    cli.add_argument("downloads", type=Path, nargs="+")
    cli.add_argument("--plan", default="delegation_plan.json")
    cli.add_argument("--runs", default="github-runs.json")
    args = cli.parse_args()
    run = args.run.resolve()
    manifest = local.load(run / "bundle")
    delegation = design.read(run / args.plan)
    expected = {task["key"]: task for batch in delegation["batches"] for task in batch}
    workflow_runs = design.read(run / args.runs)["runs"]
    job_runs = {task["key"]: str(workflow_runs[str(i)]) for i, batch in enumerate(delegation["batches"], 1)
                for task in batch}
    if (delegation["protocol_id"] != manifest["protocol_id"]
            or len(expected) != sum(map(len, delegation["batches"]))
            or len(delegation["batches"]) != len(workflow_runs)):
        raise ValueError("Unexpected delegation plan")

    counts = Counter()
    for downloaded in args.downloads:
        for path in downloaded.rglob("*"):
            if path.name not in {"completed.json", "outcome.json"}:
                continue
            item = design.read(path)
            key = item["task"]["key"]
            if (item["protocol_id"] != manifest["protocol_id"] or item["task"] != expected.get(key)
                    or item["execution_host"] != "github-actions-windows"
                    or item["github_run_id"] != job_runs[key]):
                raise ValueError(f"Unexpected GitHub record: {path}")
            if path.name == "completed.json":
                result = item["solver_result"]
                task = expected[key]
                if (not result["feasible"] or result["seed"] != task["seed"]
                        or result["iteration_budget"] != task["iterations"]
                        or result["process_time_limit_seconds"] != local.process_cap(manifest, task) - 2
                        or any(result["parameters"][name] != value for name, value in manifest["parameters"].items())):
                    raise ValueError(f"Unexpected solver result: {path}")
            dest = run / "results" / key / path.name
            if dest.exists():
                if design.read(dest) != item:
                    raise ValueError(f"Conflicting result for {key}")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if "attempt" in item:
                relative = Path(item["attempt"])
                if relative.parent != Path("results") / key or not relative.name.startswith("a-"):
                    raise ValueError(f"Unexpected attempt path: {relative}")
                source_attempt = path.parent / relative.name
                if source_attempt.is_dir():
                    copytree(source_attempt, run / relative, dirs_exist_ok=True)
            design.save(dest, item)
            counts[path.name] += 1
    print(dict(counts))


if __name__ == "__main__":
    main()

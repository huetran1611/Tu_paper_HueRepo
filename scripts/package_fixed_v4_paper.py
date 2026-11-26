"""Prepare the input, code, and result files for the fixed-v4_paper branch."""

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "experiments/tables678_irace_v4/full_20261123"
IRACE = ROOT / "experiments/irace_v4/runs/ordinal3000_n100_20261121"
TI = ROOT / "experiments/v4_table8_paper_20261126"
OUT = ROOT / "experiments/fixed_v4_paper_release"
VN = timezone(timedelta(hours=7))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def write_csv(path, headers, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


def sheet(book, name, headers, rows):
    page = book.create_sheet(name)
    page.append(headers)
    for row in rows:
        page.append(row)
    page.freeze_panes = "B2"
    page.auto_filter.ref = page.dimensions
    for cell in page[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="214F67")
    for i, header in enumerate(headers, 1):
        page.column_dimensions[get_column_letter(i)].width = min(72, max(16, len(header) + 4))
        if header.endswith("_pct"):
            for (cell,) in page.iter_rows(min_row=2, min_col=i, max_col=i):
                cell.number_format = '0.000"%"'
    return page


def typed(k, value):
    if not value:
        return None
    if k == "feasible":
        return value.lower() == "true"
    if (k in {"customers", "seed", "seeds", "instances", "runs", "feasible_runs", "iteration_budget"}
            or k.endswith(("_iterations", "_calls", "_pairs", "_instances", "_runs"))) and value.isdigit():
        return int(value)
    if k.endswith(("_seconds", "_pct", "_iterations")) or k in {"makespan", "f_mk", "f_wl", "reporting_tau"}:
        return float(value)
    return value


def github_runs():
    mapping = {}
    for path in sorted(RUN.glob("github-runs*.json")):
        meta = read_json(path)
        for batch, run_id in meta["runs"].items():
            key = str(run_id)
            assert key not in mapping
            mapping[key] = (meta["repository"], meta["branch"], meta["commit"], path.stem, batch)
    return mapping


def table678():
    mapping = github_runs()
    rows = []
    batches = Counter()
    manifest = read_json(RUN / "bundle/manifest.json")
    for task in manifest["tasks"]:
        record = read_json(RUN / "results" / task["key"] / "completed.json")
        assert record["task"] == task and record["solver_result"]["feasible"]
        host = record.get("execution_host", "local-windows")
        run_id = str(record.get("github_run_id", ""))
        repo, branch, commit, dispatch, batch = mapping[run_id] if run_id else ("", "", "", "", "")
        pid = ""
        if host == "local-windows":
            process = RUN / record["attempt"] / "process.json"
            if process.is_file():
                pid = read_json(process).get("supervisor_pid", "")
            dispatch = f"Local supervisor {pid}" if pid else "Local"
        assert bool(run_id) == (host == "github-actions-windows")
        batches[host, dispatch, batch, repo, run_id, branch, commit] += 1
        rows.append([task["key"], task["instance"], task["variant"], task["seed"], task["iterations"],
                     record["termination"], record["makespan"], record["cpu_seconds"], record["wall_seconds"],
                     host, dispatch, batch, repo, run_id, record.get("github_run_attempt", ""), branch, commit,
                     f"https://github.com/{repo}/actions/runs/{run_id}" if run_id else "", pid,
                     datetime.fromtimestamp(record["completed_unix"], VN).isoformat(timespec="seconds")])
    assert Counter(row[9] for row in rows) == {"local-windows": 8590, "github-actions-windows": 1010}
    headers = ["task_key", "instance", "variant", "seed", "iteration_budget", "termination", "makespan",
               "cpu_seconds", "wall_seconds", "execution_host", "dispatch", "workflow_batch", "github_repo",
               "github_run_id", "github_run_attempt", "git_branch", "git_commit", "github_url",
               "local_supervisor_pid", "completed_at_vietnam"]
    batch_headers = ["execution_host", "dispatch", "workflow_batch", "github_repo", "github_run_id",
                     "git_branch", "git_commit", "validated_runs"]
    write_csv(OUT / "Run_provenance_678.csv", headers, rows)
    book = load_workbook(RUN / "report/Tables_6_edited_7_8.xlsx")
    sheet(book, "Run provenance", headers, rows)
    sheet(book, "Run batches", batch_headers, ([*k, v] for k, v in sorted(batches.items())))
    book.save(OUT / "Tables_6_edited_7_8.xlsx")


def table8_ti():
    book = Workbook()
    book.remove(book.active)
    specs = [("Table 8 Ti", "table8_paper.csv", 5), ("By size", "table8_by_size.csv", 45),
             ("Time budgets", "Ti.csv", 96), ("Per instance", "by_instance.csv", 480),
             ("Runs", "runs.csv", 4800)]
    for name, filename, count in specs:
        headers, rows = csv_rows(TI / filename)
        assert len(rows) == count
        if name == "Runs":
            batches = Counter((row["github_repo"], row["github_run_id"], row["source_sha"]) for row in rows)
            assert all(repo and run_id for repo, run_id, _ in batches)
            assert len({(row["instance"], row["scenario"], row["seed"]) for row in rows}) == count
            assert all(row["feasible"] == "True" for row in rows)
        sheet(book, name, headers, ([typed(k, row[k]) for k in headers] for row in rows))
    sheet(book, "GitHub batches", ["github_repo", "github_run_id", "source_sha", "runs", "url"],
          ([repo, run, sha, n, f"https://github.com/{repo}/actions/runs/{run}"]
           for (repo, run, sha), n in sorted(batches.items())))
    sheet(book, "Protocol", ["Field", "Value"], [
        ("Experiment", "Table 8 with per-instance Ti budgets. Separate from iteration-budget Tables 6–8."),
        ("Scenarios", "A, B, C, D_paper, E"),
        ("Coverage", "96 instances × 5 scenarios × 10 seeds, 4,800 feasible runs on GitHub"),
        ("Sources", "experiments/v4_table8_paper_20261126, with workflow runs listed in Runs and GitHub batches"),
    ])
    book.save(OUT / "Table_8_Ti.xlsx")


def training():
    manifest = read_json(IRACE / "campaign.json")
    root = ROOT / "experiments/irace_v4/training/instances"
    assert len(manifest["selected_instance_names"]) == 10
    with ZipFile(OUT / "irace_v4_training_instances.zip", "w", ZIP_DEFLATED, compresslevel=6) as archive:
        for name, expected in sorted(manifest["training_files"].items()):
            data = (root / name).read_bytes()
            assert hashlib.sha256(data).hexdigest() == expected, name
            archive.writestr(name, data)
    assert hashlib.sha256((ROOT / ".github/workload-tiebreak/inputs.zip").read_bytes()).hexdigest() == (
        read_json(RUN / "bundle/manifest.json")["inputs_zip_sha256"]
    )


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    table678()
    table8_ti()
    training()
    a = load_workbook(OUT / "Tables_6_edited_7_8.xlsx", read_only=True, data_only=True)
    b = load_workbook(OUT / "Table_8_Ti.xlsx", read_only=True, data_only=True)
    assert a["Run provenance"].max_row == 9601 and b["Runs"].max_row == 4801
    assert b["Time budgets"].max_row == 97 and b["Table 8 Ti"].max_row == 6
    print("Validated 9,600 Table 6–8 records, 4,800 Table 8 Ti records, and both input archives.")

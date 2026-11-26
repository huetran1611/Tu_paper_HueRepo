"""Run timed Table 8 batches from a non-default branch."""

import argparse
import os
from pathlib import Path
import shutil
import urllib.parse

import v4_table8_actions as table8

PARALLEL_JOBS_PER_BATCH = 4
WORKFLOW_SLUG = {"DE": "de", "D_paper": "d-paper"}


def prepare(args):
    source_sha = os.environ["GITHUB_SHA"]
    table8.build(argparse.Namespace(dataset="de", parallel_jobs=10 * PARALLEL_JOBS_PER_BATCH,
                                   bundle=args.bundle, source_sha=source_sha))
    table8.prepare(argparse.Namespace(bundle=args.bundle, phase=args.phase, calibration=None, batch=args.batch,
                                     source_sha=source_sha, parallel=PARALLEL_JOBS_PER_BATCH, output=args.runtime))
    for name in ("manifest.json", "baseline.json"):
        shutil.copyfile(args.bundle / name, args.runtime / name)
    scripts = Path(__file__).resolve().parent
    for name in ("v4_table8_actions.py", "run_v4_table8.py", "download_paper_bundle.py", Path(__file__).name):
        shutil.copyfile(scripts / name, args.runtime / name)


def collect(args):
    manifest, batches = table8.load_batches(args.runtime, args.phase, None)
    slug = WORKFLOW_SLUG[args.phase]
    report_name = f"v4-table8-{slug}-report"
    api = table8.GitHub()
    query = urllib.parse.urlencode({"event": "push", "head_sha": manifest["source_sha"], "per_page": 100})
    titles = {f"V4T8 transfer {args.phase} {i}": i for i in range(len(batches))}
    runs = {}
    for run in api.json(f"/runs?{query}")["workflow_runs"]:
        index = titles.get(run["display_title"])
        if index is None:
            continue
        expected_path = f".github/workflows/v4-table8-{slug}-transfer-{index:02d}.yml"
        if (run["path"].split("@")[0] != expected_path or run["head_branch"] != os.environ["GITHUB_REF_NAME"]
                or run["head_sha"] != manifest["source_sha"]):
            continue
        if index in runs:
            raise ValueError(f"Duplicate runs for {args.phase} batch {index}")
        runs[index] = run
    if len(runs) != len(batches):
        print(f"Waiting for all {args.phase} batch runs to be available.")
        return
    artifacts = {}
    for i, run in runs.items():
        artifact = api.packed_artifact(run["id"], f"v4-table8-packed-{run['run_attempt']}")
        if artifact is None:
            print(f"Waiting for the {args.phase} pack from batch {i}.")
            return
        artifacts[i] = artifact
    if any(api.packed_artifact(run["id"], report_name) for run in runs.values()):
        print(f"The {args.phase} report has already been published.")
        return
    records = []
    provenance = []
    for i, batch in enumerate(batches):
        packed = api.read_packed(artifacts[i])
        if not packed:
            raise ValueError(f"Empty {args.phase} pack for batch {i}")
        batch_provenance = {"source_sha": manifest["source_sha"], "solver_sha256": packed[0]["solver_sha256"]}
        table8.verify_batch(batch, packed, batch_provenance)
        records.extend(packed)
        provenance.append({"batch": i, "run_id": runs[i]["id"], "artifact_id": artifacts[i], **batch_provenance})
    args.output.mkdir()
    table8.write_json(args.output / "records.json", records)
    table8.write_json(args.output / "batch_runs.json", provenance)
    table8.report_timed_against_a(args.runtime / "baseline.json", args.output, table8.SCENARIOS[args.phase])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    build = commands.add_parser("prepare")
    build.set_defaults(command=prepare)
    build.add_argument("--batch", type=int, required=True)
    build.add_argument("--bundle", type=Path, required=True)
    build.add_argument("--runtime", type=Path, required=True)
    build.add_argument("--phase", choices=WORKFLOW_SLUG, default="DE")
    report = commands.add_parser("collect")
    report.set_defaults(command=collect)
    report.add_argument("--runtime", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--phase", choices=WORKFLOW_SLUG, default="DE")
    args = parser.parse_args()
    args.command(args)

"""Package GitHub Actions matrices for the delegated v4 campaign."""

import argparse
import json
from pathlib import Path
from shutil import copy2

import paper_tables_678 as design
import run_tables678_local as local


PACKAGE = Path(".github/irace-v4-offload")
BRANCH = "codex/irace-v4-offload-n200-20261124"
RUNTIMES = ("libgcc_s_seh-1.dll", "libstdc++-6.dll", "libwinpthread-1.dll")


def workflow(batch, slots, branch=BRANCH, package=PACKAGE, stem="irace-v4-offload", inputs_url=None):
    slot = "$" + "{{ matrix.slot }}"
    package = package.as_posix()
    input_checkout = "" if inputs_url else "            /.github/workload-tiebreak/inputs.zip"
    input_destination = f"'{package}/bundle/inputs.zip'"
    stage_input = (f"curl.exe --fail --location --retry 4 --output {input_destination} '{inputs_url}'\n"
                   "          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"
                   if inputs_url else
                   f"Copy-Item -LiteralPath '.github/workload-tiebreak/inputs.zip' -Destination {input_destination}")
    label = "Irace v4 n200 retry" if "retry" in stem else (
        "Irace v4 n200 tail transfer" if "tail" in stem else "Irace v4 n200"
    )
    return f"""name: {label} - batch {batch}
run-name: {label} - batch {batch}
on:
  push:
    branches: [{json.dumps(branch)}]
    paths: ['.github/workflows/{stem}-{batch}.yml']
  workflow_dispatch:
permissions:
  contents: read
jobs:
  solve:
    name: Solver {batch}-{slot}
    runs-on: windows-2022
    timeout-minutes: 300
    strategy:
      fail-fast: false
      matrix:
        slot: [{", ".join(str(i) for i in slots)}]
    steps:
      - uses: actions/checkout@v5
        with:
          persist-credentials: false
          sparse-checkout-cone-mode: false
          sparse-checkout: |
            /{package}/
{input_checkout}
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Verify frozen files and run one v4 solver
        shell: pwsh
        run: |
          {stage_input}
          python -B -u {package}/run_one.py {batch} {slot}
          exit $LASTEXITCODE
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: irace-v4-n200-b{batch}-s{slot}
          path: {package}/job/results/
          include-hidden-files: true
          retention-days: 30
          if-no-files-found: error
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--plan", default="delegation_plan.json")
    parser.add_argument("--branch", default=BRANCH)
    parser.add_argument("--stem", default="irace-v4-offload")
    parser.add_argument("--inputs-url")
    args = parser.parse_args()
    bundle = args.run / "bundle"
    plan = design.read(args.run / args.plan)
    manifest = local.load(bundle)
    if plan["protocol_id"] != manifest["protocol_id"]:
        raise ValueError("Delegation plan is for a different campaign")
    relative_package = Path(".github") / args.stem
    package = args.checkout / relative_package
    frozen = package / "bundle"
    frozen.mkdir(parents=True, exist_ok=True)
    (frozen / ".gitattributes").write_text("* -text\n", encoding="ascii")
    copy2(local.ROOT / PACKAGE / "run_one.py", package)
    copy2(local.ROOT / "scripts/paper_tables_678.py", package)
    copy2(local.ROOT / "scripts/run_tables678_local.py", package)
    for fname in ("manifest.json", "paper_tables_678.py", manifest["binary"]):
        copy2(bundle / fname, frozen)
    dependencies = Path("experiments/irace_v3_log")
    (package / dependencies).mkdir(parents=True, exist_ok=True)
    for fname in ("launch_irace.py", "target_runner.py"):
        copy2(local.ROOT / dependencies / fname, package / dependencies)
    for name in RUNTIMES:
        copy2(Path(manifest["compiler_directory"]) / name, frozen)
    plan["runtime_sha256"] = {name: design.sha(frozen / name) for name in RUNTIMES}
    design.save(package / "jobs.json", plan)
    workflows = args.checkout / ".github/workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    for batch, tasks in enumerate(plan["batches"], 1):
        path = workflows / f"{args.stem}-{batch}.yml"
        path.write_text(workflow(batch, range(len(tasks)), args.branch, relative_package,
                                 args.stem, args.inputs_url), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()

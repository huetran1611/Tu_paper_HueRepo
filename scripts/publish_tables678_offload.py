"""Publish the staged offload experiment to its dedicated GitHub branch."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess

from prepare_tables678_offload import BRANCH


def api(endpoint, payload=None, method="POST"):
    command = ["gh", "api", endpoint]
    if payload is not None:
        command.extend(("--method", method, "--input", "-"))
    response = subprocess.run(command, input=json.dumps(payload).encode() if payload is not None else None,
                              capture_output=True, check=False)
    if response.returncode:
        raise RuntimeError(response.stderr.decode(errors="replace"))
    return json.loads(response.stdout)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("checkout", type=Path)
    cli.add_argument("base_commit")
    cli.add_argument("--repository", default="huetran1611/Tu_paper_HueRepo")
    cli.add_argument("--branch", default=BRANCH)
    cli.add_argument("--stem", default="irace-v4-offload")
    cli.add_argument("--update", action="store_true")
    args = cli.parse_args()
    endpoint = f"repos/{args.repository}/git"
    base = api(f"{endpoint}/commits/{args.base_commit}")
    package = args.checkout / ".github" / args.stem
    files = sorted(p for p in package.rglob("*") if p.is_file())
    batches = json.loads((package / "jobs.json").read_text())["batches"]
    files.extend(args.checkout / f".github/workflows/{args.stem}-{i}.yml" for i in range(1, len(batches) + 1))
    if len(files) != 13 + len(batches) or any(not file.is_file() for file in files):
        raise ValueError(f"Unexpected staged files: {[str(file) for file in files]}")

    def upload(file):
        blob = api(f"{endpoint}/blobs", {
            "encoding": "base64", "content": base64.b64encode(file.read_bytes()).decode("ascii"),
        })
        return {"path": file.relative_to(args.checkout).as_posix(), "mode": "100644",
                "type": "blob", "sha": blob["sha"]}

    with ThreadPoolExecutor(max_workers=4) as workers:
        tree = list(workers.map(upload, files))
    new_tree = api(f"{endpoint}/trees", {"base_tree": base["tree"]["sha"], "tree": tree})
    commit = api(f"{endpoint}/commits", {"message": f"Run {sum(map(len, batches))} frozen v4 n200 ablation tasks",
                                      "tree": new_tree["sha"], "parents": [base["sha"]]})
    if args.update:
        ref = f"{endpoint}/refs/heads/{args.branch}"
        if api(f"{endpoint}/ref/heads/{args.branch}")["object"]["sha"] != base["sha"]:
            raise ValueError("Remote branch moved")
        branch = api(ref, {"sha": commit["sha"], "force": False}, method="PATCH")
    else:
        branch = api(f"{endpoint}/refs", {"ref": f"refs/heads/{args.branch}", "sha": commit["sha"]})
    print(json.dumps({"branch": branch["ref"], "commit": commit["sha"], "files": len(files)}))


if __name__ == "__main__":
    main()

"""Fetch the seven requested GitHub experiment runs without exposing credentials."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time
import zipfile
import io

import requests


RUN_IDS = (38436626933, 38436642842, 38436687845, 38436703055,
           38436716745, 38436730658, 38436786985)
REPO = "huetran1611/Tu_paper_HueRepo"
API = f"https://api.github.com/repos/{REPO}"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "results/seven_github_experiments_20261016"


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    local = threading.local()

    def session():
        if not hasattr(local, "session"):
            local.session = requests.Session()
            local.session.headers.update(Authorization=f"Bearer {token}",
                                         Accept="application/vnd.github+json",
                                         **{"X-GitHub-Api-Version": "2022-11-28"})
        return local.session

    def get(url):
        for attempt in range(4):
            response = session().get(url, timeout=180)
            if response.status_code >= 500 and attempt < 3:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response

    catalogs = []
    for run_id in RUN_IDS:
        directory = OUTPUT / "raw" / str(run_id)
        cached = directory / "catalog.json"
        if cached.exists():
            catalog = json.loads(cached.read_text())
        else:
            run = get(f"{API}/actions/runs/{run_id}").json()
            artifacts = []
            page = 1
            while True:
                payload = get(f"{API}/actions/runs/{run_id}/artifacts?per_page=100&page={page}").json()
                artifacts.extend(payload["artifacts"])
                if len(artifacts) >= payload["total_count"]:
                    break
                page += 1
            catalog = {"run": run, "artifacts": artifacts}
            dump(cached, catalog)
        catalogs.append(catalog)
        run = catalog["run"]
        result_artifacts = [a for a in catalog["artifacts"] if "job-" in a["name"]]
        others = [a["name"] for a in catalog["artifacts"] if "job-" not in a["name"]]
        print(json.dumps({"id": run_id, "name": run["name"], "status": run["conclusion"],
                          "head_sha": run["head_sha"], "result_artifacts": len(result_artifacts),
                          "other_artifacts": others}), flush=True)
        source = OUTPUT / "sources" / run["head_sha"] / run["path"]
        if not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(get(f"{API}/contents/{run['path']}?ref={run['head_sha']}" ).json()["content"].encode())
            import base64
            source.write_bytes(base64.b64decode(source.read_bytes()))
    dump(OUTPUT / "catalog.json", catalogs)
    if not args.download:
        return
    tasks = [(c["run"]["id"], a) for c in catalogs for a in c["artifacts"]
             if "job-" in a["name"]]

    def download(item):
        run_id, artifact = item
        target = OUTPUT / "raw" / str(run_id) / "artifacts" / f"{artifact['id']}.zip"
        if artifact["expired"]:
            return {"run_id": run_id, "artifact_id": artifact["id"], "status": "expired"}
        if not target.exists():
            payload = get(f"{API}/actions/artifacts/{artifact['id']}/zip").content
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                if archive.testzip() is not None:
                    raise ValueError("Corrupt artifact ZIP")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        return {"run_id": run_id, "artifact_id": artifact["id"], "name": artifact["name"],
                "status": "downloaded", "file": str(target.relative_to(OUTPUT)),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download, task): task for task in tasks}
        for index, future in enumerate(as_completed(futures), 1):
            try:
                results.append(future.result())
            except Exception as error:
                run_id, artifact = futures[future]
                results.append({"run_id": run_id, "artifact_id": artifact["id"],
                                "status": "failed", "error_type": type(error).__name__})
            if index % 25 == 0 or index == len(tasks):
                dump(OUTPUT / "download_manifest.json", results)
                print(f"Downloaded/checked {index}/{len(tasks)} artifacts; failures="
                      f"{sum(r['status'] != 'downloaded' for r in results)}", flush=True)
    dump(OUTPUT / "download_manifest.json", sorted(results, key=lambda r: (r["run_id"], r["artifact_id"])))


if __name__ == "__main__":
    main()

"""One irace evaluation, with durable completion cache and a process watchdog."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    with temporary.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


SPEC = json.loads((Path(__file__).resolve().parent/'domains.json').read_text())
DOMAINS = SPEC['tuned']
RANGES = {key:(min(values),max(values)) for key,values in DOMAINS.items()}
REAL_PARAMETERS = set(RANGES)

def decode(units: dict) -> dict:
    if set(units) != set(DOMAINS):
        raise ValueError('Exactly eight tuned parameters are required')
    normalized = {}
    for key, value in units.items():
        matches = [x for x in DOMAINS[key] if abs(float(value)-x)<1e-12]
        if len(matches)!=1: raise ValueError(f'Outside ordinal domain: {key}={value}')
        normalized[key] = matches[0]
    normalized.update(SPEC['fixed'])
    normalized['h_div'] = normalized['h_mode'] + normalized['h_gap']
    return normalized


def hard_limit_for(n: int, policy: dict) -> float:
    return float(policy.get(str(n), 0))


def evaluate(run_dir: Path, instance: Path, seed: int, units: dict, config_id: str,
             instance_id: str) -> dict:
    manifest = json.loads((run_dir / "campaign.json").read_text())
    parameters = decode(units)
    paths = [instance, instance.with_name(instance.stem + ".vmax_ij.txt"),
             instance.with_name(instance.stem + ".theta_ijl.txt")]
    input_hashes = {p.name:sha(p) for p in paths}
    expected = manifest["training_files"]
    if any(expected.get(name) != digest for name,digest in input_hashes.items()):
        raise ValueError("Input does not match the frozen training manifest")
    binary = run_dir / "artifacts" / manifest["binary_name"]
    if sha(binary) != manifest["binary_sha256"]:
        raise ValueError("Frozen solver executable changed")
    lines = instance.read_text().splitlines()
    header = {line.split()[0]:int(line.split()[1]) for line in lines[:3]}
    n = header["customers"]
    iters = n * 9 * math.isqrt(n)
    if math.isqrt(n)**2 != n:
        iters += 9*n
    if manifest.get("test_iteration_override") is not None:
        iters = manifest["test_iteration_override"]
    segment_iters = math.ceil(parameters["cseg"]*n - 1e-12)
    hard_limit = hard_limit_for(n, manifest["process_caps_seconds"])
    request = {"schema_version":1,"binary_sha256":manifest["binary_sha256"],
               "input_sha256":input_hashes,"seed":seed,"units":units,
               "iteration_budget":iters,"segment_iters":segment_iters,
               "hard_process_limit_seconds":hard_limit,"all_components_enabled":True,"parameters":parameters,"truck_capacity":70}
    key = hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()[:24]
    directory = run_dir / "evaluations" / key
    directory.mkdir(parents=True,exist_ok=True)
    completed = directory / "completed.json"
    if completed.exists():
        cached = json.loads(completed.read_text())
        if cached["request"] != request:
            raise ValueError("Completion cache does not match request")
        cached["cache_hit"] = True
        atomic_json(directory / f"hit-{uuid.uuid4().hex[:8]}.json",
                    {"configuration_id":config_id,"instance_id":instance_id,"time_unix":time.time()})
        return cached
    # Cross-campaign reuse is read-only at the source and requires the complete
    # solver/input/seed/parameter/budget/cap request to match, not configuration ID.
    for source_run in manifest.get("cache_source_dirs", []):
        source = Path(source_run) / "evaluations" / key / "completed.json"
        if not source.exists(): continue
        cached = json.loads(source.read_text())
        if cached["request"] != request:
            raise ValueError("External completion cache does not match request")
        relative_attempt = Path(cached["attempt"])
        source_attempt = Path(source_run) / relative_attempt
        source_attempt.resolve().relative_to(Path(source_run).resolve())
        if source_attempt.is_dir():
            destination_attempt = run_dir / relative_attempt
            destination_attempt.resolve().relative_to(run_dir.resolve())
            shutil.copytree(source_attempt, destination_attempt, dirs_exist_ok=True)
        cached.update(cache_hit=True, cache_source_completed=str(source.resolve()),
                      imported_unix=time.time())
        atomic_json(completed,cached)
        atomic_json(directory / f"hit-{uuid.uuid4().hex[:8]}.json",
                    {"configuration_id":config_id,"instance_id":instance_id,"time_unix":time.time(),
                     "source_completed":str(source.resolve())})
        return cached
    # Each interrupted attempt remains available; only completed.json is reused.
    attempt = directory / ("a-" + uuid.uuid4().hex[:10])
    attempt.mkdir()
    checkpoint = attempt / "incumbent.json"
    command = [str(binary),str(instance),"--attempts=1",f"--seed={seed}",f"--iters={iters}",
               f"--segment-iters={segment_iters}","--truck-capacity=70","--drone-capacity=2.27",
               "--truck-vmax-file="+str(paths[1]),"--truck-theta-file="+str(paths[2]),
               "--result-json="+str(checkpoint)]
    for name in ("c_tabu","h_mode","h_div","gamma1","gamma2","gamma3","gamma4","T0",
                 "alpha","rho","cseg","kappa","tau_v","r_destroy","eta"):
        command.append("--"+name.replace("_","-")+"="+str(parameters[name]))
    if hard_limit:
        # Leave time to flush the incumbent and exit before the external cutoff.
        reserve = min(2.0,hard_limit*.2)
        command.append(f"--process-time-limit={max(.001,hard_limit-reserve)}")
    started = time.monotonic()
    atomic_json(attempt / "started.json", {"request":request,"command":command,
                "configuration_id":config_id,"instance_id":instance_id,"time_unix":time.time()})
    env = os.environ.copy()
    env.update({"OMP_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1","MKL_NUM_THREADS":"1"})
    timed_out = False
    with (attempt / "stdout.log").open("w") as out, (attempt / "stderr.log").open("w") as err:
        process = subprocess.Popen(command,cwd=attempt,stdout=out,stderr=err,env=env)
        atomic_json(attempt / "process.json", {"pid":process.pid,"wrapper_pid":os.getpid(),"time_unix":time.time()})
        try:
            process.wait(timeout=max(.001,hard_limit-(time.monotonic()-started)) if hard_limit else None)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        out.flush(); os.fsync(out.fileno())
        err.flush(); os.fsync(err.fileno())
    elapsed = time.monotonic()-started
    result = json.loads(checkpoint.read_text()) if checkpoint.exists() else None
    if not timed_out and process.returncode not in (0,2):
        raise RuntimeError(f"Solver crashed, exit {process.returncode}; inspect {attempt}")
    if not timed_out and result is None:
        raise RuntimeError(f"Missing solver result; inspect {attempt}")
    if result:
        components = result['enabled_components']
        if components.get('neighborhoods') != 9 or not all(components.values()):
            raise ValueError('An AMSTS component was disabled')
        for name, actual in result['parameters'].items():
            if name in parameters and abs(actual-parameters[name])>1e-10:
                raise ValueError(f'Solver ignored {name}')
        if result['parameters']['truck_capacity'] != 70:
            raise ValueError('Synthetic truck capacity must be 70')
    feasible = bool(result and result["feasible"])
    if feasible:
        components = result["enabled_components"]
        if components.get("neighborhoods") != 9 or not all(components.values()):
            raise ValueError("An AMSTS component was disabled")
        cost = float(result["makespan"])
        if not math.isfinite(cost) or cost < 0:
            raise ValueError("Invalid makespan")
        nodes = [c for route in result["truck_routes"]+result["drone_routes"] for c in route if c]
        if sorted(nodes) != list(range(1,n+1)):
            raise ValueError("Checkpoint does not cover every customer exactly once")
        for name, actual in result["parameters"].items():
            if name in parameters and abs(actual-parameters[name])>1e-10:
                raise ValueError(f"Solver ignored a tuned parameter: {name}")
    else:
        # A finite penalty above ANY possible completion time for this domain:
        # at most 2n flight/road legs; speed >= .4*24mph; services sum <= 60n.
        coords=[(0.,0.)]+[(float(row.split()[0]),float(row.split()[1])) for row in lines[6:] if row.strip()]
        diagonal=math.hypot(max(x for x,y in coords)-min(x for x,y in coords),
                            max(y for x,y in coords)-min(y for x,y in coords))
        truck=2*n*diagonal/(.4*24*.44704)+60*n
        drone=2*n*(50/15.6464+diagonal/31.2928+50/7.8232)+60*n
        cost=math.ceil(max(truck,drone))+1
    record={"request":request,"cost":cost,"feasible":feasible,"elapsed_seconds":elapsed,
            "external_watchdog":timed_out,"solver_exit_code":process.returncode,
            "solver_result":result,"attempt":str(attempt.relative_to(run_dir)),"cache_hit":False,
            "completed_unix":time.time(),"instance":instance.name,"parameters":parameters}
    atomic_json(completed,record)  # completion is published only AFTER all logs/results
    return record


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir",type=Path,required=True)
    parser.add_argument("--instance",type=Path,required=True)
    parser.add_argument("--seed",type=int,required=True)
    parser.add_argument("--configuration-id",default="manual")
    parser.add_argument("--instance-id",default="manual")
    for name in RANGES:
        parser.add_argument("--"+name.replace("_","-"),dest=name,
                            type=float if name in REAL_PARAMETERS else int,required=True)
    args=parser.parse_args()
    record=evaluate(args.run_dir.resolve(),args.instance.resolve(),args.seed,
                    {name:getattr(args,name) for name in RANGES},args.configuration_id,args.instance_id)
    print(f"{record['cost']:.17g} {record['elapsed_seconds']:.9g}",flush=True)


if __name__=="__main__":
    try: main()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

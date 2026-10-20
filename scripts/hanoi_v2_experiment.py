"""Frozen V2 rerun: original Hanoi solver, buffers 0/5/10/15, new PI and S1."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

BUFFERS = (0, 5, 10, 15)
GROUPS = ("P0", "monday", "saturday", "sunday", "PI")
BATCH_SIZE = 250
ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2)+"\n")


def modules(capsule):
    capsule = capsule.resolve()
    sys.path.insert(0, str(capsule / "runtime/scripts"))
    module = importlib.import_module("hanoi_full_experiment")
    if Path(module.__file__).resolve() != capsule / "runtime/scripts/hanoi_full_experiment.py":
        raise RuntimeError("Historical runtime already imported from another location; use a fresh Python process")
    return module


def tasks(full, instances):
    original = [t for t in full.tasks(instances) if t["buffer_pct"] == 0]
    return [{**t, "buffer_pct": b, "key": t["key"][:-3]+f"b{b:02d}"}
            for t in original for b in ((0,) if t["policy"] == "PI" else BUFFERS)]


def export_inputs():
    """Produce a compact upload package, never the 615-MB expanded CSV tree."""
    old = ROOT / "results/hanoi_github_full_20261016/capsule"
    data = ROOT / "result_final/result_hanoi_v2/data"
    reference = json.loads((old / "experiment.json").read_text())
    metadata = json.loads((data / "manifest.json").read_text())
    assert metadata["parent_profiles_sha256"] == reference["profiles_sha256"]
    assert sha(old / "solver") == reference["binary_sha256"]
    target = ROOT / ".github/hanoi-v2/inputs.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, expected in reference["code_sha256"].items():
            content = subprocess.check_output(["git", "show", reference["commit"]+":"+name], cwd=ROOT)
            assert hashlib.sha256(content).hexdigest() == expected, name
            archive.writestr("runtime/"+name, content)
        archive.writestr("runtime/scripts/requirements-hanoi-v2.txt", "-r requirements-hanoi-full.txt\nmatplotlib==3.9.2\n")
        archive.write(old / "solver", "solver")
        archive.writestr("original_protocol.json", json.dumps({k:v for k,v in reference.items() if k != "tasks"}, indent=2))
        archive.write(data / "profiles.npz", "bundle/profiles.npz")
        archive.write(data / "manifest.json", "bundle/manifest.json")
        for name in metadata["instances"]:
            for file in ("instance.txt", "truck_distance_m.txt", "drone_distance_m.txt"):
                archive.write(data / "instances" / name / file, f"bundle/instances/{name}/{file}")
    dump(target.with_suffix(".json"), {"sha256":sha(target), "profiles_sha256":metadata["profiles_sha256"],
        "original_solver_sha256":reference["binary_sha256"], "original_source_commit":reference["commit"],
        "buffers_pct":list(BUFFERS), "optimization_count":18400, "scheduled_replays":28800})
    print("Exported", target, target.stat().st_size, "bytes")


def freeze(args):
    if args.capsule.exists():
        raise FileExistsError(args.capsule)
    source = ROOT / ".github/hanoi-v2/inputs.zip"
    expected = json.loads(source.with_suffix(".json").read_text())
    assert sha(source) == expected["sha256"]
    with zipfile.ZipFile(source) as archive:
        assert all(not Path(n).is_absolute() and ".." not in Path(n).parts for n in archive.namelist())
        archive.extractall(args.capsule)
    runtime = args.capsule / "runtime"
    shutil.copy2(Path(__file__), runtime / "scripts/hanoi_v2_experiment.py")
    full = modules(args.capsule)
    original = json.loads((args.capsule / "original_protocol.json").read_text())
    assert sha(args.capsule / "solver") == original["binary_sha256"]
    assert full.FLAGS == original["flags"]
    for name, value in original["code_sha256"].items():
        assert sha(runtime / name) == value
    store = full.Profiles(args.capsule / "bundle")
    assert len(store.instances) == 20
    for name in store.instances:
        assert store.daily(name).shape == (12,101,101,12)
    manifest = {"experiment_id":args.experiment_id, "commit":args.commit,
        "created_at":datetime.now(timezone.utc).isoformat(), "dataset_version":"hanoi_v2_group_permutation",
        "buffers_pct":list(BUFFERS), "PI_buffer_pct":0, "start_hour":8, "p0_window":[8,17],
        "seeds":list(full.base.SEEDS), "flags":full.FLAGS,
        "binary_sha256":sha(args.capsule / "solver"), "profiles_sha256":store.manifest["profiles_sha256"],
        "bundle_manifest_sha256":sha(args.capsule / "bundle/manifest.json"),
        "code_sha256":{str(p.relative_to(runtime)):sha(p) for p in sorted(runtime.rglob("*")) if p.is_file()},
        "tasks":tasks(full,store.instances), "batch_size":BATCH_SIZE,
        "original_solver_commit":original["commit"], "original_solver_reused_exactly":True,
        "original_results_or_PI_reused":False, "incumbent_transfer":False}
    assert len(manifest["tasks"]) == len({t["key"] for t in manifest["tasks"]}) == 18400
    manifest["protocol_id"] = full.digest(manifest)
    dump(args.capsule / "experiment.json",manifest)
    counts = Counter(t["owner"] for t in manifest["tasks"])
    dump(args.capsule / "batch_plan.json", {"optimizations":18400, "scheduled_replays":28800,
        "groups":{g:{"tasks":n,"batches":(n+249)//250} for g,n in counts.items()}})
    print(json.dumps(counts),flush=True)


def load(capsule):
    full = modules(capsule)
    manifest = full.load_capsule(capsule)
    if manifest.get("dataset_version") != "hanoi_v2_group_permutation" or manifest["buffers_pct"] != list(BUFFERS):
        raise ValueError("Not the frozen V2 four-buffer capsule")
    if manifest["original_results_or_PI_reused"] or manifest["incumbent_transfer"]:
        raise ValueError("V2 must use independently rerun original results")
    return full, manifest


def collect(full, manifest, expected, source):
    records, report = full.collect_records(source,manifest,expected)
    for key, record in list(records.items()):
        embedded = "planning_customer_events_csv" in record
        embedded &= not record["planning"]["feasible"] or bool(record.get("planning_solution_text"))
        embedded &= all(c["replay"] is None or bool(c.get("customer_events_csv")) for c in record["cases"])
        if not embedded:
            del records[key]
            report["invalid"].append({"task_key":key,"error":"Missing self-contained V2 route/event payload"})
    report["missing"] = sorted(set(expected)-records.keys())
    return records,report


def pack_batch(args):
    full,manifest=load(args.capsule)
    manifest["_capsule"]=args.capsule
    expected={t["key"]:t for t in full.batch_tasks(manifest["tasks"],args.batch,args.policy)}
    records,report=collect(full,manifest,expected,args.input)
    for key,record in records.items(): dump(args.output / key / "task_result.json",record)
    dump(args.output / "batch_coverage.json",{"policy":args.policy,"batch":args.batch,**report})
    if any(report[k] for k in ("missing","conflicting_retries","invalid")):
        raise RuntimeError("Batch incomplete; exact missing/invalid task keys were preserved")


def run_task(args):
    full, manifest = load(args.capsule)
    try:
        full.run_task(args)
        path = args.output / "task_result.json"
        record = json.loads(path.read_text())
        plan = args.output / "planning/output_solution_best.txt"
        record["planning_solution_text"] = plan.read_text() if plan.exists() else None
        record["planning_customer_events_csv"] = (args.output / "planning/customer_events.csv").read_text()
        for case in record["cases"]:
            if case["replay"] is not None:
                case["customer_events_csv"] = (args.output / f"replay_{case['day_type']}/customer_events.csv").read_text()
        manifest["_capsule"] = args.capsule
        full.validate_record(record,record["task"],manifest)
        dump(path,record)
    except Exception as error:
        dump(args.output / "error.json", {"error":str(error),"task_key":args.task_key,
            "protocol_id":manifest["protocol_id"],"technical_failure_not_planning_NP":True})
        raise


def fetch(args):
    _, manifest = load(args.capsule)
    prefix = f"hanoi-v2-{manifest['experiment_id']}-batch-"
    def pages(endpoint, field):
        for page in itertools.count(1):
            sep = "&" if "?" in endpoint else "?"
            value = json.loads(subprocess.check_output(["gh","api",endpoint+f"{sep}per_page=100&page={page}"]))[field]
            yield from value
            if len(value) < 100: break
    import itertools
    args.output.mkdir(parents=True,exist_ok=True)
    artifacts = []
    endpoint = f"repos/{args.repo}/actions/workflows/hanoi-v2.yml/runs?created=%3E%3D{manifest['created_at'][:10]}"
    for run in pages(endpoint,"workflow_runs"):
        for artifact in pages(f"repos/{args.repo}/actions/runs/{run['id']}/artifacts","artifacts"):
            if artifact["name"].startswith(prefix) and not artifact["expired"]:
                artifacts.append(artifact)
    for artifact in artifacts:
        target = args.output / str(artifact["id"])
        target.mkdir(exist_ok=True)
        path = target / "artifact.zip"
        with path.open("wb") as stream:
            subprocess.run(["gh","api",f"repos/{args.repo}/actions/artifacts/{artifact['id']}/zip"],stdout=stream,check=True)
        with zipfile.ZipFile(path) as archive:
            assert all(not Path(n).is_absolute() and ".." not in Path(n).parts for n in archive.namelist())
            archive.extractall(target)
        path.unlink()
    dump(args.output / "download_manifest.json",{"artifacts":artifacts,"experiment_id":manifest["experiment_id"]})


def travel_seconds(distance_m, speed_kph, hours, departure=8):
    """Same piecewise-hour OD integration as the original S1 diagnostic."""
    import numpy as np
    left = np.asarray(distance_m,dtype=float)/1000
    speed, hours = np.asarray(speed_kph), np.asarray(hours)
    assert speed.shape == left.shape+(len(hours),) and np.all(speed>0) and np.all(left>=0)
    elapsed, clock = np.zeros_like(left), float(departure)
    while np.any(left>0):
        k = int(np.clip(np.searchsorted(hours,clock,side="right")-1,0,len(hours)-1))
        if clock>=hours[-1]:
            elapsed += left/speed[...,k]
            break
        boundary = float(hours[0] if clock<hours[0] else hours[k]+1)
        moved = np.minimum(left,speed[...,k]*(boundary-clock))
        elapsed += moved/speed[...,k]
        left = np.maximum(0,left-moved)
        clock = boundary
    return elapsed*3600


def s1(args):
    import numpy as np
    full, manifest = load(args.capsule)
    store = full.Profiles(args.capsule / "bundle")
    ii,jj = np.where(~np.eye(101,dtype=bool))
    dates,hours = store.data["dates"].astype(str),store.data["hours"]
    detailed = []
    for name in store.instances:
        daily = store.daily(name)
        ids = store.data["source_ids"][store.instances.index(name)]
        positions = [store.lookup[(ids[i],ids[j])] for i,j in zip(ii,jj)]
        distance = store.data["distance_m"][positions]
        for group,day in enumerate(full.base.DAYS):
            for fold in range(1,5):
                index = group*4+fold-1
                truth = travel_seconds(distance,daily[index,ii,jj],hours)
                inverse_speed_sum = float(np.sum(truth/(3.6*distance)))
                for policy in ("P0","P1","P2"):
                    profile,_,_ = full.base.profiles_for(daily,hours,fold,policy,day)
                    pred = travel_seconds(distance,profile[ii,jj],hours)
                    error = 100*(pred-truth)/truth
                    detailed.append({"instance":name,"day_type":day,"test_date":dates[index],"policy":policy,
                        "OD_cases":len(error),"MAPE_pct":float(np.mean(abs(error))),"Bias_pct":float(np.mean(error)),
                        "optimistic_OD_pct":float(100*np.mean(error<0)),"inverse_effective_speed_sum":inverse_speed_sum})
    args.output.mkdir(parents=True,exist_ok=True)
    full.base.csv_file(args.output / "S1_by_instance_date.csv", detailed)
    by_date=[]
    for day in full.base.DAYS:
        for label in sorted({r["test_date"] for r in detailed if r["day_type"]==day}):
            for policy in ("P0","P1","P2"):
                rows=[r for r in detailed if (r["day_type"],r["test_date"],r["policy"])==(day,label,policy)]
                total=sum(r["OD_cases"] for r in rows)
                by_date.append({"day_type":day,"test_date":label,"policy":policy,"OD_cases":total,
                    **{k:sum(r[k]*r["OD_cases"] for r in rows)/total
                       for k in ("MAPE_pct","Bias_pct","optimistic_OD_pct")}})
    full.base.csv_file(args.output / "S1_by_date.csv",by_date)
    summary=[]
    for day in full.base.DAYS:
        for policy in ("P0","P1","P2"):
            rows=[r for r in detailed if r["day_type"]==day and r["policy"]==policy]
            total=sum(r["OD_cases"] for r in rows)
            assert total==808000
            summary.append({"day_type":day,"policy":policy,"OD_cases":total,
                "Speed_kph":total/sum(r["inverse_effective_speed_sum"] for r in rows),
                "MAPE_pct":sum(r["MAPE_pct"]*r["OD_cases"] for r in rows)/total,
                "Bias_pct":sum(r["Bias_pct"]*r["OD_cases"] for r in rows)/total})
    full.base.csv_file(args.output / "S1_summary.csv",summary)
    lines=[r"\begin{tabular}{lrrrrrrr}",r"\hline",
        r"Day & Speed & \multicolumn{2}{c}{$P_0$} & \multicolumn{2}{c}{$P_1$} & \multicolumn{2}{c}{$P_2$} \\",
        r" & (km/h) & MAPE & Bias & MAPE & Bias & MAPE & Bias \\",r"\hline"]
    for day in full.base.DAYS:
        rows=[r for r in summary if r["day_type"]==day]
        lines.append(" & ".join([day.title(),f"{rows[0]['Speed_kph']:.2f}"]+
            [f"{r[k]:.2f}" for r in rows for k in ("MAPE_pct","Bias_pct")])+r" \\")
    lines += [r"\hline",r"\end{tabular}"]
    (args.output / "S1.tex").write_text("\n".join(lines)+"\n")


def aggregate(args):
    import numpy as np
    full,manifest = load(args.capsule)
    manifest["_capsule"] = args.capsule
    expected = {t["key"]:t for t in manifest["tasks"]}
    records,coverage = collect(full,manifest,expected,args.input)
    inherited=[]
    for path in args.input.glob("**/batch_coverage.json"):
        value=json.loads(path.read_text())
        if value["conflicting_retries"] or value["invalid"]: inherited.append(str(path))
    refs={(r["task"]["instance"],r["test_date"],r["task"]["seed"]):r["planning"]["makespan_sec"]
          for r in records.values() if r["task"]["policy"]=="PI" and r["planning"]["feasible"]}
    plans,cases=[],[]
    for key,r in sorted(records.items()):
        t=r["task"]
        plans.append({**t,**r["planning"],"solution_text":r.get("planning_solution_text")})
        for c in r["cases"]:
            replay=c["replay"]
            row={**t,"day_type":c["day_type"],"test_date":c["test_date"],"plan_returned":r["planning"]["feasible"]}
            fields=("makespan_sec","finish_hour","freshness_failure","n_violated","max_excess_pct",
                    "max_excess_min","failure_gt5","max_truck_trip_load_kg","max_drone_trip_load_kg")
            row.update({k:replay[k] if replay else None for k in fields})
            pi=refs.get((t["instance"],c["test_date"],t["seed"]))
            row.update(PI_makespan_sec=pi,gap_pct=100*(replay["makespan_sec"]-pi)/pi if replay and pi else None)
            cases.append(row)
    summaries=[]
    for b in BUFFERS:
        for row in full.base.summarize([r for r in cases if r["buffer_pct"]==b]):
            row.update(buffer=b/100,buffer_pct=b)
            paired=[r for r in cases if r["buffer_pct"]==b and r["day_type"]==row["day_type"]
                    and r["policy"]==row["policy"] and r["gap_pct"] is not None]
            mean=lambda k:float(np.mean([r[k] for r in paired])) if paired else None
            rep,pi=mean("makespan_sec"),mean("PI_makespan_sec")
            row.update(mean_paired_replay_min=rep/60 if rep is not None else None,
                mean_paired_PI_min=pi/60 if pi is not None else None,
                gap_ratio_of_means_pct=100*(rep-pi)/pi if paired else None)
            summaries.append(row)
    complete=not any(coverage[k] for k in ("missing","conflicting_retries","invalid")) and not inherited and len(cases)==28800 \
        and all(r["observed_cases"]==800 for r in summaries)
    coverage.update(complete=complete,expected_optimizations=18400,completed_optimizations=len(records),
        scheduled_replays=28800,replay_case_rows=len(cases),PI_feasible_references=len(refs),
        protocol_id=manifest["protocol_id"],batch_integrity_issues=inherited,
        original_PI_not_pooled=True,incumbent_transfer=False)
    args.output.mkdir(parents=True,exist_ok=True)
    dump(args.output / "coverage.json",coverage)
    (args.output / "methods.md").write_text(
        "# Hanoi V2 original rerun\n\n"
        f"Protocol: {manifest['protocol_id']}\n\n"
        "New synthetic group-permuted traffic; independently rerun plans and PI. "
        "No V1 results, incumbent transfers or posthoc pooled PI are used. "
        "PI is a heuristic reference, not a proven optimum.\n\n"
        "NP = missing feasible planning returns / scheduled cases; technical missing jobs "
        "are separately disclosed, not counted as NP. RF = freshness failures / returned plans. "
        "RF is undefined with no returned plan. Date ranges are descriptive minima/maxima, "
        "not confidence intervals.\n\n"
        "gap_pct is the mean casewise 100*(C_replay-C_PI)/C_PI. "
        "gap_ratio_of_means_pct is 100*(mean(C_replay)/mean(C_PI)-1), using identical "
        "paired cases. Negative gaps and freshness-violating replays are retained. "
        "Durations include service. Each complete summary row schedules 800 cases. "
        "Severity means condition on violating replays only.\n\n"
        "S1 uses common directed OD pairs, departure 08:00, no service time, "
        "and piecewise-hour speed integration. Its Speed averages all four dates "
        "of the day type. P0 pools training speeds over [08:00,17:00).\n")
    def uniform(rows):
        fields=list(dict.fromkeys(k for r in rows for k in r))
        return [{k:r.get(k) for k in fields} for r in rows]
    for filename,rows in (("all_planning_runs.csv",plans),("all_replay_cases.csv",cases),("heldout_summary.csv",summaries)):
        full.base.csv_file(args.output / filename,uniform(rows))
    if not complete:
        raise RuntimeError("Incomplete experiment: inspect coverage.json; no complete-result tables generated")
    full.latex_table(args.output,summaries,0)
    if (args.capsule / "S1").exists(): shutil.copytree(args.capsule / "S1",args.output / "S1")
    from openpyxl import Workbook
    book=Workbook(write_only=True)
    for title,rows in (("Planning",uniform(plans)),("Replay",uniform(cases)),("Summary",uniform(summaries))):
        sheet=book.create_sheet(title); fields=list(rows[0]);sheet.append(fields)
        for r in rows:
            values=[r[k] for k in fields]
            assert all(not isinstance(v,str) or len(v)<=32767 for v in values)
            sheet.append(values)
    book.save(args.output / "Hanoi_V2_Results.xlsx")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    colors={"P0":"#2878B5","P1":"#D65B29","P2":"#159782"}
    markers={0:"o",5:"s",10:"^",15:"D"}
    for field,suffix,label in (("gap_pct","casewise","Mean casewise replay-duration gap to PI (%)"),
                             ("gap_ratio_of_means_pct","ratio_means","Ratio-of-means replay-duration gap to PI (%)")):
        fig,axes=plt.subplots(1,3,figsize=(16,5),sharey=True)
        for ax,day in zip(axes,full.base.DAYS):
            for policy in ("P0","P1","P2"):
                rows=sorted((r for r in summaries if r["day_type"]==day and r["policy"]==policy),key=lambda r:r["buffer_pct"])
                rows=[r for r in rows if r[field] is not None and r["RF_pct"] is not None]
                ax.plot([r[field] for r in rows],[r["RF_pct"] for r in rows],color=colors[policy],lw=1.5)
                for r in rows:
                    full_coverage=r["returned_NA"]==r["paired_NB"]==800
                    ax.scatter(r[field],r["RF_pct"],marker=markers[r["buffer_pct"]],s=45,
                        facecolors=colors[policy] if full_coverage else "white",edgecolors=colors[policy],zorder=3)
                    if not full_coverage: ax.annotate(f"A={r['returned_NA']}, B={r['paired_NB']}",(r[field],r["RF_pct"]),fontsize=7)
            ax.set_title(day.title());ax.set_xlabel(label,fontsize=9);ax.grid(axis="y",alpha=.2)
        axes[0].set_ylabel("Replay failure (%)")
        handles=[Line2D([],[],color=c,label=p) for p,c in colors.items()]
        handles += [Line2D([],[],color="#555555",marker=markers[b],ls="none",label=f"{b}%") for b in BUFFERS]
        fig.legend(handles=handles,loc="upper center",ncol=7,frameon=False)
        fig.subplots_adjust(top=.82,bottom=.21,wspace=.16)
        fig.text(.06,.035,"Original V2 runs; heuristic PI; freshness-failing replays retained. Hollow markers indicate incomplete coverage.",fontsize=9)
        fig.savefig(args.output / f"bufferI_{suffix}.pdf",bbox_inches="tight")
        fig.savefig(args.output / f"bufferI_{suffix}.png",bbox_inches="tight",dpi=160)
        plt.close(fig)


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("action",choices=("export-inputs","freeze","check","matrix","run-task","pack-batch","fetch-batches","aggregate","s1"))
    parser.add_argument("--capsule",type=Path,default=Path("build/hanoi-v2-capsule"))
    parser.add_argument("--experiment-id");parser.add_argument("--commit")
    parser.add_argument("--policy",choices=GROUPS);parser.add_argument("--batch",type=int,default=1)
    parser.add_argument("--task-key");parser.add_argument("--input",type=Path);parser.add_argument("--output",type=Path)
    parser.add_argument("--repo",default="huetran1611/Tu_paper_HueRepo")
    args=parser.parse_args();args.capsule=args.capsule.resolve()
    if args.action=="export-inputs": export_inputs()
    elif args.action=="freeze": freeze(args)
    elif args.action=="run-task":run_task(args)
    elif args.action=="fetch-batches":fetch(args)
    elif args.action=="aggregate":aggregate(args)
    elif args.action=="s1":s1(args)
    elif args.action=="pack-batch":pack_batch(args)
    else:
        full,manifest=load(args.capsule)
        if args.action=="check":print(json.dumps({"protocol_id":manifest["protocol_id"],"optimizations":len(manifest["tasks"])}))
        elif args.action=="matrix":
            assert args.policy in GROUPS
            selected=full.batch_tasks(manifest["tasks"],args.batch,args.policy)
            print(json.dumps({"include":[{"key":t["key"]} for t in selected]},separators=(",",":")))


if __name__=="__main__":main()

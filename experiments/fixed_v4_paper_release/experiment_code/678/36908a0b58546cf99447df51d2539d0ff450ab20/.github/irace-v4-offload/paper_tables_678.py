"""Freeze, run and summarize paper Tables 6--8 using an isolated v3 ablation build.

No production source/executable or irace artifact is edited.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = {
    "cyclic": dict(selection="cyclic", mode=False, sa=False, dr=False, tie=False, multiplier=1),
    "random": dict(selection="random", mode=False, sa=False, dr=False, tie=False, multiplier=1),
    "adaptive_mk": dict(selection="adaptive", mode=False, sa=False, dr=False, tie=False, multiplier=1),
    "switch": dict(selection="adaptive", mode=True, sa=False, dr=False, tie=False, multiplier=1),
    "mk_tie": dict(selection="adaptive", mode=False, sa=False, dr=False, tie=True, multiplier=1),
    "mk_tie_2x": dict(selection="adaptive", mode=False, sa=False, dr=False, tie=True, multiplier=2),
    "mk_2x": dict(selection="adaptive", mode=False, sa=False, dr=False, tie=False, multiplier=2),
    "switch_dr": dict(selection="adaptive", mode=True, sa=False, dr=True, tie=False, multiplier=1),
    "switch_sa": dict(selection="adaptive", mode=True, sa=True, dr=False, tie=False, multiplier=1),
    "switch_dr_sa": dict(selection="adaptive", mode=True, sa=True, dr=True, tie=False, multiplier=1),
}
TABLES = {"6": ("cyclic", ["random", "adaptive_mk"]),
          "7": ("adaptive_mk", ["switch", "mk_tie", "mk_tie_2x", "mk_2x"]),
          "8": ("switch", ["switch_dr", "switch_sa", "switch_dr_sa"])}
PARAMETERS = {"cseg":2., "c_tabu":2., "h_mode":1, "h_div":4,
              "gamma1":.7,"gamma2":.3,"gamma3":.1,"gamma4":.3,
              "T0":.005,"alpha":.9982,"kappa":1.,"tau_v":.005,"r_destroy":.2,"eta":1.}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+".tmp-"+uuid.uuid4().hex[:8])
    with temp.open("w",encoding="utf-8") as f:
        json.dump(value,f,indent=2,allow_nan=False);f.write("\n");f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def replace_once(text, old, new):
    if text.count(old)!=1:
        raise ValueError("Production source changed; review ablation adapter anchor: "+old[:100])
    return text.replace(old,new,1)


CONFIG = r'''
struct PaperAblation { std::string selection; bool mode,sa,dr,tie; };
static PaperAblation paper_ablation_from_env() {
    const char* raw=std::getenv("PAPER_VARIANT");
    const std::string name=raw?raw:"adaptive_mk";
    if(name=="cyclic") return {"cyclic",false,false,false,false};
    if(name=="random") return {"random",false,false,false,false};
    if(name=="adaptive_mk"||name=="mk_2x") return {"adaptive",false,false,false,false};
    if(name=="mk_tie"||name=="mk_tie_2x") return {"adaptive",false,false,false,true};
    if(name=="switch") return {"adaptive",true,false,false,false};
    if(name=="switch_dr") return {"adaptive",true,false,true,false};
    if(name=="switch_sa") return {"adaptive",true,true,false,false};
    if(name=="switch_dr_sa") return {"adaptive",true,true,true,false};
    throw std::runtime_error("Unknown PAPER_VARIANT");
}
static const PaperAblation paper_ablation=paper_ablation_from_env();
static double paper_reference_secondary=std::numeric_limits<double>::infinity();
static int paper_cycle_index(int iteration) {
    static const int order[9]={0,1,2,5,6,3,4,7,8};
    return order[iteration%9];
}
static bool paper_improves(double a,double sa,double b,double sb) {
    return a+1e-12<b || (paper_ablation.tie&&std::fabs(a-b)<=1e-12&&sa+1e-12<sb);
}
'''

HELPERS = r'''
static double paper_secondary(const Solution& s) {
    if(!paper_ablation.tie) return 0.;
    double sum=0.;
    for(double t:s.truck_route_times) sum+=t*t;
    for(double t:s.drone_route_times) sum+=t*t;
    return std::sqrt(sum);
}
static bool paper_select(double value,double secondary,double best,double best_secondary,int& ties) {
    if(!paper_ablation.tie) return better_score_with_random_tie(value,best,ties);
    if(value+1e-8<best) { ties=1;return true; }
    if(std::fabs(value-best)>1e-8) return false;
    if(secondary+1e-8<best_secondary) { ties=1;return true; }
    if(std::fabs(secondary-best_secondary)>1e-8) return false;
    return better_score_with_random_tie(value,best,ties);
}
'''


def adapt(cpp, hpp, *, paper_v4=False):
    """Guarded adapter: all other production logic is copied unchanged."""
    cpp=replace_once(cpp,"#define ll long long",CONFIG+"\n#define ll long long")
    helpers = HELPERS
    if paper_v4:
        start = helpers.index("    double sum")
        end = helpers.index("\n}", start)
        helpers = helpers[:start] + "    return solution_score_from_cached_metrics(s, solution_score_workload);" + helpers[end:]
    cpp=replace_once(cpp,'#include "paper_neighborhoods.hpp"',helpers+'\n#include "paper_neighborhoods.hpp"')
    start=cpp.index("        // Roulette Wheel Selection")
    end=cpp.index("        count[selected_neighbor]++;",start)
    if paper_v4:
        roulette = cpp[start:cpp.index("        // Change it to random", start)]
        roulette = replace_once(roulette, "int selected_neighbor =", "selected_neighbor =")
        cpp = cpp[:start] + '''        int selected_neighbor;
        if (paper_ablation.selection == "cyclic") {
            selected_neighbor = paper_cycle_index(iter);
        } else if (paper_ablation.selection == "random") {
            selected_neighbor = static_cast<int>(uniform_index(NUM_NEIGHBORHOODS));
        } else {
''' + "".join("    " + line if line.strip() else line for line in roulette.splitlines(keepends=True)) + "        }\n" + cpp[end:]
        cpp = replace_once(cpp, "    Solution current_sol = initial_solution;",
                           "    Solution current_sol = initial_solution;\n"
                           "    if (paper_ablation.tie) freeze_workload_tau(current_sol);")
        cpp = replace_once(cpp, "if(scoring_mode_iter==1) freeze_workload_tau(current_sol);",
                           "if (scoring_mode_iter == 1 || paper_ablation.tie) freeze_workload_tau(current_sol);")
    else:
        cpp=cpp[:start]+'''        int selected_neighbor=0;
        if(paper_ablation.selection=="cyclic") {
            selected_neighbor=paper_cycle_index(iter);
        } else if(paper_ablation.selection=="random") {
            selected_neighbor=rand()%NUM_NEIGHBORHOODS;
        } else {
            double total=std::accumulate(std::begin(weight),std::end(weight),0.0);
            double draw=double(rand())/RAND_MAX,cumulative=0.;
            selected_neighbor=NUM_NEIGHBORHOODS-1;
            for(int q=0;q<NUM_NEIGHBORHOODS;++q) {
                cumulative+=weight[q]/total;
                if(draw<cumulative) {selected_neighbor=q;break;}
            }
        }
''' + cpp[end:]
    cpp=replace_once(cpp,"        double segment_reference_score = best_segment_score;",
                    "        double segment_reference_score = best_segment_score;\n        paper_reference_secondary=paper_secondary(best_segment_sol);")
    cpp=replace_once(cpp,"bool improves_segment_reference = neighbor_score + 1e-12 < segment_reference_score;",
                    "bool improves_segment_reference = paper_improves(neighbor_score,paper_secondary(neighbor),segment_reference_score,paper_reference_secondary);")
    cpp=replace_once(cpp,"bool improves_current_solution = neighbor_score + 1e-12 < current_score;",
                    "bool improves_current_solution = paper_improves(neighbor_score,paper_secondary(neighbor),current_score,paper_secondary(current_sol));")
    cpp=replace_once(cpp,"        } else {\n            ++run_telemetry.sa_trials;",
                    "        } else if(paper_ablation.sa) {\n            ++run_telemetry.sa_trials;")
    cpp=replace_once(cpp,"if (current_score + 1e-12 < best_segment_score)",
                    "if (paper_improves(current_score,paper_secondary(current_sol),best_segment_score,paper_secondary(best_segment_sol)))")
    cpp=replace_once(cpp,"if (no_improve_segments >= H_DIV)","if (paper_ablation.dr && no_improve_segments >= H_DIV)")
    cpp=replace_once(cpp,"else if (no_improve_segments == H_MODE)","else if (paper_ablation.mode && no_improve_segments == H_MODE)")
    cpp=replace_once(cpp,"if (count[i] != 0)","if (paper_ablation.selection==\"adaptive\" && count[i] != 0)")
    cpp=replace_once(cpp,'\\"adaptive_selection\\":true,\\"tabu\\":true,',
                    '\\"adaptive_selection\\":" << (paper_ablation.selection=="adaptive"?"true":"false") << ",\\"tabu\\":true,')
    cpp=replace_once(cpp,'\\"simulated_annealing\\":true,\\"adaptive_penalties\\":true,',
                    '\\"simulated_annealing\\":" << (paper_ablation.sa?"true":"false") << ",\\"adaptive_penalties\\":true,')
    cpp=replace_once(cpp,'\\"mode_switching\\":true,\\"destroy_repair\\":true}',
                    '\\"mode_switching\\":" << (paper_ablation.mode?"true":"false") << ",\\"destroy_repair\\":" << (paper_ablation.dr?"true":"false") << "}')
    hpp=replace_once(hpp,'double best_score=numeric_limits<double>::infinity();int ties=0;',
                    'double best_score=numeric_limits<double>::infinity();int ties=0;\n    double best_secondary=numeric_limits<double>::infinity();')
    aspiration = '''const double secondary=paper_secondary(candidate);
        const bool aspirates=value+1e-8<reference || (paper_ablation.tie&&fabs(value-reference)<=1e-8&&secondary+1e-8<paper_reference_secondary);'''
    if 'const bool aspirates = value+1e-8<reference;' in hpp:
        hpp=replace_once(hpp,'const bool aspirates = value+1e-8<reference;',aspiration)
    else:
        hpp=replace_once(hpp,'const double value = context.score_candidate(candidate, changes, cost);',
                         'const double value = context.score_candidate(candidate, changes, cost);\n        '+aspiration)
        hpp=hpp.replace('!(value+1e-8<reference)','!aspirates')
    hpp=replace_once(hpp,'if(better_score_with_random_tie(value,best_score,ties))',
                    'if(paper_select(value,secondary,best_score,best_secondary,ties))')
    hpp=replace_once(hpp,'best=candidate;best_score=value;paper_candidate_found=true;',
                    'best=candidate;best_score=value;best_secondary=secondary;paper_candidate_found=true;')
    return cpp,hpp


def prepare(args):
    out=args.bundle.resolve();out.mkdir(parents=True,exist_ok=True)
    source=out/"source";source.mkdir(exist_ok=True)
    cpp_path=ROOT/"src_v3/tabubu_TimeDependent_v3.cpp"
    hpp_path=ROOT/"src_v3/paper_neighborhoods.hpp"
    cpp,hpp=adapt(cpp_path.read_text(encoding="utf-8"),hpp_path.read_text(encoding="utf-8"))
    (source/"solver.cpp").write_text(cpp,encoding="utf-8")
    (source/"paper_neighborhoods.hpp").write_text(hpp,encoding="utf-8")
    binary=out/("solver.exe" if os.name=="nt" else "solver")
    subprocess.run([args.compiler,"-std=c++17","-O2","-Wall","-Wextra",str(source/"solver.cpp"),"-o",str(binary)],check=True)
    shutil.copy2(__file__,out/"paper_tables_678.py")
    shutil.copy2(ROOT/".github/workload-tiebreak/inputs.zip",out/"inputs.zip")
    meta=read(ROOT/".github/workload-tiebreak/inputs.json")
    instances=meta["instances"]
    if len(instances)!=96 or any(i["customers"]>200 for i in instances):
        raise ValueError("Expected the 96 synthetic test instances, n=6..200")
    # Confirm packaged values, cardinalities and sidecar hashes, rather than trusting filenames.
    with ZipFile(out/"inputs.zip") as archive:
        for inst in instances:
            for f in inst["files"]:
                if hashlib.sha256(archive.read(f["path"])).hexdigest()!=f["sha256"]:
                    raise ValueError("Packaged data hash mismatch")
    seeds=list(range(42,52))
    tasks=[]
    for inst in instances:
        for name,variant in VARIANTS.items():
            for seed in seeds:
                tasks.append(dict(key=f"{inst['instance']}-{name}-s{seed}",instance=inst["instance"],variant=name,
                                  seed=seed,n=inst["customers"],iterations=9*inst["customers"]*math.ceil(math.sqrt(inst["customers"]))*variant["multiplier"]))
    for index,task in enumerate(tasks):
        task.update(batch=index//250+1,slot=index%250)
    manifest=dict(version=2,commit=args.commit,source_sha256={"cpp":sha(cpp_path),"hpp":sha(hpp_path)},
                  adapted_source_sha256={p.name:sha(p) for p in source.iterdir()},binary=binary.name,binary_sha256=sha(binary),
                  runner_sha256=sha(out/"paper_tables_678.py"),inputs_zip_sha256=sha(out/"inputs.zip"),instances=instances,seeds=seeds,
                  variants=VARIANTS,tables=TABLES,parameters=PARAMETERS,tasks=tasks,batch_size=250,batch_count=39,
                  process_caps={"default":7200,"200":7200},time_metric="process CPU user+system, per solver process",
                  n7="all ordered distinct vehicles, top3 distance-delta insertion positions on C, no dedup",
                  tie_rule="active penalized makespan primary; raw workload L2 secondary on ties; for feasible solutions this is (Cmax,L2)",
                  sa_disabled="accept strict improvement only, including lexicographic improvement if enabled",
                  paper_seed_assumption="42..51; paper specifies ten independent runs but no seed values")
    manifest["protocol_id"]=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    save(out/"manifest.json",manifest)
    print(json.dumps(dict(instances=96,variants=10,tasks=len(tasks),batches=39,protocol_id=manifest["protocol_id"])))


def load(bundle):
    m=read(bundle/"manifest.json");raw=dict(m);identifier=raw.pop("protocol_id")
    if hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()!=identifier: raise ValueError("Protocol changed")
    for name,digest in ((m["binary"],m["binary_sha256"]),("inputs.zip",m["inputs_zip_sha256"]),("paper_tables_678.py",m["runner_sha256"])):
        if sha(bundle/name)!=digest: raise ValueError("Frozen artifact changed: "+name)
    return m


def run_one(bundle,out,task,m):
    target=out/task["key"];target.mkdir(parents=True,exist_ok=True)
    completed=target/"completed.json"
    if completed.exists():
        existing=read(completed)
        if existing["protocol_id"]!=m["protocol_id"] or existing["task"]!=task: raise ValueError("Cached task mismatch")
        return
    attempt=target/("a-"+uuid.uuid4().hex[:10]);attempt.mkdir()
    inst=next(i for i in m["instances"] if i["instance"]==task["instance"])
    data=attempt/"inputs";data.mkdir()
    with ZipFile(bundle/"inputs.zip") as archive:
        for f in inst["files"]:
            raw=archive.read(f["path"])
            if hashlib.sha256(raw).hexdigest()!=f["sha256"]: raise ValueError("Input hash mismatch")
            (data/Path(f["path"]).name).write_bytes(raw)
    instance=data/(task["instance"]+".txt")
    cap=m["process_caps"].get(str(task["n"]),m["process_caps"]["default"])
    result_path=attempt/"incumbent.json"
    command=[str(bundle/m["binary"]),str(instance),"--attempts=1",f"--seed={task['seed']}",f"--iters={task['iterations']}",
             f"--segment-iters={2*task['n']}","--truck-capacity=40","--drone-capacity=2.27",f"--process-time-limit={cap-2}",
             "--truck-vmax-file="+str(instance.with_name(instance.stem+".vmax_ij.txt")),
             "--truck-theta-file="+str(instance.with_name(instance.stem+".theta_ijl.txt")),"--result-json="+str(result_path)]
    for name,value in PARAMETERS.items():
        if name!="cseg": command.append("--"+name.replace("_","-")+"="+str(value))
    env=os.environ.copy();env.update(PAPER_VARIANT=task["variant"],OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1")
    save(attempt/"request.json",dict(task=task,command=command,protocol_id=m["protocol_id"],started_unix=time.time()))
    import resource
    before=resource.getrusage(resource.RUSAGE_CHILDREN);start=time.monotonic()
    with (attempt/"stdout.log").open("w") as stdout,(attempt/"stderr.log").open("w") as stderr:
        subprocess.run(command,cwd=attempt,env=env,stdout=stdout,stderr=stderr,check=True,timeout=cap)
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    result=read(result_path)
    if not result["feasible"] or result["completed_iterations"]!=task["iterations"] or result["stop_reason"]!="iteration_budget":
        raise ValueError("Not a feasible full-budget result; partial checkpoint kept: "+str(attempt))
    if sorted(c for route in result["truck_routes"]+result["drone_routes"] for c in route if c)!=list(range(1,task["n"]+1)):
        raise ValueError("Incorrect customer coverage")
    flags=result["enabled_components"];v=VARIANTS[task["variant"]]
    for name,expected in dict(adaptive_selection=v["selection"]=="adaptive",simulated_annealing=v["sa"],mode_switching=v["mode"],destroy_repair=v["dr"]).items():
        if flags[name]!=expected: raise ValueError("Ablation flag mismatch: "+name)
    for name,value in PARAMETERS.items():
        if name!="cseg" and abs(result["parameters"][name]-value)>1e-10: raise ValueError("Table-5 parameter mismatch")
    cpu=(after.ru_utime+after.ru_stime)-(before.ru_utime+before.ru_stime)
    save(completed,dict(protocol_id=m["protocol_id"],task=task,makespan=result["makespan"],cpu_seconds=cpu,
                        wall_seconds=time.monotonic()-start,solver_result=result))
    # Inputs are frozen in the shared zip; keep logs/checkpoints, not per-task duplicates.
    for path in data.iterdir(): path.unlink()
    data.rmdir()


def task_at(manifest,batch,slot):
    if manifest.get("version")!=2: raise ValueError("Build a new single-process/job setup bundle (version 2)")
    found=[t for t in manifest["tasks"] if t["batch"]==batch and t["slot"]==slot]
    if len(found)!=1: raise ValueError("Invalid batch/slot")
    return found[0]


def task_job(args):
    if os.name!="posix": raise RuntimeError("CPU timing requires Linux/POSIX. Use the GitHub workflow.")
    bundle=args.bundle.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    m=load(bundle);task=task_at(m,args.batch,args.slot)
    try: run_one(bundle,out,task,m)
    except Exception as error:
        save(out/task["key"]/"last_error.json",dict(key=task["key"],error=str(error)))
        raise


def accepts_result_protocol(manifest, record):
    if record["protocol_id"] == manifest["protocol_id"]:
        return True
    task = record["task"]
    revised_cap = task["n"] == 200 and VARIANTS[task["variant"]]["multiplier"] == 2
    return record["protocol_id"] == manifest.get("previous_protocol_id") and not revised_cap


def aggregate(args):
    m=load(args.bundle.resolve());out=args.output;out.mkdir(parents=True,exist_ok=True)
    expected={t["key"]:t for t in m["tasks"]};records={}
    for path in args.results.rglob("completed.json"):
        r=read(path);key=r["task"]["key"]
        if expected.get(key)!=r["task"] or not accepts_result_protocol(m, r): raise ValueError("Foreign result")
        if key in records and records[key]!=r: raise ValueError("Conflicting duplicate result")
        records[key]=r
    save(out/"missing_tasks.json",[t for key,t in expected.items() if key not in records])
    rows=[dict(instance=r["task"]["instance"],variant=r["task"]["variant"],seed=r["task"]["seed"],
               makespan=r["makespan"],cpu_seconds=r["cpu_seconds"],wall_seconds=r["wall_seconds"]) for r in records.values()]
    irace_v4 = m.get("algorithm_policy") == "irace-v4"
    if irace_v4:
        for row, result in zip(rows, records.values()):
            row["termination"] = result["termination"]
            row["execution_host"] = result.get("execution_host", "local-windows")
    if rows:
        with (out/"runs.csv").open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    groups={}
    for inst in m["instances"]:
        for name in VARIANTS:
            found=[r for r in rows if r["instance"]==inst["instance"] and r["variant"]==name]
            if {r["seed"] for r in found}==set(m["seeds"]):
                groups[(inst["instance"],name)]={"best":min(r["makespan"] for r in found),
                    "mean":statistics.mean(r["makespan"] for r in found),"time":statistics.mean(r["cpu_seconds"] for r in found),
                    "execution_hosts": {r.get("execution_host", "local-windows") for r in found}}
    paper_v4 = m.get("algorithm_policy") in {"paper-v4", "irace-v4"}
    instance_count = len(m["instances"]) if paper_v4 else 96
    implementation = ("Paper (4) Table 5, v4 solver, Windows, and paired seeds 42–51."
                      if paper_v4 else "Current v3/N7 policy; this reproduces the experimental design, not guaranteed published numbers.")
    title = "# Tables 6–8 — irace v4 configuration" if irace_v4 else "# Paper Tables 6–8 — Table-5 parameters"
    if irace_v4:
        standard = m["process_caps"]["200"]
        doubled = m.get("doubled_process_caps", {}).get("200", standard)
        implementation = ("V4 solver with the winning irace parameters, on Windows. "
                          f"The n=200 wall-clock caps are {standard:,} s at standard budget and "
                          f"{doubled:,} s at doubled budget. Feasible incumbents at the limit enter the tables.")
    tables={};lines=[title, "",f"Completed {len(records)}/{len(expected)} runs with feasible results.",
                        implementation,
                        "Time is solver-process CPU user+system. Gaps are computed per instance and averaged with equal instance weight.", ""]
    if irace_v4:
        capped = sum(row["termination"] == "time_limit" for row in rows)
        lines[3:3] = [f"Full iteration budget: {len(rows)-capped}. Feasible at the time limit: {capped}.",
                      f"Seeds: {', '.join(map(str, m['seeds']))}."]
        if any(row["execution_host"] != "local-windows" for row in rows):
            lines.append("CPU-time gaps use only paired instance groups measured in the same runner pool. "
                         "Makespan results include both pools, with the same wall-clock caps.")
            lines.append("")
    for number,(baseline,variants) in TABLES.items():
        lines += [f"## Table {number} — baseline {baseline}","",f"| Variant | Instances/{instance_count} | Best gap % | Mean gap % | CPU time gap % |","|---|---:|---:|---:|---:|"]
        table=[]
        for variant in variants:
            paired=[i["instance"] for i in m["instances"] if (i["instance"],baseline) in groups and (i["instance"],variant) in groups]
            gaps={metric:statistics.mean(100*(groups[(i,variant)][metric]/groups[(i,baseline)][metric]-1) for i in paired)
                  if paired and all(groups[(i,baseline)][metric]>0 for i in paired) else None for metric in ("best","mean","time")}
            row = dict(variant=variant,paired_instances=len(paired),complete=len(paired)==instance_count,**gaps)
            if paper_v4:
                cpu_pairs = [i for i in paired if groups[i,baseline]["time"] > 0 and groups[i,variant]["time"] > 0]
                if irace_v4:
                    cpu_pairs = [i for i in cpu_pairs
                                 if len(groups[i, baseline]["execution_hosts"]) == 1
                                 and groups[i, baseline]["execution_hosts"] == groups[i, variant]["execution_hosts"]]
                row["cpu_paired_instances"] = len(cpu_pairs)
                gaps["time"] = row["time"] = (
                    statistics.mean(100 * (groups[i,variant]["time"] / groups[i,baseline]["time"] - 1) for i in cpu_pairs)
                    if cpu_pairs else None
                )
            table.append(row)
            fmt=lambda value:"NA" if value is None else f"{value:+.3f}"
            cpu_gap = fmt(gaps["time"])
            if paper_v4:
                cpu_gap += f" ({row['cpu_paired_instances']} instances)"
            lines.append(f"| {variant} | {len(paired)}/{instance_count} | {fmt(gaps['best'])} | {fmt(gaps['mean'])} | {cpu_gap} |")
        tables[number]=table;lines.append("")
    save(out/"tables.json",tables)
    (out/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    if len(records)!=len(expected): raise SystemExit("Partial experiment: see missing_tasks.json and resume")


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("prepare");p.add_argument("--bundle",type=Path,required=True);p.add_argument("--compiler",default="g++");p.add_argument("--commit",default="local")
    p=sub.add_parser("task");p.add_argument("--bundle",type=Path,required=True);p.add_argument("--batch",type=int,required=True);p.add_argument("--slot",type=int,required=True);p.add_argument("--output",type=Path,required=True)
    p=sub.add_parser("aggregate");p.add_argument("--bundle",type=Path,required=True);p.add_argument("--results",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.command=="prepare": prepare(args)
    elif args.command=="task": task_job(args)
    else: aggregate(args)


if __name__=="__main__": main()

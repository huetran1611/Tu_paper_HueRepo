"""Real solver ablation checks plus an instance-level aggregation oracle."""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile
import paper_tables_678 as batch


def test_aggregation():
    # Different baseline scales distinguish mean-of-gaps from ratio-of-means.
    with tempfile.TemporaryDirectory() as raw:
        root=Path(raw);tasks=[]
        instances=[dict(instance="a"),dict(instance="b")]
        for name in batch.VARIANTS:
            for instance,base,new in (("a",100,50),("b",1000,1100)):
                for seed in (42,43):
                    task=dict(key=f"{instance}-{name}-{seed}",instance=instance,variant=name,seed=seed)
                    tasks.append(task)
                    value=base if name=="cyclic" else new
                    batch.save(root/"results"/task["key"]/"completed.json",dict(protocol_id="test",task=task,makespan=value,cpu_seconds=value,wall_seconds=value))
        manifest=dict(protocol_id="test",tasks=tasks,instances=instances,seeds=[42,43])
        args=SimpleNamespace(bundle=root,results=root/"results",output=root/"report")
        with patch.object(batch,"load",return_value=manifest): batch.aggregate(args)
        tables=batch.read(root/"report/tables.json")
        assert abs(tables["6"][0]["best"]-(-20))<1e-10
        assert abs(tables["6"][0]["mean"]-(-20))<1e-10
        assert abs(tables["6"][0]["time"]-(-20))<1e-10
        assert not tables["6"][0]["complete"]
        missing=next((root/"results").rglob("completed.json"));missing.unlink()
        with patch.object(batch,"load",return_value=manifest):
            try: batch.aggregate(args)
            except SystemExit: pass
            else: raise AssertionError("Incomplete coverage must not pass")
        assert len(batch.read(root/"report/missing_tasks.json"))==1


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--bundle",type=Path,required=True);parser.add_argument("--compiler",default="g++")
    args=parser.parse_args();bundle=args.bundle.resolve();m=batch.load(bundle)
    assert len(m["tasks"])==9600 and len({t["key"] for t in m["tasks"]})==9600
    assert m["version"]==2 and m["batch_count"]==39
    assert [sum(t["batch"]==i for t in m["tasks"]) for i in range(1,40)]==[250]*38+[100]
    assert len({(t["batch"],t["slot"]) for t in m["tasks"]})==9600
    assert len(m["instances"])==96 and len(m["seeds"])==10
    assert m["parameters"]==batch.PARAMETERS
    test_aggregation()
    with tempfile.TemporaryDirectory(prefix="t678-",dir=bundle.parent) as raw:
        temp=Path(raw)
        with ZipFile(bundle/"inputs.zip") as archive:
            inst=next(i for i in m["instances"] if i["instance"]=="6.5.1")
            for f in inst["files"]: (temp/Path(f["path"]).name).write_bytes(archive.read(f["path"]))
        instance=temp/"6.5.1.txt"
        for name,variant in batch.VARIANTS.items():
            directory=temp/name;directory.mkdir()
            iterations=180*variant["multiplier"]
            command=[str(bundle/m["binary"]),str(instance),"--attempts=1","--seed=42",f"--iters={iterations}","--segment-iters=12",
                     "--truck-capacity=40","--drone-capacity=2.27","--result-json="+str(directory/"result.json"),
                     "--truck-vmax-file="+str(temp/"6.5.1.vmax_ij.txt"),"--truck-theta-file="+str(temp/"6.5.1.theta_ijl.txt")]
            for key,value in batch.PARAMETERS.items():
                if key!="cseg": command.append("--"+key.replace("_","-")+"="+str(value))
            env=os.environ.copy();env["PAPER_VARIANT"]=name
            result=subprocess.run(command,cwd=directory,env=env,capture_output=True,text=True,timeout=30)
            assert result.returncode==0,(name,result.stderr)
            data=batch.read(directory/"result.json");flags=data["enabled_components"];telemetry=data["telemetry"]
            assert data["feasible"] and data["completed_iterations"]==iterations
            for key,value in batch.PARAMETERS.items():
                if key!="cseg": assert abs(data["parameters"][key]-value)<1e-10
            assert flags["adaptive_selection"]==(variant["selection"]=="adaptive")
            assert flags["mode_switching"]==variant["mode"] and flags["simulated_annealing"]==variant["sa"]
            assert flags["destroy_repair"]==variant["dr"]
            if not variant["sa"]: assert telemetry["sa_trials"]==0
            if not variant["mode"]: assert telemetry["workload_segments"]==0
            if not variant["dr"]: assert telemetry["destroy_repair_calls"]==0
            if variant["sa"]: assert telemetry["sa_trials"]>0
            if variant["selection"]=="cyclic": assert telemetry["operator_calls"]==[iterations//9]*9
        # Force stagnation with an impossible post-pickup limit; verify that
        # the enabled mechanisms actually execute, rather than only reporting flags.
        impossible=temp/"impossible.txt"
        original=instance.read_text().splitlines()
        rows=[]
        for row in original[6:]:
            values=row.split()
            if values: values[6]="0.00000001";rows.append(" ".join(values))
        impossible.write_text("\n".join(original[:6]+rows)+"\n")
        directory=temp/"forced-stagnation";directory.mkdir()
        env=os.environ.copy();env["PAPER_VARIANT"]="switch_dr_sa"
        result=subprocess.run([str(bundle/m["binary"]),str(impossible),"--attempts=1","--seed=42","--iters=120","--segment-iters=6",
                               "--result-json="+str(directory/"result.json"),
                               "--truck-vmax-file="+str(temp/"6.5.1.vmax_ij.txt"),"--truck-theta-file="+str(temp/"6.5.1.theta_ijl.txt")],
                              cwd=directory,env=env,capture_output=True,text=True,timeout=30)
        assert result.returncode==2,result.stderr
        telemetry=batch.read(directory/"result.json")["telemetry"]
        assert telemetry["destroy_repair_calls"]>0 and telemetry["workload_segments"]>0 and telemetry["sa_trials"]>0
        unit=temp/"unit.cpp"
        unit.write_text('#define main solver_main\n#include "'+(bundle/"source/solver.cpp").as_posix()+'"\n#undef main\n#include <cassert>\n'+r'''
int main() {
    const int order[9]={0,1,2,5,6,3,4,7,8};
    for(int i=0;i<27;++i) assert(paper_cycle_index(i)==order[i%9]);
    assert(paper_improves(9,100,10,0));
    assert(!paper_improves(11,0,10,100));
    assert(paper_improves(10,1,10,2)==paper_ablation.tie);
    if(paper_ablation.tie) {
        int ties=5;
        assert(paper_select(10,1,10,2,ties)&&ties==1);
        assert(!paper_select(10,3,10,2,ties));
        assert(!paper_select(11,0,10,2,ties));
    }
}
''',encoding="utf-8")
        unit_binary=temp/("unit.exe" if os.name=="nt" else "unit")
        subprocess.run([args.compiler,"-std=c++17","-O1",str(unit),"-o",str(unit_binary)],check=True,capture_output=True)
        for variant in ("mk_tie","adaptive_mk"):
            env=os.environ.copy();env["PAPER_VARIANT"]=variant
            subprocess.run([str(unit_binary)],env=env,check=True,capture_output=True)
        if os.name=="posix":
            task=next(t for t in m["tasks"] if t["n"]==6 and t["variant"]=="adaptive_mk")
            output=temp/"cpu-check"
            batch.run_one(bundle,output,task,m)
            result_path=output/task["key"]/"completed.json"
            original=result_path.read_bytes()
            record=json.loads(original)
            assert record["cpu_seconds"]>0 and record["wall_seconds"]>0
            batch.run_one(bundle,output,task,m)
            assert result_path.read_bytes()==original
    print("PASS: 9600-task coverage, 10 real ablations, Table-5 parameters, flags/telemetry, doubled budget, cyclic order, lexicographic comparison, per-instance gaps and incomplete-report rejection")


if __name__=="__main__": main()

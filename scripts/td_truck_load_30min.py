"""Run one TD instance per process and audit payloads between depot visits."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re
import subprocess
import time


def matrix(root):
    instances = []
    for path in sorted(root.glob('*.txt')):
        if re.fullmatch(r'(200|500)\.\d+\.\d+\.txt', path.name):
            base = path.stem
            for suffix in ['.txt', '.vmax_ij.txt', '.theta_ijl.txt']:
                if not (root / (base + suffix)).is_file():
                    raise ValueError(f'Missing input: {base}{suffix}')
            instances.append(base)
    if len(instances) != 32 or sum(s.startswith('200.') for s in instances) != 16:
        raise ValueError(f'Expected 16 instances per size, found {len(instances)}')
    return instances


def demands(path):
    with path.open(encoding='utf-8-sig') as stream:
        lines = stream.readlines()
    n = int(lines[2].split()[1])
    header = lines[5].split()
    index = header.index('Demand')
    values = [float(line.split()[index]) for line in lines[6:] if line.strip()]
    if len(values) != n:
        raise ValueError(f'Demand count mismatch: {path}')
    return [0.] + values


def payloads(text, q):
    trips, visits, drone_loads = [], [], []
    for kind, vehicle, route in re.findall(r'^(Truck|Drone)\s+(\d+):\s*([0-9 ]+)', text, re.M):
        nodes = list(map(int, route.split()))
        if not nodes or nodes[0] != 0 or nodes[-1] != 0:
            raise ValueError(f'Unclosed {kind} route')
        current, trip_index = [], 0
        for node in nodes:
            if node:
                if node >= len(q):
                    raise ValueError(f'Customer {node} outside instance')
                current.append(node)
                visits.append(node)
            elif current:
                trip_index += 1
                load = sum(q[c] for c in current)
                if kind == 'Truck':
                    trips.append(dict(truck_index=int(vehicle), trip_index=trip_index,
                                      load_kg=load, customers_count=len(current),
                                      customers=' '.join(map(str, current))))
                else:
                    drone_loads.append(load)
                current = []
    counts = Counter(visits)
    coverage_ok = set(counts) == set(range(1, len(q))) and all(v == 1 for v in counts.values())
    best = max(trips, key=lambda t: t['load_kg'], default=dict(load_kg=0., customers='', customers_count=0,
                                                            truck_index=0, trip_index=0))
    return trips, best, coverage_ok, max(drone_loads, default=0.)


def write_csv(path, rows):
    if not rows:
        raise ValueError(f'No records for {path}')
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(args):
    root, output = args.root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.instance not in matrix(root):
        raise ValueError(f'Unexpected instance: {args.instance}')
    q = demands(root / f'{args.instance}.txt')
    # Reserve finalization time inside the hard per-process wall limit.
    search_seconds = max(1., args.wall_limit_sec - 30.)
    command = [str(args.binary.resolve()), str(root / f'{args.instance}.txt'),
               f'--truck-vmax-file={root / (args.instance + ".vmax_ij.txt")}',
               f'--truck-theta-file={root / (args.instance + ".theta_ijl.txt")}',
               '--attempts=1', '--seed=1', '--truck-capacity=400', '--drone-capacity=2.27',
               f'--time-limit={search_seconds}']
    if args.smoke_iterations is not None:
        command.append(f'--iters={args.smoke_iterations}')
    (output / 'command.json').write_text(json.dumps(command, indent=2))
    start = time.monotonic()
    status = 'completed'
    return_code = None
    with (output / 'solver.log').open('w') as stream:
        try:
            proc = subprocess.run(command, cwd=output, stdout=stream, stderr=subprocess.STDOUT,
                                  timeout=args.wall_limit_sec)
            return_code = proc.returncode
            if return_code:
                status = 'solver_failed'
        except subprocess.TimeoutExpired:
            status = 'hard_timeout'
    elapsed = time.monotonic() - start
    row = dict(instance=args.instance, customers=len(q)-1, drone_capacity_kg=2.27,
               truck_capacity_kg=400, model='multi_trip', repetitions=1, attempts=1, seed=1,
               wall_limit_sec=args.wall_limit_sec, search_limit_sec=search_seconds,
               wall_elapsed_sec=elapsed, process_status=status, return_code=return_code,
               termination_reason='', executed_iterations=0, feasibility='NO_SOLUTION',
               coverage_ok=False, payload_capacity_ok=False, max_truck_trip_load_kg='',
               max_drone_trip_load_kg='', truck_index='', trip_index='', trip_customers='',
               trip_customers_count='', truck_trip_count='', makespan='',
               source_commit=args.source_commit, run_url=args.run_url)
    iteration_file = output / 'output_mode_0.txt'
    if iteration_file.exists():
        with iteration_file.open() as stream:
            row['executed_iterations'] = max(0, sum(1 for _ in stream)-1)
    solution = output / 'output_solution_best.txt'
    if solution.is_file():
        text = solution.read_text()
        trips, best, coverage_ok, drone_max = payloads(text, q)
        feasibility = re.search(r'Final solution feasibility:\s*(\w+)', text)
        makespan = re.search(r'Improved solution cost:\s*([\d.eE+-]+)', text, re.I)
        row.update(feasibility=feasibility.group(1) if feasibility else 'UNKNOWN',
                   makespan=makespan.group(1) if makespan else '', coverage_ok=coverage_ok,
                   payload_capacity_ok=best['load_kg'] <= 400 + 1e-8 and drone_max <= 2.27 + 1e-8,
                   max_truck_trip_load_kg=best['load_kg'], max_drone_trip_load_kg=drone_max,
                   truck_index=best['truck_index'], trip_index=best['trip_index'],
                   trip_customers=best['customers'], trip_customers_count=best['customers_count'],
                   truck_trip_count=len(trips))
        if trips:
            write_csv(output / 'truck_trips.csv', [dict(instance=args.instance, **t) for t in trips])
    log = (output / 'solver.log').read_text(errors='replace')
    config = re.search(r'Search config: total_iters=(\d+)', log)
    budget = int(config.group(1)) if config else 200000
    row['termination_reason'] = (status if status != 'completed' else
                                 'iteration_limit' if row['executed_iterations'] >= budget else 'search_time_limit')
    write_csv(output / 'result.csv', [row])
    print(json.dumps(row, indent=2))
    if status != 'completed' or not solution.is_file() or not row['coverage_ok']:
        raise SystemExit(1)


def aggregate(args):
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(args.input.rglob('result.csv')):
        with path.open() as stream:
            rows.extend(csv.DictReader(stream))
    expected = set(matrix(args.root))
    if len(rows) != 32 or {r['instance'] for r in rows} != expected:
        raise ValueError(f'Incomplete results: {len(rows)}/32; missing {sorted(expected-{r["instance"] for r in rows})}')
    rows.sort(key=lambda r: tuple(map(int, r['instance'].split('.'))))
    write_csv(args.output / 'truck_load_all_instances.csv', rows)
    summary = []
    for n in [200, 500]:
        group = [r for r in rows if int(r['customers']) == n]
        valid = [r for r in group if r['process_status']=='completed' and r['coverage_ok']=='True'
                 and r['payload_capacity_ok']=='True' and r['feasibility']=='FEASIBLE']
        best = max(valid, key=lambda r: float(r['max_truck_trip_load_kg']), default=None)
        summary.append(dict(customers=n, expected_instances=16, completed_instances=sum(r['process_status']=='completed' for r in group),
                            feasible_verified_instances=len(valid), max_truck_trip_load_kg=best['max_truck_trip_load_kg'] if best else '',
                            instance=best['instance'] if best else '', trip_customers=best['trip_customers'] if best else ''))
    write_csv(args.output / 'truck_load_max_by_size.csv', summary)
    if args.summary_file:
        lines=['| Customers | Feasible Instances | Max Truck Trip Load (kg) | Instance |',
               '|---:|---:|---:|---|']
        lines += [f"| {r['customers']} | {r['feasible_verified_instances']}/16 | {r['max_truck_trip_load_kg']} | {r['instance']} |" for r in summary]
        args.summary_file.write_text('\n'.join(lines)+'\n')
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    matrix_parser = sub.add_parser('matrix')
    matrix_parser.add_argument('--root', type=Path, default=Path('instance_time_dependent'))
    runner = sub.add_parser('run')
    runner.add_argument('--root', type=Path, default=Path('instance_time_dependent'))
    runner.add_argument('--binary', type=Path, required=True)
    runner.add_argument('--instance', required=True)
    runner.add_argument('--output', type=Path, required=True)
    runner.add_argument('--wall-limit-sec', type=float, default=1800)
    runner.add_argument('--smoke-iterations', type=int)
    runner.add_argument('--source-commit', default='')
    runner.add_argument('--run-url', default='')
    agg = sub.add_parser('aggregate')
    agg.add_argument('--root', type=Path, default=Path('instance_time_dependent'))
    agg.add_argument('--input', type=Path, required=True)
    agg.add_argument('--output', type=Path, required=True)
    agg.add_argument('--summary-file', type=Path)
    args = parser.parse_args()
    if args.command == 'matrix':
        print(json.dumps(dict(instance=matrix(args.root))))
    elif args.command == 'run':
        run(args)
    else:
        aggregate(args)


if __name__ == '__main__':
    main()

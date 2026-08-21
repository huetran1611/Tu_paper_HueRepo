#!/usr/bin/env python3
import argparse
import json


def round4_configs():
    config_id = 1
    for kappa in [0.1, 0.3, 0.5, 1.0, 2.0]:
        for tau_v in [0.001, 0.005, 0.01, 0.02, 0.05]:
            yield {
                "config_id": config_id,
                "kappa": kappa,
                "tau_v": tau_v,
            }
            config_id += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-jobs", type=int, default=250)
    parser.add_argument("--seeds", default="1001,2002,3003")
    args = parser.parse_args()

    if args.num_jobs < 1:
        raise SystemExit("--num-jobs must be positive")

    seeds = [int(seed.strip()) for seed in args.seeds.split(",") if seed.strip()]
    tasks = []
    task_id = 1
    for config in round4_configs():
        for seed_index, seed in enumerate(seeds, start=1):
            tasks.append({
                "task_id": task_id,
                "round": "round4",
                "seed_index": seed_index,
                "seed": seed,
                **config,
            })
            task_id += 1

    num_jobs = min(args.num_jobs, len(tasks))
    shards = [[] for _ in range(num_jobs)]
    for index, task in enumerate(tasks):
        shards[index % num_jobs].append(task)

    matrix = []
    for shard_index, shard_tasks in enumerate(shards):
        matrix.append({
            "shard_id": f"{shard_index + 1:03d}",
            "task_count": len(shard_tasks),
            "tasks_json": json.dumps(shard_tasks, separators=(",", ":")),
        })

    print(json.dumps(matrix, separators=(",", ":")))


if __name__ == "__main__":
    main()

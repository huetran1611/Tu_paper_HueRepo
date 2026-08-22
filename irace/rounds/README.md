# Sequential irace rounds

Instance:

```txt
/Users/huetran/Tu_paper_HueRepo/instance_time_dependent/100.10.1.txt
```

Capacities passed by the runner:

```txt
truck capacity = 400 kg
drone capacity = 2.27 kg
```

Run a round from the repository root:

```bash
chmod +x irace/run_round.sh
irace/run_round.sh round1
```

Inspect elite configurations:

```bash
conda run -n base Rscript irace/show_elites.R irace/rounds/round1/irace.Rdata
```

Sequential workflow:

```txt
1. Run round1.
2. Copy the selected best values into irace/rounds/round2/fixed.args.
3. Run round2.
4. Copy best round2 values into round3/fixed.args.
5. Continue until round5.
```

The same instance appears three times in each `instances.txt` so racing evaluates repeated runs with different seeds. `irace` is still a racing method: it may discard poor configurations early. For exact exhaustive evaluation of every listed configuration exactly three times, use a grid-evaluation script rather than irace.


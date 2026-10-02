# Hanoi Selected 20

This folder contains the 20 retained instances from the original 33-instance
Hanoi experiment. Dataset names match the IDs in the experiment results:

set_03, set_05, set_07, set_08, set_09, set_11, set_12, set_14, set_15,
set_16, set_17, set_19, set_20, set_21, set_23, set_25, set_27, set_30,
set_32, set_33.

Each dataset folder includes the instance, truck and drone distance matrices,
weekday speed profile, theta and maximum-speed tables, node coordinates,
and metadata. File stems use the same dataset ID as their enclosing folder.
The weekday data retains the original 13 hours (6h through 18h); experiment
runners trim it to the 12 segments from 6h through 17h.

## Results

- `results/hanoi_20_departure_10seeds_results.xlsx`: 800 runs at departure
  hours 7h, 8h, 9h, and 10h with 10 seeds per instance and hour.
- `results/selected_20_instances_clearest_hour_gaps.xlsx`: selected-only
  instance means and the mean trend across departure hours.
- `results/hanoi_case_20_selected_p0_p1_p2_results.xlsx`: 3,400 optimizations
  and 7,200 evaluations for the three policies and three day profiles.
- `results/runs_v2_*_set_03/`: earlier local runs of the retained set_03,
  with filtered raw records and recomputed summaries.

## Run Configuration

`scripts/hanoi_selected_instances.py` defines the retained dataset IDs.
The departure and policy runners and their GitHub Actions workflows now
use this folder. Local source data and results for the other 13 instances
in this experiment have been removed. Other Hanoi experiment datasets
are separate from this selection.

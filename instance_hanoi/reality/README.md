# reality instances

This directory contains consolidated 1000-point Hanoi reality datasets built from `1000point_v1` and `1000point_v2`.

Each dataset uses the same file pattern, where `<id>` is `1000.1` or `1000.2`:

- `reality.<id>.txt`: solver instance file. Customer order and node ids match `reality.<id>.json`; x/y coordinates are the solver planar coordinates used for Euclidean drone distances.
- `reality.<id>.json`: original real-world lat/lon and OSRM metadata copied from the OSRM JSON source.
- `reality.<id>.nodes.csv`: joined node metadata with JSON lat/lon plus solver x/y and demand/service attributes.
- `reality.<id>.truck_distance_m.txt`: truck road-distance matrix copied from OSRM `<source>.distance_m.txt`.
- `reality.<id>.distance_m.txt`: compatibility copy of `reality.<id>.truck_distance_m.txt` for code/scripts that expect `<base>.distance_m.txt`.
- `reality.<id>.drone_distance_m.txt`: drone Euclidean distance matrix computed from `reality.<id>.txt` x/y coordinates.
- `reality.<id>.vmax_ij.txt`: truck edge base speed coefficients copied from the corresponding source.
- `reality.<id>.theta_ijl.txt`: truck edge/time smoothness coefficients copied from the corresponding source.
- `source_meta.json`: source paths, output paths, counts, and depot-distance summaries for all generated datasets.

## Real-only 200.10 subsets

`reality.200.10.1.*` and `reality.200.10.2.*` are generated from `reality.1000.1.*` using only real customers. The depot-centered 10000m x 10000m square contains 106 real customers, so each 200-customer subset includes all 106 inside-square customers plus 94 randomly sampled real customers from outside the square. No synthetic customers are used.

## Real OSM 200.10 Mile Subsets

`reality.200.10.1.*` and `reality.200.10.2.*` are real-only 200-customer datasets inside a depot-centered 10 mile x 10 mile square. Points are selected from `reality.1000.1` first, then unique points from `reality.1000.2`, then real OSM POIs from Overpass. No synthetic customers are used. Truck distances are generated with OSRM table distance, while drone distances are Euclidean from the x/y coordinates.

## Real OSM 500.10 Mile Subsets

`reality.500.10.1.*` through `reality.500.10.4.*` are real-only 500-customer datasets inside a depot-centered 10 mile x 10 mile square. Each dataset uses 154 unique points from `reality.1000.1`/`reality.1000.2` plus 346 real OSM POIs from Overpass. No synthetic customers are used. Truck distances are generated with OSRM table distance, while drone distances are Euclidean from the x/y coordinates. Any `theta_ijl = 1.00` values are regenerated with the l-specific min-max rule.

## Real-only 200.20 Mile Subsets

`reality.200.20.1.*` through `reality.200.20.4.*` are real-only 200-customer datasets inside a depot-centered 20 mile x 20 mile square. Points are randomly sampled from `reality.1000.1`; truck distances, base speeds, and time-dependent coefficients are sliced from the same source dataset, while drone distances are recomputed from x/y coordinates. Any `theta_ijl = 1.00` values are regenerated from the same l-specific min-max rule used for the `200.10` files.

## Real-only 200.30 Mile Subsets

`reality.200.30.1.*` through `reality.200.30.4.*` are real-only 200-customer datasets inside a depot-centered 30 mile x 30 mile square. Points are randomly sampled from `reality.1000.1`; truck distances, base speeds, and time-dependent coefficients are sliced from the same source dataset, while drone distances are recomputed from x/y coordinates. Any `theta_ijl = 1.00` values are regenerated from the same l-specific min-max rule used for the `200.10` files.

## Real-only 200.40 Mile Subsets

`reality.200.40.1.*` through `reality.200.40.4.*` are real-only 200-customer datasets inside a depot-centered 40 mile x 40 mile square. Points are randomly sampled from `reality.1000.1`; truck distances, base speeds, and time-dependent coefficients are sliced from the same source dataset, while drone distances are recomputed from x/y coordinates. Any `theta_ijl = 1.00` values are regenerated from the same l-specific min-max rule used for the `200.10` files.

## Real-only 500.20 Mile Subsets

`reality.500.20.1.*` through `reality.500.20.4.*` are real-only 500-customer datasets inside a depot-centered 20 mile x 20 mile square. Points are randomly sampled from `reality.1000.1`; truck distances, base speeds, and time-dependent coefficients are sliced from the same source dataset, while drone distances are recomputed from x/y coordinates. Any `theta_ijl = 1.00` values are regenerated from the same l-specific min-max rule used for the `200.10` files.

## Real-only 500.30 Mile Subsets

`reality.500.30.1.*` through `reality.500.30.4.*` are real-only 500-customer datasets inside a depot-centered 30 mile x 30 mile square. Points are randomly sampled from `reality.1000.1`; truck distances, base speeds, and time-dependent coefficients are sliced from the same source dataset, while drone distances are recomputed from x/y coordinates. Any `theta_ijl = 1.00` values are regenerated from the same l-specific min-max rule used for the `200.10` files.

## Real-only 500.40 Mile Subsets

`reality.500.40.1.*` through `reality.500.40.4.*` are real-only 500-customer datasets inside a depot-centered 40 mile x 40 mile square. Points are randomly sampled from `reality.1000.1`; truck distances, base speeds, and time-dependent coefficients are sliced from the same source dataset, while drone distances are recomputed from x/y coordinates. Any `theta_ijl = 1.00` values are regenerated from the same l-specific min-max rule used for the `200.10` files.

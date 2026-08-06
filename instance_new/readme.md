# Benchmark Dataset Specification

## Time-Dependent Multi-Trip Multi-Truck Multi-Drone Pickup Vehicle Routing Problem

---

# 1. Overview

This benchmark is designed to evaluate the value of time-dependent travel-time information for a multi-trip truck-drone pickup routing problem.

Each benchmark instance consists of

- one depot,
- one hundred customers,
- multiple trucks,
- multiple drones,
- one reference time-dependent traffic profile.

A corresponding static instance is **not stored**. Instead, it is generated automatically from the reference time-dependent profile by replacing the travel time of every truck arc with its average travel time over the entire planning horizon.

Consequently, each benchmark instance defines one paired comparison:

- Time-dependent instance
- Static instance derived from the time-dependent profile

---

# 2. Instance Generation

Generate

- 10 independent instances.

Each instance contains

- 1 depot
- 100 customers.

Random number generator

Every instance uses an independent random seed.

The seed must be stored in `metadata.json` to ensure complete reproducibility.

---

# 3. Coordinate System

The service region is

```
20 km × 20 km
```

The depot is fixed at

```
(0,0)
```

Customer coordinates are generated independently

```
x ~ Uniform(-10,10)

y ~ Uniform(-10,10)
```

The minimum Euclidean distance between every pair of nodes is

```
0.5 km
```

Otherwise regenerate the customer.

---

# 4. Distance Metrics

Truck travel distance

```
Euclid distance

dTruck(i,j)

=
sqrt((xi-xj)^2+(yi-yj)^2)
```

Drone travel distance

```
Euclidean distance

dDrone(i,j)

=
sqrt((xi-xj)^2+(yi-yj)^2)
```

Distance matrices are symmetric.

---

# 5. Fleet

Number of trucks

```
3
```

Number of drones

```
3
```

Truck capacity

```
150 kg
```

Drone payload capacity

```
5 kg
```

Both trucks and drones are allowed to perform multiple trips.

---

# 6. Drone Parameters

Vận tốc bay ngang: 60 km/h
Vận tốc cất cánh: 30km/h
Vận tốc hạ cánh:15km/h
drone capacity = 5kg
battery capacity of Emax 1.59 kWh
enerygy consumption E=(γ + βm)t, where γ=0.397 kW and β=0.066 kW/kg are

<!-- The drone parameters are calibrated with reference to the JOUAV CW-25E electric VTOL UAV. According to the manufacturer's specifications, the platform supports a payload of approximately 6 kg and has a nominal battery capacity of about 1.59 kWh. Based on these specifications, the effective energy consumption is approximated by a linear model

E=(γ + βm)t,

where γ=0.397 kW and β=0.066 kW/kg are calibrated coefficients selected such that the model reproduces the nominal endurance reported by the manufacturer under unloaded and full-payload operating conditions. -->


# 7. Customer Demand

Exactly
80%
of customers are drone eligible.
Drone-eligible customers

Demand ~ Uniform(0.5,5.0) kg

Truck-only customers 
Demand ~ Uniform(5.1,10.0) kg
```

Round demand to two decimal places.

---

# 8. Service Time

Truck: 1 miutes

Drone: 1 minites



# 9. Road Classes

Each truck arc belongs to one of three road classes.

For every arc

```
(i,j)
```

compute the midpoint

```
mx=(xi+xj)/2

my=(yi+yj)/2
```

Compute its Euclidean distance to the depot

```
r(i,j)
```

Road classes are assigned as

| Road class | Condition | Free-flow speed |
|------------|-----------|-----------------|
| Urban road | r ≤ 4 km | 30 km/h |
| Suburban road | 4 < r ≤ 7 km | 35 km/h |
| Arterial road | r > 7 km | 40 km/h |

The free-flow speed is symmetric
v_ij =v_ji

# 10. Planning Horizon

The planning horizon starts at

```
07:00
```

and ends at

```
12:00
```

The horizon is divided into ten equal intervals.

| Interval | Time |
|----------|-------------|
|1|07:00–07:30|
|2|07:30–08:00|
|3|08:00–08:30|
|4|08:30–09:00|
|5|09:00–09:30|
|6|09:30–10:00|
|7|10:00–10:30|
|8|10:30–11:00|
|9|11:00–11:30|
|10|11:30–12:00|

---

# 11. Reference Traffic Profile

Time-dependent speed coefficients are predetermined.

No random traffic noise is introduced.

The coefficients are

| Interval | Urban | Suburban | Arterial |
|----------|------:|---------:|---------:|
|07:00–07:30|0.65|0.72|0.80|
|07:30–08:00|0.50|0.60|0.70|
|08:00–08:30|0.40|0.50|0.60|
|08:30–09:00|0.45|0.55|0.65|
|09:00–09:30|0.60|0.68|0.75|
|09:30–10:00|0.70|0.78|0.82|
|10:00–10:30|0.80|0.86|0.90|
|10:30–11:00|0.85|0.90|0.95|
|11:00–11:30|0.80|0.86|0.90|
|11:30–12:00|0.70|0.78|0.82|

Truck speed is computed as

```
v(i,j,l)

=

v(i,j)

×

theta(class,l)
```

If a truck crosses an interval boundary, the speed must be updated using the speed of the next interval.

This rule preserves the FIFO property.

---

# 12. Static Travel Time

Static travel time is **not stored**.

Whenever a static instance is required, compute

```
travel_time(l)

=

distance

/

speed(l)
```

Then

```
StaticTravelTime(i,j)

=

Average

of

travel_time(l)

over

all ten intervals
```

Because every interval has equal duration (30 minutes), this is simply the arithmetic mean of the ten travel times.

---

# 13. Waiting-Time Limit

divided customer to 3 group equal the number of customrer based on distances to the deport:
nearest group: 30 min
middle group: 45 min
farest group: 60 min

# 14. Validation

Reject and regenerate an instance if

- two customers are closer than 0.5 km;
- the number of drone-eligible customers is not exactly 80;
- any customer cannot be directly served by either truck or drone;
- the generated instance is structurally infeasible.

---

# 15. Output Structure

```
instance001/

metadata.json

customers.txt
v_ij.txt
theta_ij.txt


# 16. metadata.json

Store

- random seed
- depot coordinates
- number of customers
- number of truck
- number of drone
- vehicle capacities
- drone enery
- drone endurance
- planning horizon
- time intervals

---

# 17. Programming Requirements

Implement the generator using

```
C++20
```

Use

```
std::mt19937_64
```

The code must be

- modular,
- object-oriented,
- deterministic,
- fully reproducible.

The generator should automatically validate every instance before writing it to disk.
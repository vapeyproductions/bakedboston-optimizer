# BakedBoston Schedule Simulation & Optimizer

BakedBoston is an academic operations-research demonstration of how volunteer
drivers could move hypothetical bakery surplus to food pantries under real-world
time-window constraints. The project is a **simulation, not an operating
delivery service**.

Real institution names, locations, and publicly listed schedules can be used as
read-only templates. Inclusion does not imply participation, affiliation,
available food, or consent to receive a delivery. Bakery surplus, pantry staff
attendance, drivers, and deliveries are synthetic and reproducible.

## What the demonstration does

For each simulated day, the system:

1. expands bakery and pantry schedules into time-specific occurrences;
2. applies one-time windows, monthly rules, and schedule exceptions;
3. samples hypothetical bakery surplus with a saved random seed;
4. treats unattended pantry windows as open and samples staff attendance for
   staffed windows;
5. generates synthetic Boston-area volunteer-driver requests;
6. removes time-infeasible driver–bakery–pantry combinations;
7. asks Gurobi to maximize completed pickups and then route quality;
8. records the virtual timeline, assignments, solver diagnostics, and outcomes.

Nothing in a simulation run writes to an organization record, creates an
account, sends a notification, or represents a real pickup.

## Gurobi mixed-integer model

For each feasible driver request \(d\), bakery occurrence \(b\), and pantry
window \(p\):

\[
x_{d,b,p} \in \{0,1\}
\]

Gurobi uses two lexicographic objectives:

1. maximize the number of assigned bakery pickup occurrences;
2. among maximum-coverage solutions, maximize pantry priority and route quality.

Candidate quality is:

\[
q_{d,b,p} = 45\,priority_p - driveMinutes_{d,b,p}
- 0.35\,waitingMinutes_{d,b,p}
- 0.65\,destinationMinutes_{d,b,p}
\]

Constraints ensure that each request and physical driver receives at most one
assignment, each bakery occurrence is used at most once, and every selected
route satisfies pickup, receiving, latest-arrival, and driver time windows.
Pantries intentionally have no one-delivery capacity constraint while open.

The complete formulation is in [docs/model.md](docs/model.md).

## Pantry priority by receiving opportunity

Priority is based on the pantry's last \(N\) simulated opportunities to receive
food—not an arbitrary seven-day period. If \(served_p\) of \(n_p\) recent open
windows received at least one assignment:

\[
priority_p = 1 - \frac{served_p + 1}{n_p + 2}
\]

Laplace smoothing gives a new pantry a neutral priority of 0.5. Missed
opportunities raise priority; recent service lowers it. A pantry is never
excluded merely for having received food.

## Reproducible rolling-horizon comparison

The primary academic experiment compares BakedBoston with five transparent
baselines over identical 3-, 4-, and 5-day seeded scenarios. The bundled public
replay uses a five-day horizon, nine fictional bakeries, nine fictional
pantries, sixty driver requests, and at most three drivers entering any one
decision epoch:

```bash
python3 -m bakedboston_optimizer.compare \
  data/academic_comparison_snapshot.json \
  --start-date 2026-08-24 \
  --horizons 3,4,5 \
  --seeds 2026,2027,2028 \
  --drivers-per-day 12 \
  --max-simultaneous-drivers 3 \
  --output comparison-result.json \
  --summary-csv comparison-summary.csv
```

Use `--disable-acceptance` for the deterministic routing-capacity comparison
shown on the public simulator: every driver selects the highest-scoring route
in their conflict-free recommendation list. A transparent behavioral model can
also be enabled as a separate sensitivity analysis; its expected acceptance and
likely-rejection measures are diagnostics rather than observed behavior.
`--matching-interval-minutes` controls how closely arriving drivers are grouped
into one network-wide Gurobi solve. The JSON contains the complete auditable
event trace; the CSV contains one analysis-ready row per horizon and policy.

The policies are Gurobi MIP, random feasible, shortest route, earliest bakery
deadline, highest pantry priority, and best driver-destination fit. Every policy
receives the same feasible routes and the same synthetic events. The detailed
protocol and metric definitions are in
[docs/simulation.md](docs/simulation.md).

The bundled academic comparison fixture is deliberately contention-rich: nine
fictional bakeries, nine fictional pantries, and fewer drivers than available
pickups force the policies to make meaningfully different choices. A fixed-seed
regression test verifies that the Gurobi policy attains the highest declared
system-objective value in this demonstration while the metrics still expose
tradeoffs in completion, distance, pantry reach, and equity. A baseline may
legitimately win an individual column; the MIP is optimal for its stated
lexicographic objective, not for every evaluation measure simultaneously.

## Driver recommendation trace

The five-day experiment also exports the exact menu shown to every synthetic
driver at every decision time. The sequence is explicit and auditable:

1. generate every time-feasible driver–bakery–pantry candidate;
2. solve all drivers entering the same decision epoch jointly, so a bakery
   pickup cannot be offered to two drivers;
3. remove routes whose bakery was assigned to another driver;
4. rank each driver's remaining routes from highest to lowest policy score; and
5. mark rank **#1** as the route selected by that driver.

The selected route is therefore always the highest-scoring route in the
conflict-free recommendation list actually displayed to that driver. The JSON
trace records `recommendationRank`, `routeScore`, `selected`, and `accepted`
for every displayed option, plus the selected route as `selectedRoute`.

Generate the compact payload used by the public simulator with:

```bash
python3 -m bakedboston_optimizer.web_export \
  data/academic_comparison_snapshot.json \
  --start-date 2026-08-24 \
  --days 5 \
  --seed 2026 \
  --drivers-per-day 12 \
  --max-simultaneous-drivers 3 \
  --output simulation-data.json
```

The interactive replay is published at
[baked-boston.com/simulator](https://www.baked-boston.com/simulator).

## Simple day-ahead replay

Requires Python 3.11+ and a non-production Gurobi license suitable for academic
work.

```bash
python3 -m bakedboston_optimizer.simulate \
  data/example_schedule_snapshot.json \
  --start-date 2026-08-24 \
  --days 7 \
  --seed 2026 \
  --drivers-per-day 8 \
  --output simulation-result.json
```

The bundled scenario is fictional and exists only to demonstrate the input
format. A research dataset should add a public source URL and verification date
for every real institution schedule. See [data/README.md](data/README.md) and
[docs/simulation.md](docs/simulation.md).

## Authenticated API mode

The optional HTTP wrapper can run the same read-only simulation from a schedule
snapshot supplied by the BakedBoston database:

```json
{
  "mode": "schedule_simulation",
  "startDate": "2026-08-24",
  "days": 7,
  "randomSeed": 2026,
  "driversPerDay": 8,
  "bakeryFoodProbability": 0.75,
  "staffedPantryOpenProbability": 0.9,
  "pantryHistorySize": 10
}
```

This mode reads schedule templates and returns a report. It performs no live
matching and has no write path.

## Outputs and evaluation

Each report includes:

- **bakery pickup coverage:** eligible food-ready pickup occurrences completed;
- **completed deliveries:** successful bakery-to-pantry assignments;
- **pantry coverage:** unique and percentage of available pantries served;
- **pantries never served:** count and fraction receiving no delivery;
- **distribution fairness:** pantry-service Gini coefficient and service gap;
- **mean route burden:** driving minutes, distance, waiting time, preferred-
  destination deviation, and total trip duration;
- **unserved pickups:** food-ready windows that expired without assignment;
- **driver acceptance:** deterministic selected-route share in the public replay,
  plus expected acceptance under the optional behavioral assumption;
- **rejection diagnostics:** predicted likely-rejected offer count and rate;
- **computational performance:** Gurobi runtime, status, and optimality gap;
- **objective evidence:** total declared system-objective value and feasible
  candidate count; and
- a timestamped event log containing every route recommended to each driver and
  the rank-1 route selected.

No policy is expected to win every column. A highest-priority-first rule can,
for example, produce a lower pantry-service Gini coefficient while leaving an
eligible pickup unserved. A shortest-route rule can minimize driving while
repeatedly serving fewer pantries. The MIP is evaluated against its declared
lexicographic objective: first maximize completed pickups, then maximize total
route quality across simultaneous drivers subject to assignment and timing
constraints.

## Repository structure

```text
bakedboston_optimizer/
  experiment.py       Rolling-horizon policies, acceptance, evaluation
  compare.py          3/4/5-day policy-comparison CLI
  simulation.py       Virtual schedule clock, seeded events, metrics
  simulate.py         Local scenario runner
  optimizer.py        Candidate feasibility and Gurobi MIP
  models.py           Typed inputs, assignments, diagnostics
  network.py          Read-only schedule snapshot parser
  travel.py           Deterministic travel-time interface
data/
  academic_comparison_snapshot.json
  example_schedule_snapshot.json
docs/
  model.md
  simulation.md
api/
  recommendations.py
tests/
```

## Verification

```bash
python3 -m compileall -q bakedboston_optimizer api tests
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The tests cover Gurobi assignment constraints, feasibility, schedule expansion,
monthly/one-time semantics, exceptions, pantry priority, API dispatch, seeded
reproducibility, rolling-horizon policy comparisons, conflict-free assignments,
fairness metrics, and 3/4/5-day reports.

## Research extensions

- calibrate objective weights and run sensitivity analysis;
- test robustness across denser schedules and more random seeds;
- calibrate the transparent acceptance function with future consented data;
- add traffic scenarios while retaining deterministic replay; and
- add confidence intervals and publication-ready plots.

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
3. draws hypothetical bakery food amount and usability from each bakery's own
   fixed triangular distributions with a saved random seed;
4. treats unattended pantry windows as open and samples staff attendance for
   staffed windows;
5. generates synthetic Boston-area volunteer-driver requests;
6. removes time-infeasible driver–bakery–pantry combinations;
7. estimates each route's probability of volunteer acceptance with an explicit,
   synthetic participation model;
8. asks Gurobi to maximize expected completed pickups and then, within 1% of
   that optimum, maximize a normalized food, fairness, environmental, and
   driver-fit objective;
9. records the virtual timeline, assignments, solver diagnostics, and outcomes.

Nothing in a simulation run writes to an organization record, creates an
account, sends a notification, or represents a real pickup.

## Gurobi mixed-integer model

Before the MIP runs, the route generator creates timed route columns. Each
column records a driver request, bakery occurrence, pantry window, optimized
departure, pickup, pantry arrival, and finish time. Login is the decision time,
not the departure time. For each feasible timed column \(a\):

\[
x_a \in \{0,1\}
\]

Gurobi uses two hierarchical objectives:

1. maximize the expected number of completed bakery pickup occurrences;
2. among solutions retaining at least 99% of the best first-stage value,
   maximize pantry priority and sustainable-logistics quality.

For route \(r\), the first-stage coefficient is an inspectable scenario
estimate—not observed behavior or a trained ML model:

\[
a_r = \sigma(2.2 - 0.045\,driveMinutes_r
- 1.8\,outsideWindowRatio_r
- 1.1\,spatialDeviationRatio_r)
\]

The optimizer's first stage is therefore

\[
\max \sum_{r\in R} a_r x_r.
\]

This directly tests BakedBoston's thesis: a route that technically moves food
is not useful if its burden makes a volunteer unlikely to accept it. The
coefficients are reproducible academic assumptions and must not be presented as
empirically calibrated until real choice data exist.

For bakery \(b\), pantry \(p\), and simulated day \(d\), route food saved is:

\[
H_{bpd}=Q_{bd}U_{bd}D_p.
\]

Food-available bakery occurrences with no completed pickup are recorded as
uncollected bakery food. For a completed route, \(Q-H\) is collected food not
ultimately distributed. Each bakery's fixed landfill, pig-farm, and compost mix
values those two cases; tonne-kilometre transport emissions are then subtracted.
Avoided production is held at zero in the primary score.

The normalized second-stage objective is

\[
10C+10V_Q+10F_Q+10V_H+10F_H+10P+20E+20D,
\]

covering pantry reach, raw and saved-food volume/evenness, opportunity priority,
net direct CO₂ benefit, and driver fit. See [docs/model.md](docs/model.md) for
the full formulation and [docs/institutions.md](docs/institutions.md) for every
fixed institution input, waste allocation, distribution fraction, and
environmental coefficient.

Opening the app creates a decision epoch; it does not force immediate
departure. The route generator chooses a just-in-time departure, includes 5
minutes to load at the bakery and 5 minutes to unload at the pantry, and shows
the volunteer when to leave. Waiting safely before that departure is not a
route-quality penalty. Instead,

\[
outsideWindowRatio =
\frac{\max(0, requestedStart-departure)+\max(0, finish-requestedFinish)}
{requestedFinish-requestedStart}
\]

Only route time before or after the driver's requested interval is penalized.
Dividing by the requested interval makes the same miss matter more for a tight
request than for a broad one. Driver requests must span at least 30 minutes;
the interval remains a soft preference, so a near miss may still be shown when
it is the best feasible option.

The optional starting and ending ZIP codes are soft preferences. Each ZIP is
represented by a small estimated circular area around its center. The raw
misses for route \(r=(d,b,p)\) are the shortest straight-line distances from
the bakery and pantry to the corresponding requested areas:

\[
\delta^{start}_r = \max\{0,\ distance(b,startZIP_d)-radius^{start}_d\}
\]

\[
\delta^{end}_r = \max\{0,\ distance(p,endZIP_d)-radius^{end}_d\}
\]

A facility inside its requested area has deviation zero. It receives no bonus;
routes farther away only receive a penalty. For each driver, the two deviations
are scaled against the largest corresponding deviation among that driver's
feasible alternatives:

\[
spatialDeviationRatio_r = \frac{1}{K}
\left(
\frac{\delta^{start}_r}{\max_{j\in R_d}\delta^{start}_j}
+
\frac{\delta^{end}_r}{\max_{j\in R_d}\delta^{end}_j}
\right)
\]

where \(K\) is the number of supplied spatial preferences (one or two) and a
component whose maximum is zero contributes zero. Thus the route with the
smallest geographic miss is favored relative to the driver's other feasible
choices without imposing a hard geographic cutoff.

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

The public academic experiment compares BakedBoston with a distance-first
adaptation of Nair, Rashidi, and Dixit's food-rescue pickup-and-delivery model
and a Total-Curb adaptation of Xue and Zou's carbon-aware meal-delivery model,
plus a stochastic-menu adaptation of Horner, Pazour, and Mitchell's SLSF-noZ model
over identical five-day seeded scenarios. The bundled public replay uses nine
fictional bakeries, nine fictional pantries, synthetic volunteer requests, and
at most three drivers entering any one decision epoch:

```bash
python3 -m bakedboston_optimizer.compare \
  data/academic_comparison_snapshot.json \
  --start-date 2026-08-24 \
  --horizons 5 \
  --seeds 2026,2027,2028 \
  --drivers-per-day 12 \
  --max-simultaneous-drivers 3 \
  --output comparison-result.json \
  --summary-csv comparison-summary.csv
```

Use `--disable-acceptance` for a deterministic routing-capacity sensitivity
analysis. In a participation-aware sensitivity experiment, the first-stage MIP
uses expected acceptance while the event replay samples accept/reject outcomes
from the same transparent behavioral assumptions. Expected acceptance and
likely-rejection measures remain model-based diagnostics, not observed behavior.
`--matching-interval-minutes` controls how closely arriving drivers are grouped
into one network-wide Gurobi solve. The JSON contains the complete auditable
event trace; the CSV contains one analysis-ready row per horizon and policy.

The public policies are the BakedBoston Gurobi MIP, the Nair et al. (2018)
distance-first adaptation, the Xue-Zou (2025) Total-Curb adaptation, and the
Horner et al. (2021) stochastic-menu adaptation. The Horner comparator creates
menus of at most five routes over 100 seeded willingness scenarios, then makes
a final recourse assignment among routes drivers signal they would accept. The
command-line runner also retains the older random, shortest-route,
earliest-deadline, pantry-priority, and driver-fit heuristics for internal
sensitivity work. Every model receives the same realized scenario and feasible
routes, while each selector reads only the inputs represented in its
formulation. The detailed protocol and metric definitions are in
[docs/simulation.md](docs/simulation.md); the exact paper-adaptation boundary is in
[docs/comparison-models.md](docs/comparison-models.md).

The bundled academic comparison fixture is deliberately contention-rich: nine
fictional bakeries, nine fictional pantries, and fewer drivers than available
pickups force the models to make meaningfully different choices. The website
reports food recovered/wasted, direct CO₂e, average driving time/distance, and
sigmoid-based likely acceptance/rejection alongside coverage and fairness. A
model may legitimately win an individual measure; each is optimized only for
its own declared objective.

## Research foundation

BakedBoston adapts established OR structures rather than claiming that food
rescue routing is a new problem class:

- [Nair et al. (2018)](https://doi.org/10.1016/j.seps.2017.06.003) integrate
  scheduling, assignment, and pickup-and-delivery routing for food rescue.
- [Xue and Zou (2025)](https://doi.org/10.1016/j.clscn.2025.100253) integrate
  order, food-waste, and vehicle emissions in open pickup-and-delivery routing;
  the public Total-Curb adaptation isolates their total-emissions objective
  without inventing multi-stop routes or driver-familiarity observations.
- [Horner, Pazour, and Mitchell (2021)](https://doi.org/10.1016/j.tre.2021.102419)
  optimize personalized driver menus under stochastic willingness and make a
  final recourse assignment. The public adaptation uses their SLSF-noZ variant
  so no unsupported compensation or unhappy-driver penalty is introduced.
- [Hernandez-Perez and Salazar-Gonzalez (2007)](https://doi.org/10.1002/net.20209)
  provide an exact formulation for the one-commodity pickup-and-delivery
  traveling-salesman problem, a useful structural ancestor for unpaired surplus
  pickup and pantry delivery.
- [Rey, Almi'ani, and Nair (2018)](https://doi.org/10.1016/j.tre.2018.02.001)
  model envy-free food-rescue allocation, while
  [Orgut et al. (2018)](https://doi.org/10.1016/j.ejor.2018.02.017) study robust,
  equitable donated-food distribution. These motivate measuring service gaps
  and rewarding opportunity-based pantry priority rather than minimizing miles
  alone.
- [The online VRP with occasional drivers (2021)](https://doi.org/10.1016/j.cor.2020.105144)
  and [optimization of driver menus under stochastic selection (2021)](https://doi.org/10.1016/j.tre.2021.102419)
  motivate rolling decision epochs and explicit route acceptance.
- [Beyond efficiency (2025)](https://doi.org/10.1016/j.orl.2025.107344) examines
  the cost of prioritizing driver satisfaction in vehicle routing, supporting
  the project's central evaluation question: can preference-aware logistics
  preserve social impact while improving volunteer participation?
- [Guo et al. (2026)](https://doi.org/10.3390/foods15040645) compare eight food-
  donation and redistribution scenarios with life-cycle assessment. Their
  results motivate treating environmental performance as avoided production
  and disposal minus transportation and redistribution-waste burdens—not as a
  synonym for shortest distance. They also show why food usability, sorting,
  disposal counterfactuals, and substitution assumptions belong in sensitivity
  analysis.

The papers motivate the structure and evaluation criteria; BakedBoston's
current participation coefficients are still synthetic. The implementation
does **not** claim to use the papers' exact algorithms, learned parameters, or
data. See [docs/research.md](docs/research.md) for the implemented-versus-future
mapping.

## Driver recommendation trace

The five-day experiment also exports the exact menu shown to every synthetic
driver at every decision time. The sequence is explicit and auditable:

1. generate every time-feasible driver–bakery–pantry candidate;
2. solve all drivers entering the same decision epoch jointly, so a bakery
   pickup cannot be offered to two drivers;
3. assign each bakery pickup to one temporary menu owner while allowing the
   same pantry to appear in multiple drivers' routes;
4. build menus in fairness layers: maximize the number of drivers receiving
   recommendation #1 before optimizing its quality, then do the same for
   recommendation #2, and so on; and
5. mark rank **#1** as the route selected by that driver.

This cardinality-first rule prevents a driver from receiving zero options just
so another driver can receive an extra option. Every simultaneous driver gets
one distinct feasible bakery pickup before anyone gets a second, and every
driver gets a second before anyone gets a third. A zero-option menu remains
possible only when there are fewer distinct feasible bakery pickups than active
drivers, or when that driver has no time-feasible route. Because pantry windows
can receive multiple donations, pantry overlap does not create a conflict.

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
  --seed 2033 \
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
  --days 5 \
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
  "days": 5,
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
- **mean route burden:** driving minutes, distance, requested-time deviation,
  preferred-destination deviation, and total trip duration;
- **unserved pickups:** food-ready windows that expired without assignment;
- **driver acceptance:** deterministic selected-route share in the public replay,
  plus expected acceptance under the optional behavioral assumption;
- **rejection diagnostics:** predicted likely-rejected offer count and rate;
- **computational performance:** Gurobi runtime, status, and optimality gap;
- **food and environmental performance:** ultimately saved food, bakery food
  not picked up, collected food not ultimately distributed, and one net direct
  kg CO₂e benefit;
- **Balanced Total Impact:** a post-hoc, scenario-relative six-pillar index
  giving equal weight to completed service, food recovery, net environmental
  benefit, distribution equity, volunteer fit, and route efficiency; this is a
  communication aid rather than an optimizer objective or externally validated
  social-impact measure;
- **objective evidence:** total declared system-objective value and feasible
  candidate count; and
- a timestamped event log containing every route recommended to each driver and
  the rank-1 route selected.

No policy is expected to win every column. A highest-priority-first rule can,
for example, produce a lower pantry-service Gini coefficient while leaving an
eligible pickup unserved. A shortest-route rule can minimize driving while
repeatedly serving fewer pantries. The MIP is evaluated against its declared
hierarchical objective: first maximize expected completed pickups, then
maximize social and sustainable-logistics quality within 1% of that best
expected-completion value, subject to assignment and timing constraints.

## Repository structure

```text
bakedboston_optimizer/
  environment.py      Transparent waste-versus-transport CO2e accounting
  experiment.py       Rolling-horizon policies, acceptance, evaluation
  compare.py          Five-day policy-comparison CLI
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
  research.md
  simulation.md
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
fairness metrics, and five-day reports.

## Research extensions

- calibrate objective weights and run sensitivity analysis;
- test robustness across denser schedules and more random seeds;
- calibrate the transparent acceptance function with future consented data;
- add traffic scenarios while retaining deterministic replay; and
- add confidence intervals and publication-ready plots.

# Academic schedule simulation

## Boundary

The simulator is deliberately sealed from real operations. It may read public
institution names, locations, and schedules, but it does not:

- create or connect an institution account;
- contact an institution or volunteer;
- claim that surplus food exists;
- reserve or complete a real delivery;
- update a schedule or operational database.

Every output must retain the non-affiliation disclaimer included in
`SimulationReport.as_dict()`.

## Deterministic inputs

The following are fixed for an experiment:

- institution coordinates;
- recurring, monthly, and one-time schedule windows;
- service mode (staffed or unattended);
- schedule exceptions;
- Gurobi constraints and objective weights;
- simulation dates, time zone, and random seed.

Each real schedule record should include its public source URL and the date on
which it was checked. Any inferred value must be labeled as a modeling
assumption.

## Synthetic events

The seed controls three uncertain inputs:

1. whether a scheduled bakery occurrence generates hypothetical surplus;
2. whether staff are available for a staffed pantry occurrence;
3. where and when synthetic volunteer drivers are available, including whether
   they supply a preferred destination; and
4. each bakery's daily food and usability draws from its own fixed triangular
   distributions.

Unattended pantry windows are treated as available whenever the public schedule
window is active.

## Virtual timeline and rolling decisions

For each simulated day, schedule templates are expanded into concrete,
timezone-aware occurrences. The comparison engine groups synthetic driver
arrivals into decision epochs (15 minutes by default). At each epoch it:

1. enumerates every feasible driver–bakery–pantry route for the drivers who
   arrived at that epoch;
2. calculates the same route-quality score for every feasible route;
3. applies the selected routing policy;
4. samples the transparent driver-acceptance response;
5. removes an accepted bakery pickup from future epochs; and
6. re-solves when the next group of drivers arrives.

This is a rolling-horizon experiment: later decisions see the consequences of
earlier accepted routes. A rejected offer does not consume the pickup, so it may
be offered at a later epoch. The older `simulate` command remains available as a
simple day-ahead replay; `compare` is the primary experimental runner.

## Routing policies

Every policy receives the exact same feasible candidate set. Time-window and
location validity are therefore constraints for all policies, not advantages
given only to BakedBoston.

- **BakedBoston MIP:** maximizes expected completed pickups, then retains at
  least 99% of that value while balancing normalized pantry coverage, raw and
  saved-food volume/evenness, opportunity priority, net direct CO₂e benefit,
  and driver fit.
- **Random feasible:** selects a seeded random conflict-free assignment.
- **Shortest route:** greedily minimizes driving time.
- **Earliest deadline:** greedily serves the bakery pickup with the earliest
  pickup deadline.
- **Highest priority:** greedily serves the pantry with the highest current
  opportunity-based priority.
- **Driver fit:** greedily minimizes distance from the pantry to the driver's
  optional preferred destination, then route burden.

The old idea of maximizing the average score of a five-route menu per driver is
not used. It can hide low total service and does not resolve scarce-bakery
conflicts cleanly. The model instead chooses one conflict-free primary assignment
per driver request; alternative recommendation menus can be generated afterward
without changing the allocation objective.

For the interactive replay, those menus are generated only after the joint
assignment is known. A bakery assigned to another simultaneous driver is removed
from the current driver's selectable menu. The remaining candidates are sorted
by policy score, and the selected assignment is recorded as rank #1. Each
decision-epoch trace therefore shows both the complete recommendation list a
driver received and the highest-scoring route they selected.

## Synthetic driver acceptance

An offered route is accepted with a seeded logistic probability that decreases
with drive minutes, the proportional requested-time-window miss, and normalized
start/destination-area miss. Planned waiting before departure is not treated as
a burden. The same transparent estimate supplies the primary MIP's first-stage
coefficient, so participation is part of the routing decision rather than only
an after-the-fact dashboard diagnostic. This is an explicit behavioral
assumption, not a trained machine-learning model. It can be disabled for a pure
routing-capacity sensitivity analysis.

## Pantry opportunity history

An opportunity is one open pantry receiving-window occurrence. For each pantry,
the simulator stores whether at least one assignment was made during each of the
last \(N\) opportunities. This means a pantry open once per month is evaluated
against its recent actual opportunities, not against an arbitrary seven-day
calendar window.

## Reproducibility

A result is reproducible when the schedule snapshot, configuration, code
version, Gurobi version, and random seed are saved. Solver wall-clock runtime may
vary; selected assignments and synthetic events should not.

## Evaluation metrics

The comparison report includes:

- bakery pickup coverage and unserved food-available pickup occurrences;
- completed deliveries;
- pantry coverage as both a count and percentage;
- pantries never served as both a count and percentage;
- distribution fairness as a pantry-service Gini coefficient and service gap;
- mean drive time, distance, preferred-destination deviation, and
  total trip duration;
- ultimately saved food \(Q\times U\times D\), bakery food not picked up, and
  collected food not ultimately distributed;
- one net direct kg CO₂e result;
- offers, accepted routes, simulated rejections, expected acceptance, likely
  rejections, and likely-rejection rate;
- feasible candidates examined and the declared system-objective value; and
- Gurobi runtime, solve status, and MIP gap.

The standard experiment evaluates a five-day horizon, optionally over multiple
random seeds, and reports the mean of every metric for each policy.
Passing `--summary-csv` also writes one flat row per horizon and policy for
spreadsheet analysis and presentation charts; the JSON remains the complete
auditable event trace.
Run once with acceptance enabled for the primary participation-aware experiment
and once with `--disable-acceptance` to isolate deterministic routing capacity.
Reporting both prevents the behavioral assumption from being mistaken for an
observed result.

## Bundled comparison fixture

`data/academic_comparison_snapshot.json` is the primary reproducible experiment.
Its organizations and schedules are fictional. The fixture intentionally has
more food-available bakery occurrences than drivers, overlapping pantry windows,
different geography, and multiple defensible routing choices. This prevents a
trivial case in which every policy makes the same assignment.

The bundled five-day public replay contains nine fictional bakeries, nine
fictional pantries, and sixty driver requests, with no more than three drivers
entering one decision epoch. For the fixed seed used by the regression suite,
BakedBoston's Gurobi policy has the greatest declared system-objective value and
completes every eligible pickup. The highest-priority baseline can produce the
lowest Gini coefficient while leaving a pickup unserved, and the earliest or
shortest policy can reduce a route-burden component while serving fewer unique
pantries. These are real tradeoffs, so the dashboard highlights the best value
in each column instead of implying that the MIP must dominate every metric.

# Gurobi-backed network assignment model

BakedBoston solves a mixed-integer assignment problem over synthetic driver requests, hypothetical bakery-surplus occurrences, and pantry schedule windows. Candidate routes are generated first; Gurobi then chooses a globally consistent set of assignments instead of optimizing each driver independently. Real institutions may supply the public schedule geometry of an experiment, but all operational events are simulated.

The route-generation sequence is fixed throughout the project:

1. form every driver–bakery–pantry combination;
2. remove combinations that violate a hard constraint;
3. score every remaining feasible route;
4. pass the complete feasible set to the allocation policy; and
5. rank or assign routes from that set.

No shortcut policy is used to decide which candidates the MIP is allowed to see.

## Timed route columns, sets, and decision variables

The MIP does not equate a login with a departure. Before optimization, the
candidate generator creates a timed route column

\[
a=(d,b,p,t^{depart},t^{pickup},t^{arrival},t^{finish})
\]

for each useful driver–bakery–pantry schedule. A column therefore contains the
complete plan: when the driver should leave, when pickup occurs, when pantry
arrival occurs, and when the trip finishes. For the current academic-sized
instances, the generator enumerates useful just-in-time departure breakpoints
and retains the best feasible timed plan for each driver–bakery–pantry
combination. Larger instances could generate these columns dynamically with
column generation.

- \(D\): synthetic driver requests
- \(B\): simulated bakery-surplus occurrences
- \(P\): eligible pantry receiving-window occurrences
- \(A\): feasible timed route columns
- \(x_a \in \{0,1\}\): 1 when timed route column \(a\) is selected

Each pantry window is represented as a time-specific occurrence. An unattended window is treated as open in the experiment; attendance at a staffed window is a seeded synthetic event.

## Timing and feasibility

For every potential \((d,b,p)\), the feasibility engine treats the driver's
login as a **decision epoch**, not a required departure. It evaluates a finite
set of useful departure breakpoints and propagates:

1. waiting at the driver's origin until the proposed departure;
2. traffic-aware travel to the bakery;
3. waiting until the pickup is ready, if needed;
4. 5 minutes for pickup;
5. travel from the bakery to the pantry;
6. waiting until the pantry opens, if needed;
7. 5 minutes for drop-off.

The model uses fixed 5-minute pickup and 5-minute drop-off service times. The
assignment enters \(A\) only if:

- the driver can reach the bakery by its pickup deadline;
- the pantry is open and the delivery arrives by its latest permitted arrival;
- the completed trip fits inside an outer search horizon;
- the simulated bakery-surplus event is available;
- the bakery and pantry schedule templates have validated coordinates;
- the pantry window is not paused or cancelled;
- the synthetic staffed-attendance event is available.

## Pantry priority

The fairness metric uses the last \(N\) open receiving opportunities. Let \(n_p\) be the number of recent opportunities and \(served_p\) the number that received at least one simulated assignment:

\[
priority_p = 1 - \frac{served_p + 1}{n_p + 2}
\]

Laplace smoothing gives a pantry with no recorded opportunities a neutral priority of 0.5. Missed opportunities raise priority, while recent service lowers it. A pantry always remains feasible, so a nearby lower-priority pantry can still be selected rather than discarding a viable pickup.

The preferred trip interval is deliberately **not** in that hard-feasibility
list. A route may depart before the preferred start or finish after the
preferred finish, but each minute of deviation lowers its score. This avoids
returning no options when a useful route narrowly misses a volunteer's stated
preference.

An optional starting ZIP replaces the profile origin for that request. Starting
and ending ZIP codes are soft spatial preferences represented by estimated
circular areas. A route is penalized only for its shortest straight-line miss
outside those areas; an exact match has zero deviation and receives no bonus.

## Candidate quality

For feasible assignment \((d,b,p)\):

\[
quality_{d,b,p} =
45 \cdot priority_p
- driveMinutes_{d,b,p}
- 24 \cdot outsideWindowRatio_{d,b,p}
- 18 \cdot spatialDeviationRatio_{d,b,p}
\]

where:

- `outsideWindowRatio` is the route's minutes outside the requested interval,
  divided by the length of that interval; and
- `spatialDeviationRatio` compares the bakery's miss from the requested
  starting ZIP area and the pantry's miss from the requested ending ZIP area
  with the corresponding misses of every other feasible route for that driver.

More precisely:

\[
outsideMinutes = \max(0, requestedStart-departure)
+ \max(0, finish-requestedFinish)
\]

\[
outsideWindowRatio = \frac{outsideMinutes}{requestedWindowMinutes}
\]

The request interval must be at least 30 minutes. Waiting between login and a
planned later departure is displayed to the driver but does not reduce route
quality. The generator schedules departure as late as feasibility permits, so
avoidable facility waiting is removed rather than priced into the objective.
For example, a 30-minute miss against a 30-minute request has ratio 1.0 and a
24-point penalty; a 10-minute miss against a four-hour request has ratio
10/240 and only a 1-point penalty.

For route \(r=(d,b,p)\), define the raw spatial misses:

\[
\delta^{start}_r = \max\{0,\ distance(b,startZIP_d)-radius^{start}_d\}
\]

\[
\delta^{end}_r = \max\{0,\ distance(p,endZIP_d)-radius^{end}_d\}
\]

The distance is measured to the closest edge of the estimated ZIP circle, so a
facility inside the area has a zero-mile miss. For each driver request, each
applicable miss is divided by the largest miss of that type among the driver's
feasible routes. The final `spatialDeviationRatio` is the mean of the available
normalized components. If every alternative has zero deviation for one
component, that component contributes zero. This creates a relative penalty in
\([0,1]\), favors the closest alternatives, and never rewards an exact match.

The weights are centralized in `OptimizationWeights` so they can be tested and tuned without changing the constraints.

## Lexicographic objectives

The Gurobi model uses two ordered objectives.

First, maximize the number of bakery pickups assigned:

\[
\max \sum_{a \in A} x_a
\]

Second, among solutions with the same number of assignments, maximize route quality:

\[
\max \sum_{a \in A} quality_a x_a
\]

This ordering prevents a negative route score from causing viable food to be left unmatched. The solver first saves as many pickups as possible, then chooses the fairest and most travel-efficient version of that maximum-coverage solution.

This is deliberately **not** an average-score objective. Maximizing the average
of recommendation menus can improve the displayed mean while serving fewer
pickups. Lexicographic maximum coverage makes the operational priority explicit
and gives route quality control only after coverage has been fixed.

At each rolling decision epoch, the model solves over all drivers who arrived in
that epoch and all pickups still available. Accepted assignments consume their
bakery pickup. A rejected offer leaves the pickup available for a later solve.

This is a compact rolling-horizon pickup-and-delivery formulation with hard
institutional time windows and soft volunteer preferences. It follows the same
general OR pattern used in dynamic vehicle routing and crew scheduling: create
feasible timed duties, attach preference penalties, then select a globally
consistent set at each decision epoch. BakedBoston can enumerate its small
academic instances directly; column generation would be the natural scaling
path if the feasible-route set became too large to enumerate.

## Constraints

Each driver request receives at most one assignment:

\[
\sum_{a \in A:\,driverRequest(a)=d} x_a \leq 1 \qquad \forall d \in D
\]

Each physical driver receives at most one simultaneous assignment, even if multiple active requests exist:

\[
\sum_{a \in A:\,driver(a)=v} x_a \leq 1 \qquad \forall v
\]

Each bakery pickup can be assigned only once:

\[
\sum_{a \in A:\,pickup(a)=b} x_a \leq 1 \qquad \forall b \in B
\]

There is intentionally no one-delivery capacity constraint on pantries. A pantry may receive multiple orders while its window is open.

## Solver result and diagnostics

The API returns the selected assignments plus:

- solver backend;
- optimization status;
- feasible candidate count;
- matched assignment count;
- total route quality;
- runtime;
- MIP gap when Gurobi exposes it.

The academic Gurobi run fails closed: if Gurobi or its license is unavailable, the service returns an error rather than silently describing a heuristic as a Gurobi result. An explicitly named deterministic fallback exists only for development comparisons.

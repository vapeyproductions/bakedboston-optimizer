# Gurobi-backed network assignment model

BakedBoston solves a mixed-integer assignment problem over synthetic driver requests, hypothetical bakery-surplus occurrences, and pantry schedule windows. Candidate routes are generated first; Gurobi then chooses a globally consistent set of assignments instead of optimizing each driver independently. Real institutions may supply the public schedule geometry of an experiment, but all operational events are simulated.

The route-generation sequence is fixed throughout the project:

1. form every driver–bakery–pantry combination;
2. remove combinations that violate a hard constraint;
3. score every remaining feasible route;
4. pass the complete feasible set to the allocation policy; and
5. rank or assign routes from that set.

No shortcut policy is used to decide which candidates the MIP is allowed to see.

## Sets and decision variables

- \(D\): synthetic driver requests
- \(B\): simulated bakery-surplus occurrences
- \(P\): eligible pantry receiving-window occurrences
- \(A \subseteq D \times B \times P\): feasible driver–pickup–pantry assignments
- \(x_{d,b,p} \in \{0,1\}\): 1 when driver request \(d\) is assigned pickup \(b\) and pantry window \(p\)

Each pantry window is represented as a time-specific occurrence. An unattended window is treated as open in the experiment; attendance at a staffed window is a seeded synthetic event.

## Timing and feasibility

For every potential \((d,b,p)\), the feasibility engine propagates:

1. the driver's earliest allowed departure;
2. traffic-aware travel to the bakery;
3. waiting until the pickup is ready, if needed;
4. 5 minutes for pickup;
5. travel from the bakery to the pantry;
6. waiting until the pantry opens, if needed;
7. 5 minutes for drop-off.

The assignment enters \(A\) only if:

- the driver can reach the bakery by its pickup deadline;
- the pantry is open and the delivery arrives by its latest permitted arrival;
- the completed trip fits inside the driver's requested time window;
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

## Candidate quality

For feasible assignment \((d,b,p)\):

\[
quality_{d,b,p} =
45 \cdot priority_p
- driveMinutes_{d,b,p}
- 0.35 \cdot waitingMinutes_{d,b,p}
- 0.65 \cdot destinationMinutes_{d,b,p}
\]

The weights are centralized in `OptimizationWeights` so they can be tested and tuned without changing the constraints.

## Lexicographic objectives

The Gurobi model uses two ordered objectives.

First, maximize the number of bakery pickups assigned:

\[
\max \sum_{(d,b,p) \in A} x_{d,b,p}
\]

Second, among solutions with the same number of assignments, maximize route quality:

\[
\max \sum_{(d,b,p) \in A} quality_{d,b,p} x_{d,b,p}
\]

This ordering prevents a negative route score from causing viable food to be left unmatched. The solver first saves as many pickups as possible, then chooses the fairest and most travel-efficient version of that maximum-coverage solution.

This is deliberately **not** an average-score objective. Maximizing the average
of recommendation menus can improve the displayed mean while serving fewer
pickups. Lexicographic maximum coverage makes the operational priority explicit
and gives route quality control only after coverage has been fixed.

At each rolling decision epoch, the model solves over all drivers who arrived in
that epoch and all pickups still available. Accepted assignments consume their
bakery pickup. A rejected offer leaves the pickup available for a later solve.

## Constraints

Each driver request receives at most one assignment:

\[
\sum_{(d,b,p) \in A} x_{d,b,p} \leq 1 \qquad \forall d \in D
\]

Each physical driver receives at most one simultaneous assignment, even if multiple active requests exist:

\[
\sum_{(d,b,p) \in A:\, driver(d)=v} x_{d,b,p} \leq 1 \qquad \forall v
\]

Each bakery pickup can be assigned only once:

\[
\sum_{(d,b,p) \in A} x_{d,b,p} \leq 1 \qquad \forall b \in B
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

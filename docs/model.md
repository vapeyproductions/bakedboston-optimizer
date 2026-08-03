# Mathematical model

## Decision context

BakedBoston matches time-sensitive bakery pickup windows with volunteer drivers and pantries. Food quantity and type are deliberately outside the first model. A bakery pickup can be used once; a pantry can receive more than one delivery while it is available.

The implementation has two connected layers:

1. a feasibility engine propagates time through every possible driver–bakery–pantry route;
2. an OR-Tools mixed-integer program selects the best simultaneous assignments.

## Sets

- \(D\): available drivers or active driver requests
- \(B\): confirmed, unclaimed bakery pickup occurrences
- \(P\): open pantry receiving windows
- \(F \subseteq D \times B \times P\): complete routes that pass every hard feasibility rule

Pantry receiving windows, rather than pantry organizations alone, belong to \(P\). This preserves the correct latest-arrival time and staffed/unattended receiving mode for a particular visit.

## Feasibility propagation

For each possible \((d,b,p)\), the engine calculates:

1. driver departure and traffic-aware travel to the bakery;
2. waiting until the bakery pickup window opens, if necessary;
3. five-minute pickup service;
4. traffic-aware travel from bakery to pantry;
5. waiting until the pantry receiving window opens, if necessary;
6. five-minute drop-off service;
7. optional travel from the pantry toward the driver's preferred destination.

A candidate is included in \(F\) only when:

- the bakery pickup is confirmed and unclaimed;
- the driver reaches the bakery by its pickup deadline;
- the pantry window is available;
- the driver reaches the pantry by its latest permitted arrival and before it closes;
- the completed drop-off fits inside the driver's requested time window.

No pantry is excluded because it has received recent deliveries. Delivery history affects priority only.

## Route quality

For feasible candidate \(r=(d,b,p)\):

\[
q_r =
w_p priority_p
- w_t driveMinutes_r
- w_w waitingMinutes_r
- w_e destinationMinutes_r
\]

The default weights are:

| Term | Weight |
| --- | ---: |
| Pantry-priority reward \(w_p\) | 45.00 |
| Driving-minute penalty \(w_t\) | 1.00 |
| Waiting-minute penalty \(w_w\) | 0.35 |
| Preferred-destination minute penalty \(w_e\) | 0.65 |

The current pantry priority is \(1/(1+deliveriesSevenDays)\). The production data model can later replace the seven-day count with deliveries per eligible receiving window without changing the assignment model.

## Binary variables

\[
x_r = x_{d,b,p} =
\begin{cases}
1 & \text{when driver } d \text{ is assigned pickup } b \text{ and pantry window } p\\
0 & \text{otherwise}
\end{cases}
\]

Variables exist only for routes in \(F\), so infeasible routes can never be selected.

## Constraints

Each driver receives at most one route in a batch:

\[
\sum_{r \in F: driver(r)=d} x_r \le 1 \qquad \forall d \in D
\]

Each bakery pickup is assigned at most once:

\[
\sum_{r \in F: pickup(r)=b} x_r \le 1 \qquad \forall b \in B
\]

There is intentionally no pantry-capacity constraint in version 1. A pantry can accept multiple deliveries while its window remains open.

## Lexicographic objective

BakedBoston's operating policy says a feasible delivery should not be abandoned merely because the closest pantry has already received food. The solver therefore uses two exact stages.

Stage 1 maximizes completed pickups:

\[
K^* = \max \sum_{r \in F} x_r
\]

Stage 2 fixes that maximum cardinality and maximizes route quality:

\[
\max \sum_{r \in F} q_r x_r
\]

subject to all assignment constraints and:

\[
\sum_{r \in F} x_r = K^*
\]

This is stronger than adding an arbitrary large assignment bonus: it mathematically guarantees the maximum feasible number of pickups before comparing distance, waiting, destination fit, and pantry priority.

## Solver and reproducibility

`bakedboston_optimizer.batch.optimize_batch` builds the feasible set and solves both stages with the OR-Tools CBC mixed-integer solver. `HaversineTravelTimeProvider` makes tests and demonstrations deterministic; `GoogleMapsProvider` supplies traffic-aware durations to the same model in production.

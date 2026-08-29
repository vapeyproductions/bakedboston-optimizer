# Academic comparison models

## Experimental control

The comparison engine samples one immutable seeded scenario before any model
runs. Every model therefore receives the same realized:

- bakery and pantry identities, coordinates, ZIP codes, and facility windows;
- bakery surplus occurrences and daily food/usability draws;
- pantry openings and fixed distribution fractions;
- bakery landfill, pig-farm, and compost allocations;
- driver arrivals, current locations, search horizons, and stated preferences;
  and
- route geometry, travel assumptions, and feasibility rules.

The route selector for each model reads only the variables present in that
model's declared formulation. Later endogenous pickup availability may diverge
because earlier selections differ; that divergence is model behavior, not a
change in scenario settings. All selected routes are then passed to one shared
food, waste, environmental, travel, fairness, and synthetic-acceptance
evaluator. This preserves identical experimental conditions without silently
adding BakedBoston-specific objectives to a comparison model.

## BakedBoston

BakedBoston uses binary route-column decisions \(y_r\). Its first objective
maximizes expected completed pickups:

\[
A^* = \max \sum_{r \in R} a_r y_r.
\]

Its second objective balances normalized food volume/evenness, pantry coverage
and priority, direct net environmental benefit, and driver fit while retaining
at least 99% of \(A^*\). The complete formulation is in
[model.md](model.md).

BakedBoston is treated as a route-choice policy in the public replay. After the
joint assignment resolves conflicts, each driver receives a conflict-free menu
and selects recommendation rank 1, the highest-scoring route.

## Nair et al. (2018) distance-first adaptation

### Source formulation

Nair, Rashidi, and Dixit formulate a Periodic Unpaired Pickup and Delivery
Vehicle Routing Problem for food rescue. Their weekly model selects service
schedules and vehicle arcs, requires scheduled pickup and delivery nodes to be
visited, enforces route-length/load/capacity/perishable-flow constraints, and
minimizes total transportation cost. Their solution method combines an initial
schedule MIP, partitioned cheapest insertion, and Tabu Search.

Reference: Nair, D. J., Rashidi, T. H., and Dixit, V. V. (2018), “Scheduling
and routing models for food rescue and delivery operations,”
*Socio-Economic Planning Sciences*, 63, 18–32.
[doi:10.1016/j.seps.2017.06.003](https://doi.org/10.1016/j.seps.2017.06.003).

### Necessary platform adaptation

BakedBoston currently has volunteer requests for one origin–bakery–pantry trip,
not dedicated depot-based multi-stop trucks. It also has no observed vehicle
capacity or pantry-demand quantity. Inventing those inputs would change the
simulation beyond the available evidence. The comparison therefore preserves
the paper's service-first, distance-minimizing logic within BakedBoston's actual
route contract.

For candidate route \(r\), let \(x_r\) be 1 when it is assigned. The adapted
model solves the following lexicographic objectives:

\[
\max \sum_{r \in R} x_r
\]

followed by

\[
\min \sum_{r \in R} m_r x_r,
\]

where \(m_r\) is route distance from the driver's current position to the
bakery and then the pantry. Maximizing assignment count is the minimum required
relaxation of the paper's mandatory-node service when the realized volunteer
fleet cannot serve every food-ready bakery.

The model enforces:

\[
\sum_{r \in R(q)} x_r \le 1 \quad \forall\text{ driver request }q,
\]

\[
\sum_{r \in R(d)} x_r \le 1 \quad \forall\text{ physical driver }d,
\]

and

\[
\sum_{r \in R(b)} x_r \le 1 \quad \forall\text{ food-ready bakery occurrence }b.
\]

The shared candidate generator enforces driver origin, hard search horizon,
bakery pickup readiness/deadline, pantry receiving window/latest arrival, and
route timing before this model sees a candidate. Open pantries may receive more
than one route because no evidence-based pantry demand or one-delivery capacity
currently exists.

The comparator does **not** use the following to select routes:

- soft requested-start or destination-ZIP fit;
- sigmoid acceptance probability;
- pantry distribution fraction, priority, coverage, or fairness history;
- bakery quantity/usability as an objective weight; or
- waste-pathway or transportation CO₂e.

This is explicitly a distance-first adaptation, not a reproduction of the
paper's depot-based, capacitated, multi-stop weekly Tabu Search implementation.
The assigned route is recorded as the driver's selection; the model does not
construct a driver-choice menu.

## Shared evaluation

For every completed route from bakery \(b\) to pantry \(p\) on day \(d\), food
recovered is

\[
H_{bpd}=Q_{bd}U_{bd}D_p.
\]

Food wasted is the sum of all uncollected bakery food and the collected route
remainder \(Q_{bd}-H_{bpd}\). Environmental reporting applies the same fixed
landfill/pig-farm/compost and tonne-kilometre coefficients to both models and
reports transportation CO₂e, net waste-pathway CO₂e, their total direct CO₂e,
and net environmental benefit.

The same transparent sigmoid evaluates every selected route after optimization.
The public deterministic replay reports mean expected acceptance and the shares
with probability at least/below 50%; it does not randomly remove deliveries.

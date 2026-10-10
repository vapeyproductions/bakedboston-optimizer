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

BakedBoston uses binary route-column decisions $x_r$ and a transparent modeled
acceptance probability $a_r$. It maximizes one normalized expected-impact
objective:

```math
Z^E=10C^E+10V_Q^E+10F_Q^E+10V_H^E+10F_H^E+10P^E+20E^E+20D^E.
```

The superscript $E$ indicates that route contributions and cumulative pantry
food totals are weighted by modeled completion probability. In the one-driver
case, this reduces to comparing $a_r I_r$, where $I_r$ is route $r$'s
normalized food, fairness, environmental, pantry, and driver-fit value if
completed. There is no 99%-of-best-acceptance filter. The complete formulation
is in [model.md](model.md).

BakedBoston is treated as a route-choice policy in the public replay. After the
joint assignment resolves conflicts, each driver receives a conflict-free menu
and selects recommendation rank 1, the highest-scoring route.

## Nair et al. (2018) coverage-first adaptation

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
the paper's service-first logic within BakedBoston's actual route contract,
but makes the first scarce-volunteer relaxation community-facing: serve as many
distinct pantries as possible before considering pickup count and distance.

For candidate route $r$, let $x_r$ be 1 when it is assigned, and let $y_p$ be
1 when pantry $p$ receives at least one selected route. The adapted model
solves these lexicographic objectives:

```math
\max \sum_{p\in P}y_p,
```

followed by

```math
\max \sum_{r\in R}x_r,
```

then

```math
\min \sum_{r\in R}m_rx_r.
```

Here $m_r$ is the distance from the driver's current position to the bakery
and then the pantry. The first objective protects distinct pantry reach; the
second is the smallest necessary relaxation of the source paper's mandatory
service when the realized volunteer fleet cannot serve every food-ready bakery.
The linking constraints ensure $y_p=1$ exactly when a route to $p$ is assigned.

The model enforces:

```math
\sum_{r \in R(q)} x_r \le 1 \quad \forall\text{ driver request }q,
```

```math
\sum_{r \in R(d)} x_r \le 1 \quad \forall\text{ physical driver }d,
```

and

```math
\sum_{r \in R(b)} x_r \le 1 \quad \forall\text{ food-ready bakery occurrence }b.
```

The shared candidate generator enforces driver origin, hard search horizon,
bakery pickup readiness/deadline, pantry receiving window/latest arrival, and
route timing before this model sees a candidate. Open pantries may receive more
than one route because no evidence-based pantry demand or one-delivery capacity
currently exists.

The comparator does **not** use the following to select routes:

- soft requested-start or destination-ZIP fit;
- sigmoid acceptance probability;
- pantry distribution fraction, priority, or historical fairness;
- bakery quantity/usability as an objective weight; or
- waste-pathway or transportation CO₂e.

This is explicitly a coverage-first adaptation, not a reproduction of the
paper's depot-based, capacitated, multi-stop weekly Tabu Search implementation.
The assigned route is recorded as the driver's selection; the model does not
construct a driver-choice menu.

## Xue and Zou (2025) Total-Curb adaptation

### Source formulation

Xue and Zou formulate a multiple-departure open pickup-and-delivery problem
with delivery deadlines for small-portion meal delivery. Their Total-Curb
mixed-integer model assigns every known restaurant-customer order and minimizes
the sum of order-related emissions and electric-motorcycle route emissions.
Their broader solution method uses trajectory-similarity clustering with
driver familiarity, deadline-ordered greedy insertion, and adaptive iterative
neighborhood search for multi-order routes.

Reference: Xue, G., and Zou, S. (2025), “Optimizing carbon reduction and
vehicle routing for small-portion meal delivery under dual carbon goals,”
*Cleaner Logistics and Supply Chain*, 16, 100253.
[doi:10.1016/j.clscn.2025.100253](https://doi.org/10.1016/j.clscn.2025.100253).

### Necessary platform adaptation

BakedBoston has one origin-bakery-pantry trip per volunteer request rather than
multi-order restaurant-customer routes. It has no observed arc-level driver
familiarity, meal-packaging quantities, or standard-versus-small-portion meal
classes. Inventing those inputs or adding multi-stop routes would change the
simulation rather than isolate the paper's primary Total-Curb routing
objective. The comparator therefore uses the paper's total-emissions strategy
with BakedBoston's existing direct environmental ledger and exact Gurobi solve;
it does not claim to reproduce the paper's AINS heuristic.

The source paper requires all known orders to be served. Under BakedBoston's
scarce, request-driven volunteer fleet, the adapted model first solves

```math
\max \sum_{r \in R} x_r.
```

For food-ready bakery pickup $b$, let $C_b^U$ be its fixed waste-pathway
CO₂e if uncollected. For candidate route $r$, let $C_r^W$ be residual-food
waste-pathway CO₂e after bakery usability and pantry distribution, and let
$C_r^T$ be transport CO₂e. The notation $b(r)$ identifies the bakery pickup
served by route $r$. Within the maximum service count, the second stage minimizes

```math
\min_x\left[\sum_{b\in B}C_b^U+\sum_{r\in R}\left(C_r^W+C_r^T-C_{b(r)}^U\right)x_r\right].
```

The all-uncollected term is constant inside a decision epoch, so the
implementation equivalently maximizes

```math
\max_x\sum_{r\in R}\left(C_{b(r)}^U-C_r^W-C_r^T\right)x_r.
```

The model uses the same request, physical-driver, and food-ready-pickup
exclusivity constraints as the Nair comparator. The shared candidate generator
continues to enforce current driver origin, hard search horizon, bakery pickup
readiness/deadline, pantry receiving window/latest arrival, and route timing.

The comparator uses daily bakery food/usability draws, fixed pantry
distribution fractions, bakery and pantry waste allocations, route distance,
and the existing fixed direct-emissions coefficients. It does **not** use:

- soft requested-start or destination-ZIP fit;
- sigmoid acceptance probability;
- pantry fairness, opportunity priority, or coverage;
- avoided-production credit;
- meal preparation or packaging emissions; or
- synthetic driver-familiarity values.

The assigned route is recorded as the driver's selection. The model does not
construct a driver-choice menu.

## Horner, Pazour, and Mitchell (2021) stochastic-menu adaptation

### Source formulation

Horner, Pazour, and Mitchell formulate a three-stage stochastic platform
problem. First the platform creates a short personalized menu for every driver.
Drivers then signal which menu requests they are willing to fulfill. Finally,
the platform makes a recourse assignment among willing driver-request pairs.
Their Single-Level Stochastic Formulation (SLSF) uses Sample Average
Approximation (SAA) to optimize expected platform utility under uncertain
driver willingness. Their SLSF-noZ variant removes penalties for drivers who
are willing to participate but receive no final assignment.

Reference: Horner, H., Pazour, J., and Mitchell, J. E. (2021), “Optimizing
driver menus under stochastic selection behavior for ridesharing and
crowdsourced delivery,” *Transportation Research Part E*, 153, 102419.
[doi:10.1016/j.tre.2021.102419](https://doi.org/10.1016/j.tre.2021.102419).

### Necessary platform adaptation

BakedBoston does not observe driver compensation, fares, or a history of
drivers accepting requests and receiving no assignment. Assigning a numerical
unhappy-driver penalty would therefore introduce unsupported data. The public
comparator uses the paper's SLSF-noZ variant, which preserves its central
stochastic-menu and recourse strategy without inventing that penalty.

Let $R$ be the shared set of feasible driver–bakery–pantry route candidates,
$S$ a set of 100 deterministically seeded SAA willingness scenarios, and
$p_r$ the existing sigmoid willingness estimate for candidate $r$. For
scenario $s$, $\hat y_{rs}$ is a seeded Bernoulli draw with parameter
$p_r$. The decision variables are:

- $x_r=1$ when route $r$ appears in its driver's menu; and
- $v_{rs}=1$ when route $r$ is assigned in scenario $s$.

The first objective maximizes expected completed pickups:

```math
\max \frac{1}{|S|}\sum_{s\in S}\sum_{r\in R}v_{rs}.
```

Expected route distance is minimized only as a lexicographic tie-break among
solutions with the same expected service:

```math
\min \frac{1}{|S|}\sum_{s\in S}\sum_{r\in R}m_rv_{rs}.
```

The adapted constraints include:

```math
\sum_{r\in R(d)}x_r\le 5 \quad \forall d,
```

```math
v_{rs}\le x_r,\qquad v_{rs}\le \hat y_{rs}
\quad \forall r,s,
```

```math
\sum_{r\in R(d)}v_{rs}\le1 \quad \forall d,s,
```

and

```math
\sum_{r\in R(b)}v_{rs}\le1 \quad \forall b,s.
```

A driver's menu cannot contain two pantry destinations for the same physical
bakery pickup. The same bakery may appear in different drivers' menus, as in
the source model, because the final recourse stage resolves overlap. Open
pantries remain nonexclusive.

At execution time the same seeded willingness outcome is available for every
candidate regardless of policy. The Horner comparator exposes its optimized
menu, records willingness for menu options, and makes a maximum-service,
minimum-distance final assignment among willing options. This is a direct
recourse assignment, not a rank-one driver choice. Public deterministic replay
disables Bernoulli removal for every model but retains $p_r$ in the Horner
training scenarios and reports expected/likely acceptance diagnostics.

The comparator uses the sigmoid's existing drive, requested-time, and spatial
fit inputs. It does **not** use food quantity, pantry distribution, fairness,
priority, coverage, or CO₂e to construct menus. Those outcomes are measured by
the shared evaluator after assignment. Production solves the formulation with
Gurobi. A clearly labeled development heuristic is used only when a local
size-limited Gurobi license cannot hold the 100-scenario model.

## Rey, Almi'ani, and Nair (2018) equitable-allocation adaptation

### Source formulation

Rey, Almi'ani, and Nair study food-rescue pickup and delivery allocation with
the explicit goal of envy-freeness among agencies. Their setting includes
agency requests, vehicle capacities, and multi-stop pickup-and-delivery routes;
they develop exact and heuristic approaches for their allocation problem.

Reference: Rey, D., Almi'ani, M., and Nair, D. J. (2018), “Exact and heuristic
algorithms for finding envy-free allocations in food rescue pickup and delivery
logistics,” *Transportation Research Part E*, 112, 19–46.
[doi:10.1016/j.tre.2018.02.001](https://doi.org/10.1016/j.tre.2018.02.001).

### Necessary platform adaptation

BakedBoston does not observe pantry request quantities, vehicle capacity, or
multi-stop route sequences. It therefore does **not** claim to reproduce the
paper's envy-free allocation formulation. The limited comparator retains the
most compatible idea: after preserving as much completed pickup service as
possible, avoid concentrating ultimately distributed food at one pantry.

Let $H_p^0$ be pantry $p$'s cumulative ultimately distributed food before the
decision epoch, and let $h_r=Q_rU_rD_{p(r)}$ be the ultimately distributed food
from candidate route $r$. The final total is

```math
H_p=H_p^0+\sum_{r\in R(p)}h_rx_r.
```

The adapted model first maximizes assigned food-ready pickups, then minimizes
the largest pairwise final-food gap, and finally minimizes route distance:

```math
\max \sum_{r\in R}x_r,
```

```math
\min g\quad\text{where}\quad g\ge H_p-H_{p'}\ \text{and}\ g\ge H_{p'}-H_p
\quad\forall p,p',
```

```math
\min \sum_{r\in R}m_rx_r.
```

It uses the same one-request, one-driver, and one-food-ready-bakery-occurrence
constraints as the other direct-assignment comparators. It uses daily bakery
food/usability, pantry distribution fractions, and prior delivered-food totals
only to calculate this allocation gap. It does **not** use soft preferences,
sigmoid acceptance, pantry priority, coverage, CO₂e, inferred pantry demand,
vehicle capacity, or multi-stop routing. The shared evaluator reports those
outcomes after selection.

## Shared evaluation

For every completed route from bakery $b$ to pantry $p$ on day $d$, food
recovered is

```math
H_{bpd}=Q_{bd}U_{bd}D_p.
```

Food wasted is the sum of all uncollected bakery food and the route remainder
$Q_{bd}-H_{bpd}$. Because a completed pickup transfers all food to its pantry,
that full remainder—including food found unusable—is valued with the destination
pantry's unique landfill/pig-farm mix. Only food never picked up uses the
bakery's landfill/pig-farm/compost mix. Environmental reporting applies the
same pathway and tonne-kilometre coefficients to all five policies and reports
transportation CO₂e, net waste-pathway CO₂e, total direct CO₂e, and net
environmental benefit.

The same transparent sigmoid evaluates every selected route after optimization.
The public deterministic replay reports mean expected acceptance and the shares
with probability at least/below 50%; it does not randomly remove deliveries.

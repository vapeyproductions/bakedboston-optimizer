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

```math
a=(d,b,p,t^{\mathrm{depart}},t^{\mathrm{pickup}},t^{\mathrm{arrival}},t^{\mathrm{finish}})
```

for each useful driver–bakery–pantry schedule. A column therefore contains the
complete plan: when the driver should leave, when pickup occurs, when pantry
arrival occurs, and when the trip finishes. For the current academic-sized
instances, the generator enumerates useful just-in-time departure breakpoints
and retains the best feasible timed plan for each driver–bakery–pantry
combination. Larger instances could generate these columns dynamically with
column generation.

- $D$: synthetic driver requests
- $B$: simulated bakery-surplus occurrences
- $P$: eligible pantry receiving-window occurrences
- $A$: feasible timed route columns
- $x_a \in \{0,1\}$: 1 when timed route column $a$ is selected

Each pantry window is represented as a time-specific occurrence. An unattended window is treated as open in the experiment; attendance at a staffed window is a seeded synthetic event.

## Timing and feasibility

For every potential $(d,b,p)$, the feasibility engine treats the driver's
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
assignment enters $A$ only if:

- the driver can reach the bakery by its pickup deadline;
- the pantry is open and the delivery arrives by its latest permitted arrival;
- the completed trip fits inside an outer search horizon;
- the simulated bakery-surplus event is available;
- the bakery and pantry schedule templates have validated coordinates;
- the pantry window is not paused or cancelled;
- the synthetic staffed-attendance event is available.

## Pantry priority

The fairness metric uses the last $N$ open receiving opportunities. Let $n_p$ be the number of recent opportunities and $served_p$ the number that received at least one simulated assignment:

```math
\mathrm{priority}_p=1-\frac{\mathrm{served}_p+1}{n_p+2}
```

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

## Food and environmental accounting

Each bakery $b$ has fixed triangular distributions for daily food amount
$Q_{bd}$ and usability $U_{bd}$. Their parameters do not change between
runs; the seed and occurrence ID determine the reproducible daily draws. Each
pantry $p$ has a fixed distribution fraction $D_p$. A selected route's
ultimately saved food is

```math
H_{bpd}=Q_{bd}U_{bd}D_p.
```

If a food-available bakery occurrence is not picked up, all $Q_{bd}$ is
recorded as uncollected bakery food. If it is picked up, the model records

```math
W_{bpd}=Q_{bd}-H_{bpd}
```

as collected food that is not ultimately distributed. Each bakery has fixed
landfill, pig-farm, and compost shares $(L_b,P_b,C_b)$, which sum to one. With
pathway coefficients $(e_L,e_P,e_C)=(0.36,-0.12,0.00581)$ kg CO₂e/kg waste,
define $e_b=L_be_L+P_be_P+C_be_C$. The no-pickup result is $Q_{bd}e_b$ and
the completed-route waste result is $W_{bpd}e_b$.

Transportation uses 0.41947 kg CO₂e/tonne-km:

```math
T_r=0.41947\left(\frac{Q_{bd}}{1000}\right)\left(1.60934\,\mathrm{miles}_r\right).
```

The environmental coefficient is $E_r=(Q_{bd}-W_{bpd})e_b-T_r$. The primary
score conservatively sets avoided-production substitution to zero; the declared
0.38 kg CO₂e/kg food coefficient remains available for sensitivity analysis.
These are paper-derived scenario values, not a measured BakedBoston carbon
inventory. Fixed inputs are listed in [institutions.md](institutions.md).

Driver fit is one minus the mean normalized drive-time, requested-window, and
spatial burdens, with each burden clipped to $[0,1]$.

More precisely:

```math
\mathrm{outsideMinutes}_r=\max(0,\mathrm{requestedStart}_r-\mathrm{departure}_r)
+\max(0,\mathrm{finish}_r-\mathrm{requestedFinish}_r)
```

```math
\mathrm{outsideWindowRatio}_r=\frac{\mathrm{outsideMinutes}_r}{\mathrm{requestedWindowMinutes}_r}
```

The request interval must be at least 30 minutes. Waiting between login and a
planned later departure is displayed to the driver but does not reduce route
quality. The generator schedules departure as late as feasibility permits, so
avoidable facility waiting is removed rather than priced into the objective.
For example, a 30-minute miss against a 30-minute request has ratio 1.0 and a
24-point penalty; a 10-minute miss against a four-hour request has ratio
10/240 and only a 1-point penalty.

For route $r=(d,b,p)$, define the raw spatial misses:

```math
\delta^{\mathrm{start}}_r=\max\!\left\{0,\mathrm{dist}(b,\mathrm{startZIP}_d)-\mathrm{radius}^{\mathrm{start}}_d\right\}
```

```math
\delta^{\mathrm{end}}_r=\max\!\left\{0,\mathrm{dist}(p,\mathrm{endZIP}_d)-\mathrm{radius}^{\mathrm{end}}_d\right\}
```

The distance is measured to the closest edge of the estimated ZIP circle, so a
facility inside the area has a zero-mile miss. For each driver request, each
applicable miss is divided by the largest miss of that type among the driver's
feasible routes. The final `spatialDeviationRatio` is the mean of the available
normalized components. If every alternative has zero deviation for one
component, that component contributes zero. This creates a relative penalty in
$[0,1]$, favors the closest alternatives, and never rewards an exact match.

Distance enters environmental accounting through tonne-kilometres and driver
fit through a normalized time burden. There is no separate generic mileage
penalty in the network objective.

## Participation estimate

Technical feasibility does not guarantee that a volunteer would accept a
route. Let $m_r$ be route drive minutes, $w_r$ the proportional miss of the
requested time window, and $s_r$ the normalized miss of the requested start
and destination areas. Each feasible route receives the following estimated
acceptance probability:

```math
a_r=\min\!\left\{0.98,\max\!\left\{0.02,\frac{1}{1+\exp\!\left[-\left(2.2-0.045m_r-1.8w_r-1.1s_r\right)\right]}\right\}\right\}.
```

The estimate falls when a route requires more driving or fits the volunteer's
requested time and geography less closely. The explicit min/max terms clip the
estimate to $[0.02,0.98]$ for stable scenario analysis. It is intentionally simple,
inspectable, and replaceable. These coefficients are synthetic assumptions,
not learned from volunteer behavior and not validated probabilities.

## Acceptance-adjusted expected-impact objective

The Gurobi model uses one normalized 100-point objective:

```math
Z^E=10C^E+10V_Q^E+10F_Q^E+10V_H^E+10F_H^E+10P^E+20E^E+20D^E.
```

The superscript $E$ denotes acceptance-adjusted expected outcome. For every
route-dependent contribution, the model uses $a_r x_r$ rather than $x_r$
alone. For example:

```math
V_Q^E=\frac{\sum_{r\in A}a_rQ_rx_r}{\sum_{b\in B}Q_b},
\qquad
V_H^E=\frac{\sum_{r\in A}a_rH_rx_r}{\sum_{b\in B}\max_{r:b(r)=b}H_r}.
```

Expected pantry food totals are

```math
\bar Q_p=Q_p^{\mathrm{history}}+\sum_{r:p(r)=p}a_rQ_rx_r,
\qquad
\bar H_p=H_p^{\mathrm{history}}+\sum_{r:p(r)=p}a_rH_rx_r.
```

The raw- and saved-food evenness terms $F_Q^E$ and $F_H^E$ apply the existing
normalized pairwise absolute-gap calculation to $\bar Q_p$ and $\bar H_p$.
Auxiliary continuous variables linearize those gaps. Expected pantry coverage
uses one continuous credit $c_p^E\in[0,1]$ per pantry with

```math
c_p^E\leq\sum_{r:p(r)=p}a_rx_r,
\qquad
C^E=\frac{1}{|P|}\sum_{p\in P}c_p^E.
```

The cap prevents multiple simultaneous assignments to one pantry from earning
unbounded coverage credit. It is exact for the one-driver case and is a linear
capped-expectation approximation when several independently accepted routes can
reach the same pantry in one epoch.

Opportunity priority, min–max-normalized direct environmental benefit, and
normalized driver fit are likewise multiplied by $a_r$ before being summed and
divided by the epoch's maximum feasible assignment count. Every normalized
component remains bounded by $[0,1]$. Cumulative pantry totals carry forward
across rolling epochs. This encourages balanced allocation but does not claim
the stronger envy-free property.

For the one-driver walkthrough, let

```math
I_r=10C_r+10V_{Q,r}+10F_{Q,r}+10V_{H,r}+10F_{H,r}+10P_r+20E_r+20D_r
```

be route $r$'s completed-impact score. The comparison exposed to the driver is
then

```math
Z_r^E=a_rI_r.
```

Thus, an 80% route does not automatically eliminate a 78% route. The latter can
win when its food recovery, pantry equity, environmental result, priority, or
driver fit makes its acceptance-adjusted expected impact larger. There is no
99%-of-best-acceptance constraint.

This is deliberately **not** an average-menu-score objective. Maximizing the
average of recommendation menus can improve the displayed mean while serving
fewer useful pickups. The single expected-impact objective instead makes the
participation tradeoff explicit inside the same score as the outcomes that
motivate the delivery.

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

The formulation is informed by food-rescue pickup-and-delivery, equitable
donated-food distribution, occasional-driver routing, and driver-choice
research. The precise relationship between the implementation and that
literature is documented in [research.md](research.md); references there are
context and design evidence, not a claim that BakedBoston reproduces each
paper's full algorithm.

## Constraints

Each driver request receives at most one assignment:

```math
\sum_{a\in A:\,\mathrm{driverRequest}(a)=d}x_a\leq1\qquad\forall d\in D
```

Each physical driver receives at most one simultaneous assignment, even if multiple active requests exist:

```math
\sum_{a\in A:\,\mathrm{driver}(a)=v}x_a\leq1\qquad\forall v
```

Each bakery pickup can be assigned only once:

```math
\sum_{a\in A:\,\mathrm{pickup}(a)=b}x_a\leq1\qquad\forall b\in B
```

There is intentionally no one-delivery capacity constraint on pantries. A pantry may receive multiple orders while its window is open.

## Solver result and diagnostics

The API returns the selected assignments plus:

- solver backend;
- optimization status;
- feasible candidate count;
- matched assignment count;
- expected completed deliveries;
- normalized acceptance-adjusted food/fairness/environment/driver-fit score;
- total route mileage;
- ultimately saved food;
- bakery food not picked up;
- collected food not ultimately distributed;
- net environmental benefit in kg CO₂e;
- runtime;
- MIP gap when Gurobi exposes it.

The academic Gurobi run fails closed: if Gurobi or its license is unavailable, the service returns an error rather than silently describing a heuristic as a Gurobi result. An explicitly named deterministic fallback exists only for development comparisons.

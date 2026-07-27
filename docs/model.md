# Version 0 mathematical model

## Sets

- \(B\): unclaimed bakery pickups
- \(P\): participating pantries
- \(R \subseteq B \times P\): feasible complete routes for one driver request

## Timing

For each pair \((b,p)\), the feasibility engine propagates:

1. driver departure;
2. travel to bakery;
3. waiting until food is ready;
4. 15-minute pickup service;
5. travel to pantry;
6. waiting until the pantry opens, if necessary;
7. 15-minute drop-off service.

The route is feasible only when the propagated times respect the bakery deadline, pantry latest permitted arrival, and driver latest finish.

## Version 0 ranking objective

For a feasible route \(r\):

\[
score_r =
w_p priority_r
- w_t driveMinutes_r
- w_w waitingMinutes_r
- w_d destinationMinutes_r
\]

The default weights are intentionally visible and configurable. Sensitivity analysis will be added before the hackathon submission.

## Why ranking comes before MILP

The first user interaction asks for recommendations for one driver. Enumerating feasible bakery–pantry pairs is transparent, testable, and sufficient for that request. The next model will introduce binary assignment variables for simultaneously allocating multiple drivers and bakery pickups:

\[
x_{d,b,p} = 1
\]

when driver \(d\) is assigned bakery pickup \(b\) and pantry \(p\).

The batch model will enforce:

- each bakery pickup is assigned at most once;
- each driver receives at most one conflicting route;
- only time-feasible assignments can be selected;
- every pantry remains eligible, with recent receipt history affecting priority rather than exclusion.

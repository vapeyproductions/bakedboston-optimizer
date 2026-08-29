# Research foundation and implementation map

BakedBoston is an original academic demonstration built from established
operations-research problem families. This page separates three claims:

- **Implemented:** behavior in the public code and exercised by tests.
- **Adapted:** a research-motivated design implemented in a smaller or
  different form here.
- **Future work:** a relevant method that is not part of the current solver.

The references provide context and design evidence. BakedBoston does not use
their datasets, reproduce all of their constraints, or claim their empirical
results.

## Research-to-implementation map

| Research stream | Contribution | BakedBoston implementation | Status |
| --- | --- | --- | --- |
| Food-rescue scheduling and routing | Joint scheduling, assignment, pickup-and-delivery routing, cost, and service levels | Timed driver–bakery–pantry route columns, hard pickup/receiving windows, one-use bakery occurrences | Adapted and implemented |
| Nair et al. (2018) comparison model | Mandatory food-rescue service followed by transportation-cost minimization | Public comparator maximizes assigned food-ready pickups, then minimizes miles using current volunteer origins and hard timing feasibility | Distance-first adaptation implemented; not a full PU-PDVRP/Tabu Search replication |
| Xue and Zou (2025) comparison model | Total-Curb open pickup-and-delivery routing minimizes order, waste, and vehicle emissions | Public comparator maximizes assigned food-ready pickups, then minimizes the existing direct system CO₂e ledger | Total-Curb objective adapted and implemented; not a multi-order AINS or driver-familiarity replication |
| Horner, Pazour, and Mitchell (2021) comparison model | Personalized driver menus optimized under stochastic willingness with final recourse assignment | Public comparator uses 100 seeded willingness scenarios, menus of at most five routes, and SLSF-noZ expected-service optimization | Stochastic-menu strategy adapted and implemented; no unsupported compensation or unhappy-driver penalty |
| Pickup-and-delivery routing | Exact network formulations for compatible pickup and delivery movements | Binary route-column variables with driver and bakery exclusivity | Adapted and implemented |
| Equitable charitable distribution | Equity and service continuity alongside travel efficiency | Opportunity-based pantry priority, coverage, never-served share, Gini coefficient, and service gap | Adapted and implemented |
| Online routing with occasional drivers | Rolling decisions instead of one static fleet plan | Seeded driver-arrival epochs and re-optimization over pickups still available | Adapted and implemented |
| Driver menu choice | A feasible offer may not be selected; menu composition affects participation | Transparent synthetic acceptance probability in BakedBoston plus SLSF-noZ stochastic menu optimization and recourse in the Horner comparator | Adapted and implemented with synthetic parameters |
| Driver satisfaction | Preference satisfaction trades off with traditional efficiency | Soft requested-time and ZIP-area deviation penalties | Adapted and implemented |
| Comparative food-redistribution LCA | Route distance is only one part of environmental performance; food quality, avoided production/disposal, sorting, and redistribution waste can dominate | Explicit route-level ledger for avoided food-system emissions, vehicle emissions, and residual redistribution waste | Adapted and implemented as declared scenario accounting |
| Robust/stochastic optimization | Uncertain supply, attendance, and behavior should be tested across scenarios | Reproducible seeded exogenous events plus 100-scenario SAA driver willingness in the Horner comparator | Stochastic driver-menu comparator implemented; no robust counterpart |
| Anticipatory dynamic routing | Future demand and volunteers can be anticipated | Current rolling horizon uses the observed synthetic state only | Future work; no Monte Carlo tree search |
| Large-scale route generation | Column generation can avoid full enumeration | Small academic instances enumerate useful timed columns directly | Future scaling path |
| Learned preferences | Historical choices can estimate heterogeneous driver preferences | Shared, transparent scenario coefficients | Future work requiring consented choice data |

## Primary references

### Food rescue and pickup-and-delivery structure

- Nair, Rashidi, and Dixit (2018), “Scheduling and routing models for food
  rescue and delivery operations,” *Socio-Economic Planning Sciences*, 63,
  18–32. [doi:10.1016/j.seps.2017.06.003](https://doi.org/10.1016/j.seps.2017.06.003).
  This is the closest direct precedent for combining food-rescue scheduling,
  assignment, routing, service levels, and cost. BakedBoston also implements a
  deliberately narrow comparison model derived from this paper: it maximizes
  feasible pickup assignments under volunteer scarcity and then minimizes
  distance. The comparator does not receive preference, sigmoid, fairness,
  food-distribution, or CO₂e inputs. See
  [comparison-models.md](comparison-models.md) for its equations and the exact
  boundary between the paper and the platform adaptation.
- Hernández-Pérez and Salazar-González (2007), “The one-commodity
  pickup-and-delivery traveling salesman problem,” *Networks*.
  [doi:10.1002/net.20209](https://doi.org/10.1002/net.20209). This is a
  structural ancestor for unpaired surplus pickup and delivery; BakedBoston
  uses route columns rather than reproducing its full arc formulation.

### Carbon-aware meal delivery routing

- Xue and Zou (2025), “Optimizing carbon reduction and vehicle routing for
  small-portion meal delivery under dual carbon goals,” *Cleaner Logistics and
  Supply Chain*, 16, 100253.
  [doi:10.1016/j.clscn.2025.100253](https://doi.org/10.1016/j.clscn.2025.100253).
  Their Total-Curb model minimizes meal, waste, and vehicle emissions for open
  multi-order pickup-and-delivery routes. BakedBoston's deliberately narrow
  comparator preserves that total-direct-emissions strategy within its current
  one-bakery/one-pantry volunteer route contract. It does not invent the
  paper's packaging, meal-class, arc-familiarity, or multi-stop inputs and does
  not claim to reproduce the AINS heuristic. See
  [comparison-models.md](comparison-models.md) for the adapted equations.

### Stochastic driver menus

- Horner, Pazour, and Mitchell (2021), “Optimizing driver menus under
  stochastic selection behavior for ridesharing and crowdsourced delivery,”
  *Transportation Research Part E*, 153, 102419.
  [doi:10.1016/j.tre.2021.102419](https://doi.org/10.1016/j.tre.2021.102419).
  Their model optimizes personalized request menus before uncertain driver
  willingness is known and then assigns requests in a recourse stage. The
  BakedBoston comparator preserves this three-stage strategy using the existing
  sigmoid as willingness probability and the paper's SLSF-noZ variant. It does
  not invent fare, wage, compensation, or dissatisfaction-history data. See
  [comparison-models.md](comparison-models.md) for the exact adaptation.

### Fairness and charitable distribution

- Rey, Almi’ani, and Nair (2018), “Exact and heuristic algorithms for finding
  envy-free allocations in food rescue pickup and delivery logistics,”
  *Transportation Research Part E*, 112, 19–46.
  [doi:10.1016/j.tre.2018.02.001](https://doi.org/10.1016/j.tre.2018.02.001).
  This motivates explicit fairness. BakedBoston does not claim envy-free
  allocation; it uses opportunity-based priority and distributional metrics.
- Orgut et al. (2018), “Robust optimization approaches for the equitable and
  effective distribution of donated food,” *European Journal of Operational
  Research*, 269(2), 516–531.
  [doi:10.1016/j.ejor.2018.02.017](https://doi.org/10.1016/j.ejor.2018.02.017).
  This supports evaluating equity and effectiveness together. The current
  seeded simulator is not a robust-optimization model.

### Occasional drivers, route menus, and preference fit

- Arslan et al. (2019), “Crowdsourced delivery—A dynamic pickup and delivery
  problem with ad hoc drivers,” *Transportation Science*, 53(1), 222–235.
  [doi:10.1287/trsc.2017.0803](https://doi.org/10.1287/trsc.2017.0803).
- Archetti, Savelsbergh, and Speranza (2021), “The online vehicle routing
  problem with occasional drivers,” *Computers & Operations Research*, 127,
  105144. [doi:10.1016/j.cor.2020.105144](https://doi.org/10.1016/j.cor.2020.105144).
  These motivate dynamic decisions when non-dedicated drivers appear over time.
- Horner, Pazour, and Mitchell (2021), “Optimizing driver menus under
  stochastic selection behavior for ridesharing and crowdsourced delivery,”
  *Transportation Research Part E*, 154, 102419.
  [doi:10.1016/j.tre.2021.102419](https://doi.org/10.1016/j.tre.2021.102419).
  This motivates modeling route choice rather than equating an offer with an
  accepted trip. BakedBoston does not reproduce its estimation or
  sample-average approximation.
- Šimunović and Vidović (2025), “Beyond efficiency: Exploring the cost effects
  of prioritizing driver satisfaction in vehicle routing,” *Operations
  Research Letters*, 63, 107344.
  [doi:10.1016/j.orl.2025.107344](https://doi.org/10.1016/j.orl.2025.107344).
  This supports reporting preference-fit and efficiency tradeoffs instead of
  claiming that one policy must win every metric.

### Timing, uncertainty, and future extensions

- “Anticipatory Monte Carlo tree search–based optimization for stochastic
  dynamic routing with time windows” (2026), *Computers & Chemical Engineering*.
  [doi:10.1016/j.cacaie.2026.100024](https://doi.org/10.1016/j.cacaie.2026.100024).
  This recent food-rescue work motivates anticipatory optimization.
  BakedBoston does not implement Monte Carlo tree search.

### Environmental life-cycle assessment of food redistribution

- Guo et al. (2026), “Comparing the Environmental Impacts of Representative
  Food Donation and Redistribution Strategies,” *Foods*, 15, 645.
  [doi:10.3390/foods15040645](https://doi.org/10.3390/foods15040645).
  This comparative life-cycle assessment evaluates eight produce-donation
  scenarios and reports net benefits across global warming, acidification,
  eutrophication, cumulative energy demand, and water use. For its functional
  unit of 391.8 kg redistributed over two weeks, the eight case-study scenarios
  produced estimated net climate savings of 132–233 kg CO2e, or 0.33–0.69 kg
  CO2e per kilogram redistributed. Those values describe that study's
  produce-focused system, not BakedBoston's bakery network. Its central lesson
  for BakedBoston is that sustainable logistics cannot be reduced to minimizing
  miles: food quality, sorting capacity, the amount of food discarded inside
  redistribution, the avoided-production assumption, and whether the donor's
  alternative was landfill or compost can materially change the result. The
  study found all eight scenarios environmentally beneficial in its context,
  while the strongest configurations combined higher-quality food, sorting,
  and shorter direct transport.

  BakedBoston adapts that structure using fixed bakery waste mixes, pantry
  distribution fractions, and \(H=Q\times U\times D\). The public simulator
  reports food saved, bakery food not picked up, collected food not ultimately
  distributed, and one net direct CO₂e result. These are declared scenario
  coefficients, not a verified carbon inventory.

## Evidence and validation boundary

The acceptance equation is a **behavioral scenario**, not an empirical model.
Its coefficients create transparent, testable tradeoffs among driving burden,
requested-time fit, and requested-area fit. They should be replaced only after
collecting consented volunteer-choice data.

A defensible empirical extension would pre-register observed route attributes,
collect accepted and declined menus with consent, estimate heterogeneous choice
parameters, test calibration and ranking out of sample, run weight and seed
sensitivity analyses, and report confidence intervals. Until then, the
simulator is a reproducible policy comparison under declared assumptions—not a
forecast of real volunteer behavior or food availability.

The environmental ledger has the same boundary. It is suitable for transparent
scenario comparison and sensitivity analysis, but it does not prove that any
specific bakery donation avoided a measured amount of CO₂e. A defensible field
inventory would require bakery-specific food mass and composition, usable-share
measurements, vehicle and traffic data, disposal counterfactuals, and evidence
that donated food displaced new food production.

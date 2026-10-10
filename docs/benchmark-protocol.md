# Repeated-simulation benchmark protocol

## Purpose

This protocol evaluates the five declared routing policies over repeated,
seeded synthetic five-day scenarios. It is the evidence-generating experiment
for the academic report; it is not the public website walkthrough. The
benchmark does **not** alter BakedBoston's MIP, candidate generator, food-flow
rules, environmental coefficients, or comparison policies. It repeatedly
executes those already declared methods.

## Policies and unit of analysis

Every replication includes BakedBoston's MIP and the four bounded research
adaptations: Nair–Rashidi–Dixit coverage-first, Xue–Zou Total-Curb,
Horner–Pazour–Mitchell SLSF-noZ, and Rey–Almi'ani–Nair equitable allocation.
The adaptation boundary and each model's intentionally excluded inputs are in
[comparison-models.md](comparison-models.md).

One **replication** is a five-day rolling-horizon simulation of one operating
condition. It contains one seeded sequence of bakery food events,
baker-specific daily raw-surplus and usability draws, staffed-pantry openings,
synthetic volunteer requests, and candidate-level acceptance uniforms.

## Primary experiment

The primary experiment runs 500 independent replications for each of three
conditions: 1,500 shared scenarios and 7,500 policy-level five-day reports.

```bash
python3 -m bakedboston_optimizer.benchmark \
  data/academic_comparison_snapshot.json \
  --start-date 2026-08-24 \
  --replications 500 \
  --master-seed 20261010 \
  --horizon-days 5 \
  --matching-interval-minutes 60 \
  --max-simultaneous-drivers 3 \
  --output-dir results/benchmark_2026_10_10 \
  --progress-every 10
```

| ID | Drivers/day | Bakery-food probability | Staffed-pantry open probability | Interpretation |
| --- | ---: | ---: | ---: | --- |
| `standard` | 12 | 0.88 | 0.90 | Baseline contention in the academic fixture. |
| `scarce_drivers` | 6 | 0.88 | 0.90 | Volunteer supply is constrained. |
| `high_surplus` | 12 | 1.00 | 0.90 | Every scheduled bakery occurrence has hypothetical surplus. |

Only exogenous scenario inputs vary by condition. No objective, constraint,
route generator, or emissions coefficient is retuned. The primary command
enables synthetic acceptance replay. Add `--disable-acceptance` only for a
separately reported deterministic routing-capacity sensitivity analysis.

## Validate a published or rerun package

After the benchmark completes, validate its files, policy row coverage, and
derived seed schedule without rerunning the optimizer:

```bash
python3 -m bakedboston_optimizer.verify_benchmark \
  results/benchmark_2026_10_10 \
  --repository-root .
```

For a fresh reproduction, point the command at the new output directory. A
source-hash warning means the current code differs from the source recorded in
that result package; it is expected after a code change and should be reported
alongside any comparison of outputs.

## Reproducible seeds and common random numbers

The master seed produces one seed per condition and one-based replication:

```text
material = "bakedboston-benchmark-seed-v1|masterSeed|conditionId|replication"
seed = BLAKE2b-64(material) mod 2,147,483,646 + 1
```

The seed rule is stable across Python versions and ensures that adding a future
condition cannot change an existing condition's seed list. The generated
manifest stores all derived seeds.

Within a condition and replication, `compare_policies` creates one immutable
scenario and gives it to every policy. The five policies therefore receive the
same food, usability, pantry-opening, driver, feasible-route, and
candidate-level acceptance events. Later states may diverge only because
policies make different assignments; that is the intended treatment effect.
This use of **common random numbers** reduces noise in paired differences.

If an identical candidate route exists for two policies, its acceptance uniform
is shared. If only one policy selects that route, only that policy can observe
the completed or rejected event. An unoffered route cannot produce a realized
outcome.

## Food and environmental accounting

The benchmark uses the project's current accounting rules without amendment:

- An uncollected bakery occurrence sends its full raw donation $Q$ through the
  bakery's landfill/pig-farm/compost allocation.
- A collected donation transfers the full $Q$ to the destination pantry.
- Ultimately distributed food is $H=QUD$, where $U$ is sampled bakery usability
  and $D$ is the pantry's fixed distribution fraction.
- All completed-route residual $Q-QUD$, including unusable food, is handled by
  the destination pantry's landfill/pig-farm allocation.
- Transport uses full raw collected mass and the fixed tonne-kilometre factor.

Thus `foodSavedKg` is ultimately distributed food; `foodWastedKg` is
uncollected raw bakery food plus completed-route pantry residual; and
`netEnvironmentalBenefitKgCO2e` is relative to the uncollected bakery-disposal
baseline. Equations are in [model.md](model.md); fixed inputs are in
[institutions.md](institutions.md).

## Outcomes and statistical summaries

The raw data retains every finite `PolicyReport.metrics()` output. The report's
primary outcomes are:

| Outcome | Better direction | Meaning |
| --- | --- | --- |
| `completedDeliveries` | Higher | Accepted bakery-to-pantry routes. |
| `foodSavedKg` | Higher | Ultimately distributed food, $QUD$. |
| `foodWastedKg` | Lower | Uncollected bakery food plus pantry residual. |
| `netEnvironmentalBenefitKgCO2e` | Higher | Direct waste-pathway baseline benefit after residual and transport. |
| `totalDirectKgCO2e` | Lower | Waste-pathway plus transport direct emissions. |
| `averageDriveMinutes`, `averageDistanceMiles` | Lower | Completed-route burden. |
| `expectedDriverAcceptanceRate` | Higher | Mean synthetic sigmoid probability for offered routes. |
| `driverAcceptanceRate` | Higher | Realized accepted divided by offered routes. |
| `likelyRejectionRate` | Lower | Offers with sigmoid probability below 0.50. |
| `pantryCoveragePercentage` | Higher | Unique pantries served divided by available pantries. |
| `foodSavedDistributionGini` | Lower | Inequality in ultimately distributed food. |

For every condition × policy × metric, the output reports $n$, mean, sample
standard deviation, median, minimum, maximum, and a two-sided 95% confidence
interval: $\bar{x}\pm1.96s/\sqrt{n}$.

The paired-difference file reports
$\Delta_i=x_{i,\mathrm{BakedBoston}}-x_{i,\mathrm{comparator}}$. A positive
difference favors BakedBoston for higher-is-better measures; a negative
difference favors it for lower-is-better measures. The CSV records that
direction explicitly. An interval crossing zero is inconclusive under this
fixture and configuration, not proof of equivalence.

The website's Balanced Total Impact score is not a primary inferential result.
It is a within-scenario communication index whose scale depends on the selected
comparison set. The underlying outcomes above remain the evidence.

## Output and audit trail

| File | Role |
| --- | --- |
| `reproduction_manifest.json` | Configuration, seed list, fixture/source hashes, git state, solver/runtime details, and CI method. |
| `replication_metrics.csv` | One raw row per condition × replication × policy. |
| `summary_statistics.csv` | Aggregate statistics for every finite metric. |
| `paired_bakedboston_deltas.csv` | Matched BakedBoston–comparator primary-outcome differences. |
| `results_section.md` | Report-ready language and tables generated directly from raw results. |
| `README.md` | Exact replay command and data dictionary. |

The manifest hashes every executable Python module, package configuration,
requirements, and fixture. It identifies uncommitted source changes as well as
a git commit.

## Interpretation boundary

All institutions, food events, pantry staffing, volunteer behavior, and
deliveries are synthetic. The willingness sigmoid is a transparent simulation
assumption, not a trained or validated behavioral model. Direct emissions
coefficients support consistent policy comparison but are not a complete
food-category-specific life-cycle assessment. The fixture supports
one-bakery/one-pantry assignments, not validated multi-stop routing, observed
pantry demand, or live operating outcomes.

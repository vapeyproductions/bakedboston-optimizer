# BakedBoston repeated-simulation benchmark output

This directory is the generated evidence package for the academic benchmark.

## Exact replay command

Run from the optimizer repository root:

```bash
python3 -m bakedboston_optimizer.benchmark \
  data/academic_comparison_snapshot.json \
  --start-date 2026-08-24 \
  --replications 500 \
  --master-seed 20261010 \
  --horizon-days 5 \
  --matching-interval-minutes 60 \
  --max-simultaneous-drivers 3 \
  --output-dir results/benchmark-replay \
  --progress-every 10
```

The command enables the existing synthetic acceptance replay. Add
`--disable-acceptance` only for the separate deterministic routing-capacity
sensitivity analysis; it is not the primary result package.

## Files

- `reproduction_manifest.json`: complete configuration, all derived seeds,
  input/source hashes, runtime information, and confidence-interval method.
- `replication_metrics.csv`: one row per condition × replication × policy.
  This is the source data for every aggregate result.
- `summary_statistics.csv`: one row per condition × policy × metric with mean,
  sample standard deviation, median, range, and a 95% confidence interval.
- `paired_bakedboston_deltas.csv`: matched-replication BakedBoston minus
  comparator differences for primary outcomes.
- `results_section.md`: report-ready results language and tables generated from
  the raw rows. It contains no manually typed performance claims.

## Reproducibility rules

For each condition and one-based replication number, the seed is calculated as
`BLAKE2b-64("bakedboston-benchmark-seed-v1|masterSeed|conditionId|replication")`,
reduced to the integer range 1 through 2,147,483,646. All five policies share
the resulting immutable scenario and candidate-level acceptance uniforms.

Changing the code, fixture, condition definitions, horizon, date, seed,
acceptance setting, or solver can change results. The manifest records hashes
and environment metadata so a reviewer can identify such a change.

## Conditions

| Condition | Drivers/day | Bakery food probability | Staffed-pantry open probability | Purpose |
| --- | ---: | ---: | ---: | --- |
| standard | 12 | 0.88 | 0.90 | Baseline contention with the fixture's standard daily surplus and driver supply. |
| scarce_drivers | 6 | 0.88 | 0.90 | Tests allocation quality when volunteer supply is constrained relative to food-ready pickups. |
| high_surplus | 12 | 1.00 | 0.90 | Tests routing when every scheduled bakery occurrence has hypothetical surplus available. |

## Metric columns

acceptanceRate, availablePantries, averageDestinationDeviationMiles, averageDestinationMinutes, averageDistanceMiles, averageDriveMinutes, averageElapsedFromLoginMinutes, averageMenuSize, averageNetEnvironmentalBenefitKgCO2ePerDelivery, averageNormalizedSpatialDeviation, averageOptimalityGap, averageOriginDeviationMiles, averageOutsideRequestedWindowPercent, averageOutsideRequestedWindowRatio, averagePantryPriorityServed, averagePredepartureWaitMinutes, averageRequestedTimeDeviationMinutes, averageRouteBurdenMinutes, averageRouteQuality, averageSolverRuntimeSeconds, averageTotalTripDurationMinutes, bakeryPickupCoverage, collectedNotDistributedKg, completedDeliveries, driverAcceptanceRate, eligibleBakeryPickupOccurrences, expectedDriverAcceptanceRate, expectedRejectedOffers, feasibleCandidatesEvaluated, foodAvailablePickups, foodSavedDistributionGini, foodSavedKg, foodWastedKg, fractionOpenPantriesNeverServed, likelyAcceptanceRate, likelyRejectionRate, maximumMipGap, menuOptionsOffered, netEnvironmentalBenefitKgCO2e, offersLikelyAccepted, offersLikelyRejected, openPantryWindows, pantriesNeverServedCount, pantriesNeverServedPercentage, pantryCoverageCount, pantryCoveragePercentage, pantryServiceGap, pantryServiceGini, pickupCoverage, preferredWindowFitRate, rawDonationDistributionGini, residualWasteKgCO2e, routesOffered, routesRejected, scenarioFoodAvailablePickups, scenarioOpenPantryWindows, scenarioSyntheticDriverRequests, scheduledPickupWindows, systemObjectiveValue, totalDirectKgCO2e, totalRouteQuality, totalSolverRuntimeSeconds, transportKgCO2e, uncollectedBakeryFoodKg, uncollectedWasteKgCO2e, unhappyDrivers, uniquePantriesServed, unservedPickups, wastePathwayKgCO2e, willingMenuOptionRate, willingMenuOptions

All institutions, food events, drivers, acceptance decisions, and delivery
outcomes are synthetic academic simulation data. These outputs must not be
described as live operating performance.

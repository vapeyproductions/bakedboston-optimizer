from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .environment import DEFAULT_ENVIRONMENTAL_ASSUMPTIONS
from .experiment import (
    ExperimentConfig,
    RoutingPolicy,
    compare_policies,
    estimated_postal_code,
)
from .network import NetworkSnapshot, parse_snapshot
from .optimizer import OptimizationWeights
from .simulation import SimulationConfig
from .travel import HaversineTravelTimeProvider


POLICY_METADATA = {
    "bakedboston_mip": {
        "label": "BakedBoston Gurobi MIP",
        "description": (
            "Maximizes one acceptance-adjusted expected-impact objective across simultaneous "
            "drivers, balancing food, fairness, environment, pantry priority, and driver fit."
        ),
        "selectionMode": "rank_one_choice",
        "selectionDescription": (
            "The driver chooses recommendation rank 1, the highest-scoring route "
            "in the conflict-free menu produced after the joint assignment."
        ),
        "objective": (
            "Maximize the normalized food, fairness, environmental, pantry-priority, and "
            "driver-fit score after weighting route contributions by modeled acceptance."
        ),
        "inputsUsed": [
            "current driver location",
            "hard search horizon and facility windows",
            "requested time and ZIP-area preferences",
            "acceptance estimate",
            "food and pantry distribution",
            "pantry fairness and priority",
            "direct environmental balance",
        ],
        "inputsExcluded": [],
    },
    "nair_2018_distance_first": {
        "label": "Nair et al. distance-first adaptation",
        "description": (
            "A minimal volunteer-route adaptation of the 2018 periodic unpaired "
            "pickup-and-delivery model: protect service first, then minimize miles."
        ),
        "selectionMode": "direct_assignment",
        "selectionDescription": (
            "The model assigns one route to a driver; that assigned route is recorded "
            "as the driver's selection."
        ),
        "objective": (
            "Maximize assigned food-ready pickups, then minimize total route distance."
        ),
        "inputsUsed": [
            "current driver location as route origin",
            "hard search horizon",
            "bakery and pantry availability windows",
            "route distance",
        ],
        "inputsExcluded": [
            "soft requested-time and ZIP preferences",
            "acceptance probability",
            "pantry fairness and priority",
            "food-distribution fraction",
            "CO2e",
        ],
    },
    "xue_zou_2025_total_curb": {
        "label": "Xue–Zou Total-Curb adaptation",
        "description": (
            "A minimal volunteer-route adaptation of the 2025 Total-Curb model: "
            "protect service first, then minimize total direct system CO2e."
        ),
        "selectionMode": "direct_assignment",
        "selectionDescription": (
            "The model assigns one route to a driver; that assigned route is recorded "
            "as the driver's selection."
        ),
        "objective": (
            "Maximize assigned food-ready pickups, then minimize uncollected waste, "
            "selected-route residual waste, and transportation CO2e."
        ),
        "inputsUsed": [
            "current driver location as route origin",
            "hard search horizon",
            "bakery and pantry availability windows",
            "daily bakery food and usability",
            "pantry distribution fraction",
            "bakery and pantry waste allocations",
            "route distance and fixed direct-emissions coefficients",
        ],
        "inputsExcluded": [
            "soft requested-time and ZIP preferences",
            "acceptance probability",
            "pantry fairness, priority, and coverage",
            "avoided production",
            "meal preparation and packaging emissions",
            "unobserved driver familiarity",
        ],
    },
    "horner_2021_slsf_noz": {
        "label": "Horner et al. stochastic-menu adaptation",
        "description": (
            "A minimal adaptation of the 2021 SLSF-noZ model: optimize short "
            "personalized menus over seeded stochastic willingness scenarios, "
            "then assign among routes drivers are willing to fulfill."
        ),
        "selectionMode": "direct_assignment",
        "selectionDescription": (
            "The driver signals willingness for any acceptable menu options; "
            "the platform's recourse solve assigns one willing route, which is "
            "recorded as the driver's selection."
        ),
        "objective": (
            "Maximize expected completed food-ready pickups over 100 seeded "
            "willingness scenarios, then minimize expected route distance as a tie-break."
        ),
        "inputsUsed": [
            "current driver location as route origin",
            "hard search horizon",
            "bakery and pantry availability windows",
            "existing sigmoid acceptance probability",
            "100 seeded SAA willingness scenarios",
            "route distance as an exact-service tie-break",
        ],
        "inputsExcluded": [
            "food quantity and pantry distribution as objectives",
            "pantry fairness, priority, and coverage",
            "CO2e as an objective",
            "fare, compensation, and wage assumptions",
            "unobserved unhappy-driver penalty history",
        ],
    },
    "random_feasible": {
        "label": "Random feasible",
        "description": "Chooses randomly from routes that satisfy the same timing constraints.",
    },
    "shortest_route": {
        "label": "Shortest route",
        "description": "Greedily chooses the smallest driving-time burden.",
    },
    "earliest_deadline": {
        "label": "Earliest deadline",
        "description": "Greedily serves bakery pickup deadlines that occur soonest.",
    },
    "highest_priority": {
        "label": "Highest pantry priority",
        "description": "Greedily favors pantries with the greatest current priority.",
    },
    "driver_fit": {
        "label": "Driver destination fit",
        "description": "Greedily minimizes deviation from each driver's preferred destination.",
    },
}


PUBLIC_COMPARISON_POLICIES: tuple[RoutingPolicy, ...] = (
    RoutingPolicy.BAKEDBOSTON_MIP,
    RoutingPolicy.NAIR_2018_DISTANCE_FIRST,
    RoutingPolicy.XUE_ZOU_2025_TOTAL_CURB,
    RoutingPolicy.HORNER_2021_SLSF_NOZ,
)


TOTAL_IMPACT_PILLARS = {
    "service": (
        "Completed service",
        "Completed bakery-to-pantry deliveries in the realized routing-capacity replay.",
    ),
    "food": (
        "Food recovery",
        "Ultimately distributed food using Q × bakery usability × pantry distribution.",
    ),
    "environment": (
        "Environmental benefit",
        "Net direct waste-pathway benefit after subtracting transportation CO2e.",
    ),
    "equity": (
        "Distribution equity",
        "Equal average of pantry coverage, raw-donation equality, and saved-food equality.",
    ),
    "volunteer": (
        "Volunteer fit",
        "Mean modeled route-acceptance probability, which combines driving and requested time/location fit.",
    ),
    "efficiency": (
        "Route efficiency",
        "Average route-distance efficiency relative to the shortest-distance policy in the same scenario.",
    ),
}


def _finite_metric(result: dict[str, Any], key: str) -> float:
    value = result.get("metrics", {}).get(key)
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return 0.0
    return float(value)


def _higher_is_better_ratio(value: float, best: float) -> float:
    """Return a bounded fraction of the best observed value using a natural zero."""

    if best > 0:
        return max(0.0, min(1.0, value / best))
    if best == 0:
        return 1.0 if value == 0 else 0.0
    if value < 0:
        return max(0.0, min(1.0, best / value))
    return 1.0


def _lower_is_better_ratio(value: float, best: float) -> float:
    """Return a bounded efficiency fraction for nonnegative burden measures."""

    if value <= 0:
        return 1.0 if best <= 0 else 0.0
    return max(0.0, min(1.0, best / value))


def _add_total_impact_scores(payload: dict[str, Any]) -> None:
    """Attach a transparent post-hoc six-pillar comparison score.

    This score is deliberately separate from every policy's optimization
    objective. It summarizes non-duplicated outcome families relative to the
    best policy in this exact scenario and must not be interpreted as an
    externally calibrated social-impact measure.
    """

    results = payload.get("results", [])
    if not results:
        return

    rows: list[dict[str, float]] = []
    for result in results:
        completed = _finite_metric(result, "completedDeliveries")
        acceptance = _finite_metric(result, "expectedDriverAcceptanceRate")
        rows.append({
            "service": completed,
            "food": _finite_metric(result, "foodSavedKg"),
            "environment": _finite_metric(result, "netEnvironmentalBenefitKgCO2e"),
            "coverage": _finite_metric(result, "pantryCoveragePercentage"),
            "rawEquality": max(0.0, 1.0 - _finite_metric(result, "rawDonationDistributionGini")),
            "savedEquality": max(0.0, 1.0 - _finite_metric(result, "foodSavedDistributionGini")),
            "volunteer": acceptance,
            "distance": _finite_metric(result, "averageDistanceMiles"),
        })

    positive_distances = [row["distance"] for row in rows if row["distance"] > 0]
    best = {
        "service": max(row["service"] for row in rows),
        "food": max(row["food"] for row in rows),
        "environment": max(row["environment"] for row in rows),
        "coverage": max(row["coverage"] for row in rows),
        "rawEquality": max(row["rawEquality"] for row in rows),
        "savedEquality": max(row["savedEquality"] for row in rows),
        "volunteer": max(row["volunteer"] for row in rows),
        "distance": min(positive_distances) if positive_distances else 0.0,
    }

    for result, row in zip(results, rows, strict=True):
        equity = sum((
            _higher_is_better_ratio(row["coverage"], best["coverage"]),
            _higher_is_better_ratio(row["rawEquality"], best["rawEquality"]),
            _higher_is_better_ratio(row["savedEquality"], best["savedEquality"]),
        )) / 3.0
        pillars = {
            "service": _higher_is_better_ratio(row["service"], best["service"]),
            "food": _higher_is_better_ratio(row["food"], best["food"]),
            "environment": _higher_is_better_ratio(row["environment"], best["environment"]),
            "equity": equity,
            "volunteer": _higher_is_better_ratio(row["volunteer"], best["volunteer"]),
            "efficiency": _lower_is_better_ratio(row["distance"], best["distance"]),
        }
        metrics = result["metrics"]
        for key, value in pillars.items():
            metrics[f"impact{key.title()}Score"] = round(100.0 * value, 1)
        metrics["totalImpactScore"] = round(
            100.0 * sum(pillars.values()) / len(pillars),
            1,
        )


def _network_payload(snapshot: NetworkSnapshot) -> dict[str, Any]:
    def organization(item: Any, kind: str) -> dict[str, Any]:
        location = item.location()
        payload = {
            "id": f"{kind}-{item.id}",
            "name": item.name,
            "kind": kind,
            "latitude": item.latitude,
            "longitude": item.longitude,
            "postalCode": estimated_postal_code(location),
            "formattedAddress": location.formatted_address,
            "schedule": item.schedule,
        }
        if kind == "bakery":
            payload.update({
                "foodAmountDistributionKg": asdict(item.food_amount_distribution) if item.food_amount_distribution is not None else None,
                "usableFractionDistribution": asdict(item.usable_fraction_distribution) if item.usable_fraction_distribution is not None else None,
                "wasteAllocation": asdict(item.waste_allocation) if item.waste_allocation is not None else None,
            })
        else:
            payload.update({
                "distributionFraction": item.pantry_distribution_fraction,
                "wasteAllocation": asdict(item.waste_allocation) if item.waste_allocation is not None else None,
            })
        return payload

    return {
        "bakeries": [organization(item, "bakery") for item in snapshot.eligible_bakeries],
        "pantries": [organization(item, "pantry") for item in snapshot.eligible_pantries],
    }


def _compact_day(day: dict[str, Any], *, include_trace: bool) -> dict[str, Any]:
    """Keep the browser replay auditable without shipping every raw candidate twice."""

    compact = {
        key: day[key]
        for key in (
            "serviceDate",
            "scheduledPickupWindows",
            "foodAvailablePickups",
            "openPantryWindows",
            "driverRequests",
            "feasibleCandidatesEvaluated",
            "routesOffered",
            "routesAccepted",
            "foodSavedKg",
            "collectedNotDistributedKg",
            "uncollectedBakeryFoodKg",
            "pickupWindows",
            "pantryWindows",
        )
    }
    compact["selections"] = day["offers"]
    if include_trace:
        compact["decisionEpochs"] = [
            {
                "decisionEpoch": epoch["decisionEpoch"],
                "requests": epoch["requests"],
                "queueSize": epoch["queueSize"],
                "candidateCount": epoch["candidateCount"],
                "selectedCount": epoch["selectedCount"],
                "driverRecommendations": epoch["driverRecommendations"],
                "solver": epoch["solver"],
            }
            for epoch in day["decisionEpochs"]
        ]
    return compact


def _compact_comparison(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic fields needed by the public simulator."""

    compact_results = []
    for result in payload["results"]:
        include_trace = result["policy"] in {
            "bakedboston_mip",
            "horner_2021_slsf_noz",
        }
        compact_results.append({
            "policy": result["policy"],
            "metrics": result["metrics"],
            "pantryOpportunities": result["pantryOpportunities"],
            "days": [
                _compact_day(day, include_trace=include_trace)
                for day in result["days"]
            ],
        })
    return {
        "mode": payload["mode"],
        "runtime": payload["runtime"],
        "disclaimer": payload["disclaimer"],
        "scenario": payload["scenario"],
        "results": compact_results,
    }


def build_web_payload(
    snapshot: NetworkSnapshot,
    *,
    start_date: date,
    days: int = 5,
    seed: int = 2033,
    drivers_per_day: int = 12,
    matching_interval_minutes: int = 60,
    max_simultaneous_drivers: int = 3,
    weights: OptimizationWeights = OptimizationWeights(),
) -> dict[str, Any]:
    """Create the deterministic Gurobi-backed payload replayed by the website."""

    comparison = compare_policies(
        snapshot,
        ExperimentConfig(
            SimulationConfig(
                start_date=start_date,
                days=days,
                random_seed=seed,
                drivers_per_day=drivers_per_day,
                bakery_food_probability=0.88,
                staffed_pantry_open_probability=0.90,
            ),
            matching_interval_minutes=matching_interval_minutes,
            max_simultaneous_drivers=max_simultaneous_drivers,
            acceptance_enabled=False,
        ),
        policies=PUBLIC_COMPARISON_POLICIES,
        travel=HaversineTravelTimeProvider(),
        weights=weights,
    )
    payload = _compact_comparison(comparison.as_dict())
    payload["scenario"].update({
        "bakeryCount": len(snapshot.eligible_bakeries),
        "pantryCount": len(snapshot.eligible_pantries),
    })
    _add_total_impact_scores(payload)
    payload.update({
        "schemaVersion": 4,
        "displayMode": "interactive_replay_of_precomputed_gurobi_experiment",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceSnapshotGeneratedAt": snapshot.generated_at.isoformat(),
        "policyMetadata": POLICY_METADATA,
        "objectiveWeights": {
            key: value
            for key, value in asdict(weights).items()
            if key in {
                "pantry_coverage_reward", "raw_food_volume_reward",
                "raw_food_evenness_reward", "saved_food_volume_reward",
                "saved_food_evenness_reward", "pantry_priority_reward",
                "environmental_benefit_reward", "driver_fit_reward",
            }
        },
        "routeTimingWeights": {
            "driveMinutePenalty": weights.drive_minute_penalty,
            "requestedTimeDeviationRatioPenalty": weights.requested_time_deviation_ratio_penalty,
            "spatialDeviationRatioPenalty": weights.spatial_deviation_ratio_penalty,
        },
        "environmentalAssumptions": {
            "landfillKgCo2ePerKgWaste": DEFAULT_ENVIRONMENTAL_ASSUMPTIONS.landfill_kg_co2e_per_kg_waste,
            "pigFarmKgCo2ePerKgWaste": DEFAULT_ENVIRONMENTAL_ASSUMPTIONS.pig_farm_kg_co2e_per_kg_waste,
            "compostKgCo2ePerKgWaste": DEFAULT_ENVIRONMENTAL_ASSUMPTIONS.compost_kg_co2e_per_kg_waste,
            "transportKgCo2ePerTonneKm": DEFAULT_ENVIRONMENTAL_ASSUMPTIONS.transport_kg_co2e_per_tonne_km,
            "avoidedProductionKgCo2ePerKgFood": DEFAULT_ENVIRONMENTAL_ASSUMPTIONS.avoided_production_kg_co2e_per_kg_food,
            "productionSubstitutionFraction": DEFAULT_ENVIRONMENTAL_ASSUMPTIONS.production_substitution_fraction,
            "interpretation": (
                "Declared academic scenario coefficients. The primary objective credits the "
                "difference between the bakery's no-pickup waste outcome and two route-waste "
                "streams: bakery-unusable food under the bakery mix and pantry-undistributed "
                "food under that pantry's landfill/pig-farm mix. It then subtracts usable-cargo "
                "tonne-kilometre transport emissions. Avoided production is held at zero in the "
                "primary score."
            ),
        },
        "network": _network_payload(snapshot),
        "selectionRule": (
            "The joint Gurobi solve maximizes one normalized acceptance-adjusted expected-impact "
            "score: expected pantry coverage (10), raw donation volume (10), raw donation "
            "evenness (10), ultimately saved food volume (10), saved-food evenness (10), "
            "historical pantry opportunity priority (10), net direct CO2 benefit (20), and "
            "driver fit (20). Route contributions and pantry food totals are weighted by the "
            "transparent modeled acceptance probability; there is no 99%-of-best filter."
        ),
        "recommendationAllocation": {
            "bakeryPickupExclusiveAcrossMenus": True,
            "pantryDestinationsMayRepeat": True,
            "menuConstruction": "cardinality_first_fairness_layers",
            "zeroRecommendationRule": (
                "A driver receives no recommendation only when there are fewer distinct feasible "
                "bakery pickups than simultaneous drivers or that driver has no time-feasible route."
            ),
        },
        "metricMethodology": {
            "actualSelection": (
                "For BakedBoston's route-choice menu, the simulated driver selects rank 1, "
                "the highest-scoring route. The Horner adaptation instead follows its source "
                "formulation: drivers signal willingness for menu options and the platform "
                "makes the final recourse assignment. For other direct-assignment models, "
                "the assigned route is recorded as the driver's selection."
            ),
            "acceptanceModel": (
                "Expected acceptance and likely-rejection measures are prediction-based diagnostics; "
                "they do not override the deterministic recorded selection used in this demonstration."
            ),
            "comparison": (
                "All models share the exact same seeded surplus, daily food and usability draws, "
                "pantry openings and distribution fractions, waste allocations, driver events, "
                "facility windows, and feasible route geometry. Each selector reads only the "
                "inputs represented in its formulation; every result is then evaluated through "
                "the same food, environmental, travel, and acceptance ledger."
            ),
        },
        "totalImpactMethodology": {
            "label": "Balanced Total Impact",
            "description": (
                "A post-hoc communication index that gives equal weight to six non-duplicated "
                "outcome pillars. Each pillar is scored from 0 to 100 relative to the best "
                "policy in this exact scenario."
            ),
            "weightPerPillar": round(100 / len(TOTAL_IMPACT_PILLARS), 4),
            "pillars": {
                key: {"label": label, "description": description}
                for key, (label, description) in TOTAL_IMPACT_PILLARS.items()
            },
            "caveat": (
                "This scenario-relative index is not used by any optimizer, is not an externally "
                "validated social-impact measure, and can change when the scenario or comparison "
                "set changes. The underlying physical and fairness metrics remain the auditable result."
            ),
        },
    })
    return payload


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Export a deterministic five-day Gurobi experiment for the BakedBoston web simulator."
    )
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 8, 24))
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--seed", type=int, default=2033)
    parser.add_argument("--drivers-per-day", type=int, default=12)
    parser.add_argument("--matching-interval-minutes", type=int, default=60)
    parser.add_argument("--max-simultaneous-drivers", type=int, choices=(2, 3), default=3)
    args = parser.parse_args(argv)

    raw = json.loads(args.scenario.read_text())
    if not isinstance(raw, dict):
        raise TypeError("The scenario file must contain one JSON object.")
    payload = build_web_payload(
        parse_snapshot(raw),
        start_date=args.start_date,
        days=args.days,
        seed=args.seed,
        drivers_per_day=args.drivers_per_day,
        matching_interval_minutes=args.matching_interval_minutes,
        max_simultaneous_drivers=args.max_simultaneous_drivers,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Web simulator export: {args.output}")


if __name__ == "__main__":
    main()

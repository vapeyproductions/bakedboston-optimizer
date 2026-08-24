from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .experiment import DEFAULT_POLICIES, ExperimentConfig, compare_policies
from .network import NetworkSnapshot, parse_snapshot
from .optimizer import OptimizationWeights
from .simulation import SimulationConfig
from .travel import HaversineTravelTimeProvider


POLICY_METADATA = {
    "bakedboston_mip": {
        "label": "BakedBoston Gurobi MIP",
        "description": "Maximizes completed assignments first, then total route quality across simultaneous drivers.",
    },
    "random_feasible": {
        "label": "Random feasible",
        "description": "Chooses randomly from routes that satisfy the same timing constraints.",
    },
    "shortest_route": {
        "label": "Shortest route",
        "description": "Greedily chooses the smallest drive-plus-wait burden.",
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


def _network_payload(snapshot: NetworkSnapshot) -> dict[str, Any]:
    def organization(item: Any, kind: str) -> dict[str, Any]:
        return {
            "id": f"{kind}-{item.id}",
            "name": item.name,
            "kind": kind,
            "latitude": item.latitude,
            "longitude": item.longitude,
        }

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
        include_trace = result["policy"] == "bakedboston_mip"
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
    seed: int = 2026,
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
        policies=DEFAULT_POLICIES,
        travel=HaversineTravelTimeProvider(),
        weights=weights,
    )
    payload = _compact_comparison(comparison.as_dict())
    payload["scenario"].update({
        "bakeryCount": len(snapshot.eligible_bakeries),
        "pantryCount": len(snapshot.eligible_pantries),
    })
    payload.update({
        "schemaVersion": 2,
        "displayMode": "interactive_replay_of_precomputed_gurobi_experiment",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceSnapshotGeneratedAt": snapshot.generated_at.isoformat(),
        "policyMetadata": POLICY_METADATA,
        "objectiveWeights": asdict(weights),
        "network": _network_payload(snapshot),
        "selectionRule": (
            "After the joint Gurobi solve resolves simultaneous-driver conflicts, "
            "each driver's conflict-free recommendations are ranked and rank 1 is selected."
        ),
        "metricMethodology": {
            "actualSelection": "Every simulated driver selects recommendation rank 1.",
            "acceptanceModel": (
                "Expected acceptance and likely-rejection measures are prediction-based diagnostics; "
                "they do not override the deterministic rank-1 selection used in this demonstration."
            ),
            "comparison": (
                "Policies share identical synthetic surplus, pantry, and driver events. A policy may "
                "win an individual metric without maximizing the BakedBoston lexicographic objective."
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
    parser.add_argument("--seed", type=int, default=2026)
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

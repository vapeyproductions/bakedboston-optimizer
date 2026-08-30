from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from .experiment import (
    DEFAULT_POLICIES,
    RoutingPolicy,
    compare_horizons,
    write_report,
    write_summary_csv,
)
from .network import parse_snapshot
from .travel import HaversineTravelTimeProvider


def _integers(value: str) -> tuple[int, ...]:
    try:
        result = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError("use comma-separated integers") from error
    if not result or any(item < 1 for item in result):
        raise argparse.ArgumentTypeError("values must be positive integers")
    return result


def _policies(value: str) -> tuple[RoutingPolicy, ...]:
    try:
        result = tuple(
            RoutingPolicy(item.strip()) for item in value.split(",") if item.strip()
        )
    except ValueError as error:
        choices = ", ".join(item.value for item in RoutingPolicy)
        raise argparse.ArgumentTypeError(f"policy must be one of: {choices}") from error
    if not result:
        raise argparse.ArgumentTypeError("at least one policy is required")
    return result


def _print_summary(report: Any) -> None:
    columns = (
        ("done", "completedDeliveries"),
        ("coverage", "bakeryPickupCoverage"),
        ("pantries", "pantryCoverageCount"),
        ("gini", "pantryServiceGini"),
        ("drive", "averageDriveMinutes"),
        ("quality", "totalRouteQuality"),
        ("avgQuality", "averageRouteQuality"),
        ("runtime", "totalSolverRuntimeSeconds"),
    )
    for horizon, policies in report.summary().items():
        print(f"\n{horizon}-day horizon")
        print("policy".ljust(26), *(label.rjust(11) for label, _ in columns))
        for policy, metrics in policies.items():
            values = [
                _format_metric(metrics[field]).rjust(11)
                for _, field in columns
            ]
            print(policy.ljust(26), *values)


def _format_metric(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the BakedBoston rolling-horizon Gurobi MIP with transparent "
            "routing baselines on identical seeded scenarios."
        )
    )
    parser.add_argument("scenario", type=Path, help="Schedule snapshot JSON file")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--horizons", type=_integers, default=(3, 4, 5))
    parser.add_argument("--seeds", type=_integers, default=(2026,))
    parser.add_argument("--drivers-per-day", type=int, default=8)
    parser.add_argument("--bakery-food-probability", type=float, default=0.75)
    parser.add_argument("--staffed-pantry-open-probability", type=float, default=0.90)
    parser.add_argument(
        "--matching-interval-minutes",
        type=int,
        default=15,
        help="Group driver arrivals into decision epochs of this size (default: 15)",
    )
    parser.add_argument(
        "--max-simultaneous-drivers",
        type=int,
        choices=(2, 3),
        default=3,
        help="Cap each decision epoch at two or three drivers (default: 3)",
    )
    parser.add_argument(
        "--disable-acceptance",
        action="store_true",
        help="Treat every selected route as accepted for a pure routing-capacity experiment",
    )
    parser.add_argument(
        "--policies",
        type=_policies,
        default=DEFAULT_POLICIES,
        help="Comma-separated policy identifiers; defaults to every policy",
    )
    parser.add_argument("--output", type=Path, help="Write the full JSON report here")
    parser.add_argument(
        "--summary-csv",
        type=Path,
        help="Write one analysis-ready summary row per horizon and policy here",
    )
    args = parser.parse_args(argv)

    payload: Any = json.loads(args.scenario.read_text())
    if not isinstance(payload, dict):
        raise TypeError("The scenario file must contain one JSON object.")
    report = compare_horizons(
        parse_snapshot(payload),
        start_date=args.start_date,
        horizons=args.horizons,
        seeds=args.seeds,
        policies=args.policies,
        drivers_per_day=args.drivers_per_day,
        bakery_food_probability=args.bakery_food_probability,
        staffed_pantry_open_probability=args.staffed_pantry_open_probability,
        matching_interval_minutes=args.matching_interval_minutes,
        max_simultaneous_drivers=args.max_simultaneous_drivers,
        acceptance_enabled=not args.disable_acceptance,
        travel=HaversineTravelTimeProvider(),
    )
    _print_summary(report)
    if args.output:
        write_report(report, args.output)
        print(f"\nFull report: {args.output}")
    if args.summary_csv:
        write_summary_csv(report, args.summary_csv)
        print(f"Summary CSV: {args.summary_csv}")


if __name__ == "__main__":
    main()

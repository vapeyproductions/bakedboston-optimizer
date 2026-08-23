from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from .network import parse_snapshot
from .simulation import SimulationConfig, simulate_snapshot
from .travel import HaversineTravelTimeProvider


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a reproducible BakedBoston academic schedule simulation."
    )
    parser.add_argument("scenario", type=Path, help="Path to a schedule snapshot JSON file")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--drivers-per-day", type=int, default=8)
    parser.add_argument("--bakery-food-probability", type=float, default=0.75)
    parser.add_argument("--staffed-pantry-open-probability", type=float, default=0.90)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload: Any = json.loads(args.scenario.read_text())
    if not isinstance(payload, dict):
        raise TypeError("The scenario file must contain one JSON object.")
    report = simulate_snapshot(
        parse_snapshot(payload),
        SimulationConfig(
            start_date=args.start_date,
            days=args.days,
            random_seed=args.seed,
            drivers_per_day=args.drivers_per_day,
            bakery_food_probability=args.bakery_food_probability,
            staffed_pantry_open_probability=args.staffed_pantry_open_probability,
        ),
        HaversineTravelTimeProvider(),
    ).as_dict()
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(f"{rendered}\n")
    else:
        print(rendered)


if __name__ == "__main__":
    main()

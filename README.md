# BakedBoston Optimizer

An explainable operations-research system for matching time-sensitive bakery pickups with food pantries and volunteer drivers.

This repository contains the reproducible optimization work behind [BakedBoston](https://www.baked-boston.com). It is intentionally separate from the web and mobile product so hackathon judges can inspect the mathematical model, solver, tests, and production boundary without navigating application UI code.

## What is implemented

- address-first location records with cached validated coordinates;
- Google traffic-aware route durations behind a replaceable provider interface;
- recurring and one-time bakery and pantry schedule ingestion;
- hard time-window feasibility with five-minute pickup and drop-off service;
- a transparent single-driver recommendation score;
- a two-stage OR-Tools mixed-integer model for simultaneous driver assignments;
- a private authenticated connection to live BakedBoston operational data;
- deterministic demonstrations and 13 automated tests.

## Optimization model

For every time-feasible driver–bakery–pantry route, the model creates a binary assignment variable. It enforces:

- at most one route per driver in a batch;
- at most one driver per bakery pickup;
- pantry opening, closing, and latest-arrival times;
- driver start and finish limits;
- confirmed, unclaimed bakery availability.

Pantries are never removed because they have already received deliveries. Their recent receipt history lowers priority rather than making a route infeasible.

The model first maximizes the number of completed bakery pickups, then—without reducing that number—maximizes:

```text
45 × pantry priority
− 1.00 × driving minutes
− 0.35 × waiting minutes
− 0.65 × minutes from the driver's preferred destination
```

The lexicographic objective guarantees that a feasible delivery is preferred to no delivery. See [docs/model.md](docs/model.md) for the complete sets, variables, equations, constraints, and timing logic.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
python -m bakedboston_optimizer.demo
python -m bakedboston_optimizer.batch_demo
```

The demonstrations and tests need no API key. They use deterministic travel inputs or the coordinate-based fallback.

The batch demonstration contains a deliberately non-greedy case. Choosing the individually highest-scoring route would complete only one pickup; the MIP instead selects two compatible routes and proves why a global assignment model matters.

See [docs/verification.md](docs/verification.md) for the current reproducible result and the behavior covered by the test suite.

## Repository structure

```text
bakedboston_optimizer/
  models.py          Typed address, schedule, request, and route records
  optimizer.py       Candidate feasibility and single-driver route scoring
  batch.py           Two-stage OR-Tools mixed-integer assignment
  travel.py          Replaceable travel-time provider boundary
  google_maps.py     Google validation and traffic-aware routes
  network.py         Authenticated BakedBoston operational-data client
  service.py         Live recommendation service
  demo.py            Deterministic route-ranking demonstration
  batch_demo.py      Non-greedy batch-assignment demonstration
api/
  recommendations.py  Authenticated feasibility and scoring API
  assignments.py      Authenticated OR-Tools allocation API
tests/                 Feasibility, data-boundary, and MIP tests
docs/model.md          Full mathematical formulation
```

## Live application boundary

The BakedBoston server sends each driver's requested time window and current coordinates to the authenticated recommendation API. The optimizer reads confirmed pickup occurrences and open pantry windows from the app's private operational feed, calculates traffic-aware feasible routes, and returns scored recommendations with explanations.

When a confirmed pickup is matched against multiple saved driver requests, the app sends those feasible candidates to the authenticated assignment API. OR-Tools selects the primary offer under the driver and pickup constraints; remaining feasible requests form the fallback queue. If either optimizer endpoint is unavailable, the app retains a local distance-based fallback so a temporary solver outage does not disable deliveries.

The feed excludes login credentials, contact details, access instructions, photographs, dates of birth, and identity-verification information. Keys remain server-side and must never be committed.

When `GOOGLE_MAPS_API_KEY` is configured on the optimizer deployment, travel durations reflect requested departure times. Without it, the same model uses `HaversineTravelTimeProvider`, preserving reproducibility and graceful fallback behavior.

## Secure local configuration

Copy `.env.example` to `.env` only in a secure local environment:

```bash
BAKEDBOSTON_BASE_URL=https://www.baked-boston.com
OPTIMIZER_API_KEY=replace-with-the-shared-server-secret
GOOGLE_MAPS_API_KEY=replace-with-a-server-restricted-key
```

To validate and write back cached locations for registered partners:

```bash
set -a
source .env
python -m bakedboston_optimizer.sync_locations
```

Only registered organizations appear in the live feed, and only administrator-confirmed validated locations are optimization-eligible.

## Planned extensions

- a batched Google route-matrix adapter for larger networks;
- priority based on deliveries per eligible receiving opportunity;
- calibrated driver route-acceptance probabilities;
- multi-day and multi-route driver planning;
- weight sensitivity dashboards for the hackathon presentation.

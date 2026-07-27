# BakedBoston Optimizer

Operations-research models for matching time-sensitive bakery pickups with food pantries and volunteer drivers.

This repository is intentionally separate from the BakedBoston web and mobile application. It contains the reproducible optimization work intended for the hackathon submission and will later contain driver-acceptance ML experiments.

## Address-first location design

The actual street address remains the source of truth for every bakery and pantry. Google Address Validation can standardize that address and return a geocode. BakedBoston then stores:

- the address as entered;
- the formatted address;
- the Google Place ID;
- latitude and longitude;
- address-validation status.

The optimizer uses cached coordinates for fast, reproducible calculations. `GoogleMapsProvider` validates real addresses and provides traffic-aware durations without changing the optimization model. The coordinate fallback remains available for reproducible tests.

## Version 0 model

The first model:

1. Generates every bakery–pantry route candidate.
2. Removes only infeasible routes.
3. Calculates pickup, arrival, and completion times.
4. Scores feasible routes using travel time, pantry priority, waiting time, and optional destination preference.
5. Returns ranked, explainable recommendations.

Hard feasibility rules currently include:

- the bakery pickup is unclaimed;
- the driver can arrive by the pickup deadline;
- pickup service takes 15 minutes;
- the pantry is accepting deliveries;
- the driver arrives by the pantry's latest permitted arrival;
- drop-off service takes 15 minutes;
- the trip finishes within the driver's time window.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m unittest discover -s tests -v
python -m bakedboston_optimizer.demo
```

No API key is required for the test suite or demo. The included `HaversineTravelTimeProvider` estimates driving time from coordinates so the model remains reproducible.

## Repository structure

```text
bakedboston_optimizer/
  models.py          Address-first input and output models
  travel.py          Replaceable travel-time providers
  optimizer.py       Feasibility and route-ranking logic
  demo.py            Small reproducible demonstration
tests/
  test_optimizer.py  Feasibility and ranking tests
docs/
  model.md           Mathematical definition and roadmap
```

## Google Maps integration

Set `GOOGLE_MAPS_API_KEY` only in a secure server environment. The current adapter supports:

- Address Validation to standardize an entered address and cache its Place ID and coordinates;
- traffic-aware `ComputeRoutes` durations for a requested departure time.

`ComputeRouteMatrix` batching will be added when the initial model is connected to live organization data. Batching is an implementation optimization; the feasibility and scoring model already depends only on the `TravelTimeProvider` boundary.

## Private BakedBoston data feed

`BakedBostonNetworkClient` reads active operational data from the app's private `/api/optimizer/network` endpoint. It sends `OPTIMIZER_API_KEY` as a bearer token and rejects non-HTTPS URLs.

The feed contains organization addresses, cached geocodes, schedules, availability changes, and route status history. It intentionally excludes logins, contacts, access instructions, photographs, and driver private data.

Confirmed dated pickup occurrences, saved driver ride requests, and route-offer state are also included so the optimization model can rank advance matches as soon as a bakery commits to a pickup.

Only organizations with active registered partner accounts appear in this feed. To validate and save addresses for currently registered partners:

```bash
set -a
source .env
python -m bakedboston_optimizer.sync_locations
```

The app's write-back endpoint checks registration again before accepting each result.

The feed retains registered locations that still need validation or administrator review so the sync process can find them. Route models must use `snapshot.eligible_bakeries` and `snapshot.eligible_pantries`; those collections contain only administrator-confirmed Google locations.

API keys must remain server-side and must never be committed. Copy `.env.example` to `.env` only in a secure local environment.

## Roadmap

- Batch mixed-integer assignment for multiple drivers and pickups
- Google traffic-aware route-matrix adapter
- Recurring and one-time schedule ingestion from the app database
- Reservation and one-hour confirmation constraints
- Historical pantry priority calculation
- Driver route-acceptance probability model
- Multi-objective sensitivity analysis and hackathon visualizations

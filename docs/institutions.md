# Fixed academic institution parameters

These are fictional organizations used only in the seeded academic simulator.
Names, coordinates, ZIP codes, schedule windows, distribution parameters, and
waste allocations are fixed scenario inputs; they are not measurements or
claims about real organizations. Each bakery's daily food amount and usability
are deterministic seeded draws from its own fixed triangular distribution.

Notation: a triangular distribution is shown as `minimum / mode / maximum`.
Waste columns are fixed shares and sum to 100% for each bakery.

## Bakeries

| Bakery | Coordinates | ZIP | Recurring pickup window | Food kg distribution | Usability distribution | Landfill | Pig farm | Compost |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: |
| Back Bay Bakes | 42.3503, -71.0810 | 02116 | Mon–Fri 16:00–16:40 | 11.41 / 16.47 / 23.30 | 77.9% / 86.2% / 94.0% | 51.9% | 21.1% | 27.0% |
| North End Bread Lab | 42.3651, -71.0547 | 02113 | Mon–Fri 16:20–17:05 | 9.60 / 14.21 / 19.23 | 69.1% / 79.2% / 85.5% | 31.8% | 58.5% | 9.7% |
| Roxbury Community Oven | 42.3249, -71.0827 | 02119 | Mon–Fri 16:45–17:30 | 9.78 / 15.25 / 20.83 | 65.9% / 77.4% / 84.4% | 37.1% | 9.9% | 53.0% |
| Cambridge Crumb Project | 42.3736, -71.1097 | 02138 | Mon–Fri 17:00–17:45 | 14.39 / 18.61 / 25.70 | 71.2% / 79.2% / 85.6% | 62.5% | 25.1% | 12.4% |
| Somerville Sweets Studio | 42.3854, -71.0952 | 02143 | Mon–Fri 17:20–18:00 | 17.12 / 22.77 / 27.79 | 61.9% / 72.2% / 79.2% | 24.6% | 62.9% | 12.5% |
| Jamaica Plain Pastry Co | 42.3097, -71.1151 | 02130 | Mon/Wed/Fri 15:30–16:20 | 13.31 / 20.07 / 26.59 | 80.6% / 87.8% / 94.7% | 37.3% | 5.6% | 57.1% |
| Brighton Bread Works | 42.3489, -71.1530 | 02135 | Tue/Thu 17:40–18:30 | 15.34 / 19.51 / 26.30 | 72.2% / 82.4% / 90.8% | 73.4% | 11.5% | 15.1% |
| Charlestown Crust Lab | 42.3782, -71.0602 | 02129 | Mon–Fri 18:00–18:50 | 9.31 / 15.14 / 21.04 | 68.0% / 77.0% / 82.8% | 46.9% | 39.2% | 13.9% |
| Fenway Flour Studio | 42.3431, -71.0990 | 02215 | Mon/Tue/Thu/Fri 15:50–16:35 | 17.92 / 22.39 / 29.89 | 66.8% / 77.1% / 84.5% | 44.3% | 16.7% | 39.0% |

## Pantries

| Pantry | Coordinates | ZIP | Recurring receiving window | Latest arrival | Distribution fraction |
| --- | --- | --- | --- | --- | ---: |
| Downtown Community Shelf | 42.3552, -71.0603 | 02108 | Mon–Fri 16:15–18:00 | 17:50 | 70.7% |
| Dorchester Food Cabinet | 42.3017, -71.0602 | 02122 | Mon–Fri 16:30–18:15 | 18:05 | 66.3% |
| Allston Neighborhood Pantry | 42.3541, -71.1346 | 02134 | Mon–Fri 16:45–18:30 | 18:20 | 83.4% |
| Cambridge Mutual Aid Pantry | 42.3812, -71.1124 | 02140 | Mon–Fri 17:00–19:00 | 18:50 | 81.8% |
| East Boston Open Fridge | 42.3755, -71.0350 | 02128 | Mon–Fri 17:15–18:30 | 18:20 | 81.9% |
| Jamaica Plain Community Fridge | 42.3182, -71.1120 | 02130 | Mon–Fri 15:45–19:00 | 18:50 | 63.8% |
| Brighton Mutual Aid Shelf | 42.3504, -71.1650 | 02135 | Tue/Thu 17:30–19:30 | 19:20 | 71.0% |
| Charlestown Neighborhood Pantry | 42.3785, -71.0640 | 02129 | Mon/Wed/Fri 17:45–19:00 | 18:50 | 66.1% |
| Mission Hill Open Shelf | 42.3337, -71.1050 | 02120 | Mon–Fri 16:00–18:45 | 18:35 | 80.8% |

The machine-readable source of truth is
[`data/academic_comparison_snapshot.json`](../data/academic_comparison_snapshot.json).

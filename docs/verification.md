# Verification record

Run from the repository root with Python 3.11 or newer:

```bash
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
python -m bakedboston_optimizer.demo
python -m bakedboston_optimizer.batch_demo
```

Current verified result:

```text
Ran 14 tests
OK

status=optimal
assignments=2/2
route_score=17.00
d1: Bakery 2 -> Pantry 1 (score=9.00)
d2: Bakery 1 -> Pantry 2 (score=8.00)
```

The batch demonstration is intentionally constructed so a greedy choice of the individually highest-scoring route would use Bakery 1 for driver 1 and leave driver 2 unmatched. The mixed-integer solution assigns Bakery 2 to driver 1 and Bakery 1 to driver 2, completing two deliveries with total route quality 17.

The automated suite separately verifies:

- five-minute pickup and drop-off timing;
- pickup-deadline and pantry-arrival feasibility;
- exclusion of claimed bakery occurrences;
- configurable pantry-priority tradeoffs;
- maximum-cardinality assignment even when every feasible score is negative;
- at-most-one constraints for drivers and bakery pickups;
- the live application's assignment-service payload boundary;
- parsing of the authenticated operational feed without contact data;
- exclusion of unvalidated coordinates;
- staffed pantry opening confirmation and unattended-window eligibility.

GitHub Actions executes the same tests and both demonstrations on every push and pull request.

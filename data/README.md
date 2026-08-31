# Institution schedule data

This directory holds inputs for the academic demonstration. Institution names,
addresses, coordinates, and schedules may be based on public information, but
their inclusion must never be presented as participation in or endorsement of
BakedBoston.

For each real institution, record:

- the public source URL;
- the date the schedule was last checked;
- the address and validated coordinates;
- each recurring or monthly window;
- whether the window is modeled as staffed or unattended;
- any modeling assumption that is not explicitly stated by the source.

`example_schedule_snapshot.json` is deliberately fictional. It documents the
accepted format without claiming a relationship with a real organization.

`academic_comparison_snapshot.json` is also entirely fictional. It contains a
larger, contention-rich Boston-area geometry with staggered windows so the
Gurobi policy and comparison heuristics can be evaluated on a meaningful
multi-driver assignment problem. Its organization names and addresses are
academic labels, not real institutions.

Each pantry in the academic comparison snapshot also has a fixed, unique
landfill/pig-farm allocation for redistribution waste. Those two shares sum to
100% and pantry compost is 0%. They are paper-bounded scenario assumptions, not
observations about any institution.

The simulator reads this data but never registers an account, sends a message,
reserves food, or modifies an institution's information.

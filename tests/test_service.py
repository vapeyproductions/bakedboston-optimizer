from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from bakedboston_optimizer.network import NetworkSnapshot, OrganizationRecord
from bakedboston_optimizer.service import _pantries


class ServiceEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.zone = ZoneInfo("America/New_York")
        self.earliest = datetime(2026, 7, 27, 17, tzinfo=self.zone)
        self.latest = datetime(2026, 7, 27, 19, tzinfo=self.zone)
        self.pantry = OrganizationRecord(
            id=2,
            name="Pantry",
            address="2 Main Street",
            formatted_address="2 Main Street, Boston, MA",
            google_place_id="place-2",
            address_validation_status="validated",
            latitude=42.35,
            longitude=-71.06,
            schedule={
                "openTime": "[]",
                "closeTime": "[]",
                "latestPermittedArrival": "[]",
                "serviceModes": "[]",
                "deliveriesSevenDays": 0,
            },
        )

    def snapshot(self, service_mode: str, confirmations: tuple[dict[str, object], ...] = ()) -> NetworkSnapshot:
        return NetworkSnapshot(
            schema_version=2,
            generated_at=self.earliest,
            bakeries=(),
            pantries=(self.pantry,),
            availability_windows=({
                "id": 8,
                "organizationType": "pantry",
                "organizationId": 2,
                "startsAt": self.earliest.isoformat(),
                "endsAt": self.latest.isoformat(),
                "latestArrival": self.latest.isoformat(),
                "serviceMode": service_mode,
                "paused": False,
            },),
            availability_pauses=(),
            schedule_exceptions=(),
            routes=(),
            pickup_occurrences=(),
            ride_requests=(),
            route_offers=(),
            pantry_window_confirmations=confirmations,
        )

    def test_staffed_window_requires_opening_confirmation(self) -> None:
        self.assertEqual(_pantries(self.snapshot("staffed"), self.earliest, self.latest), [])
        confirmed = ({
            "recipientId": 2,
            "actionKey": "pantry-open:2:one-time:8",
            "acknowledgedAt": self.earliest.isoformat(),
        },)
        self.assertEqual(len(_pantries(self.snapshot("staffed", confirmed), self.earliest, self.latest)), 1)

    def test_unattended_window_needs_no_confirmation(self) -> None:
        self.assertEqual(len(_pantries(self.snapshot("unattended"), self.earliest, self.latest)), 1)


if __name__ == "__main__":
    unittest.main()

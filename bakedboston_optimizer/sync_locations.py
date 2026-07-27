from __future__ import annotations

import os

from .google_maps import GoogleMapsProvider
from .network import BakedBostonNetworkClient


def main() -> None:
    google = GoogleMapsProvider(os.environ["GOOGLE_MAPS_API_KEY"])
    client = BakedBostonNetworkClient(os.environ["BAKEDBOSTON_BASE_URL"], os.environ["OPTIMIZER_API_KEY"])
    snapshot = client.fetch()
    pending = [
        ("bakery", organization)
        for organization in snapshot.bakeries
        if organization.address_validation_status == "unvalidated"
    ] + [
        ("pantry", organization)
        for organization in snapshot.pantries
        if organization.address_validation_status == "unvalidated"
    ]
    for organization_type, organization in pending:
        location = google.validate_address(organization.address)
        client.save_validated_location(organization_type, organization.id, location)
        print(f"{organization_type} {organization.id}: {location.validation_status.value}")
    print(f"Validated {len(pending)} registered organization address(es).")


if __name__ == "__main__":
    main()

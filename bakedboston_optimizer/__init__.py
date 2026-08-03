"""BakedBoston route optimization package."""

from .models import BakeryPickup, DriverRequest, Location, Pantry, RouteCandidate
from .optimizer import OptimizationWeights, rank_routes
from .batch import AssignmentCandidate, BatchAssignmentResult, build_assignment_candidates, optimize_batch, solve_assignment
from .travel import HaversineTravelTimeProvider, TravelTimeProvider
from .google_maps import GoogleMapsProvider
from .network import BakedBostonNetworkClient, NetworkSnapshot, OrganizationRecord

__all__ = [
    "BakeryPickup",
    "AssignmentCandidate",
    "BatchAssignmentResult",
    "BakedBostonNetworkClient",
    "DriverRequest",
    "HaversineTravelTimeProvider",
    "GoogleMapsProvider",
    "Location",
    "OptimizationWeights",
    "NetworkSnapshot",
    "OrganizationRecord",
    "Pantry",
    "RouteCandidate",
    "TravelTimeProvider",
    "build_assignment_candidates",
    "optimize_batch",
    "rank_routes",
    "solve_assignment",
]

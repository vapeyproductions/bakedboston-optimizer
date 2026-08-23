"""BakedBoston route optimization package."""

from .models import (
    AssignmentCandidate,
    BakeryPickup,
    DriverRequest,
    Location,
    NetworkOptimizationResult,
    Pantry,
    RouteCandidate,
)
from .optimizer import (
    OptimizationWeights,
    active_solver_backend,
    enumerate_assignment_candidates,
    optimize_assignment_candidates,
    optimize_network,
    rank_routes,
    solver_version,
)
from .travel import HaversineTravelTimeProvider, TravelTimeProvider
from .google_maps import GoogleMapsProvider
from .network import BakedBostonNetworkClient, NetworkSnapshot, OrganizationRecord
from .simulation import (
    SimulationConfig,
    SimulationReport,
    pantry_priority,
    simulate_snapshot,
)
from .experiment import (
    ComparisonReport,
    DecisionEpochResult,
    DEFAULT_POLICIES,
    ExperimentCandidate,
    ExperimentConfig,
    ExperimentScenario,
    HorizonComparisonReport,
    PolicyReport,
    RoutingPolicy,
    build_scenario,
    compare_horizons,
    compare_policies,
    run_policy,
)

__all__ = [
    "AssignmentCandidate",
    "BakeryPickup",
    "BakedBostonNetworkClient",
    "DriverRequest",
    "HaversineTravelTimeProvider",
    "GoogleMapsProvider",
    "Location",
    "OptimizationWeights",
    "active_solver_backend",
    "NetworkOptimizationResult",
    "NetworkSnapshot",
    "OrganizationRecord",
    "Pantry",
    "RouteCandidate",
    "SimulationConfig",
    "SimulationReport",
    "ComparisonReport",
    "DecisionEpochResult",
    "DEFAULT_POLICIES",
    "ExperimentConfig",
    "ExperimentCandidate",
    "ExperimentScenario",
    "HorizonComparisonReport",
    "PolicyReport",
    "RoutingPolicy",
    "TravelTimeProvider",
    "pantry_priority",
    "build_scenario",
    "compare_horizons",
    "compare_policies",
    "enumerate_assignment_candidates",
    "optimize_assignment_candidates",
    "optimize_network",
    "rank_routes",
    "solver_version",
    "run_policy",
    "simulate_snapshot",
]

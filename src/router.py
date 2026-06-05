"""Energy zone router and grid API client."""

from dataclasses import dataclass
from typing import Protocol

import httpx

from src.config import settings
from src.pricing import StateVector


# Persistent HTTP client (connection-pooled)
_persistent_client: httpx.AsyncClient | None = None


def get_persistent_client() -> httpx.AsyncClient:
    global _persistent_client
    if _persistent_client is None:
        _persistent_client = httpx.AsyncClient(timeout=10.0)
    return _persistent_client


@dataclass(frozen=True)
class Zone:
    """A surplus-energy compute zone."""

    id: str
    name: str
    region: str
    compute_cost_usd_per_kwh: float
    carbon_g_per_kwh: float
    tax_credit_usd_per_kwh: float
    available_mw: float
    lat: float
    lon: float


class GridClient(Protocol):
    """Abstract interface for power grid data."""

    async def fetch_zones(self) -> list[Zone]: ...


class StaticGridClient:
    """Fallback static grid client for development / testing."""

    async def fetch_zones(self) -> list[Zone]:
        return [
            Zone(
                id="tx-solar-01",
                name="West Texas Solar Surplus",
                region="us-south",
                compute_cost_usd_per_kwh=0.04,
                carbon_g_per_kwh=35.0,
                tax_credit_usd_per_kwh=0.021,
                available_mw=450.0,
                lat=31.0,
                lon=-102.0,
            ),
            Zone(
                id="no-wind-02",
                name="North Sea Wind Curtailment",
                region="eu-north",
                compute_cost_usd_per_kwh=0.03,
                carbon_g_per_kwh=12.0,
                tax_credit_usd_per_kwh=0.018,
                available_mw=820.0,
                lat=56.0,
                lon=3.0,
            ),
            Zone(
                id="ca-hydro-03",
                name="Quebec Hydro Overflow",
                region="ca-east",
                compute_cost_usd_per_kwh=0.025,
                carbon_g_per_kwh=8.0,
                tax_credit_usd_per_kwh=0.015,
                available_mw=1200.0,
                lat=52.0,
                lon=-72.0,
            ),
        ]


class HttpGridClient:
    """Production HTTP grid API client (uses persistent connection pool)."""

    def __init__(self, base_url: str = settings.grid_api_url, api_key: str = settings.grid_api_key):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = get_persistent_client()

    async def fetch_zones(self) -> list[Zone]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            resp = await self._client.get(f"{self.base_url}/zones", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return [Zone(**z) for z in data.get("zones", [])]
        except (httpx.HTTPError, httpx.DecodingError) as exc:
            raise RouterError(f"Grid API unreachable: {exc}") from exc


class RouterError(Exception):
    """Routing failure."""

    pass


def _zone_to_state(zone: Zone, workload_kwh: float = 10.0) -> StateVector:
    """Convert a zone rate to an absolute state vector for a given workload."""
    return StateVector(
        compute_cost_usd=round(zone.compute_cost_usd_per_kwh * workload_kwh, 2),
        carbon_g=round(zone.carbon_g_per_kwh * workload_kwh, 1),
        tax_value_usd=round(zone.tax_credit_usd_per_kwh * workload_kwh, 2),
    )


def _zone_score(zone: Zone, workload_kwh: float = 10.0) -> float:
    """Multi-objective score: lower is better.

    Combines compute cost, carbon impact, and tax credit value with
    configurable weights. Current weighting prioritizes cost savings
    (70%), carbon reduction (20%), and tax credits (10%).
    """
    state = _zone_to_state(zone, workload_kwh)
    # Normalize against typical ranges to keep weights comparable
    cost_score = state.compute_cost_usd / 10.0  # ~0-1 normalized
    carbon_score = state.carbon_g / 5000.0  # ~0-1 normalized
    tax_score = -state.tax_value_usd / 1.0  # negative because higher tax credits are better
    return (cost_score * 0.7) + (carbon_score * 0.2) + (tax_score * 0.1)


async def find_optimal_zone(
    workload_kwh: float = 10.0,
    client: GridClient | None = None,
) -> tuple[Zone, StateVector]:
    """Locate the single most efficient energy surplus zone using multi-objective scoring."""
    grid = client or (HttpGridClient() if settings.grid_api_key else StaticGridClient())
    zones = await grid.fetch_zones()
    if not zones:
        raise RouterError("No surplus zones available")

    best_zone = min(zones, key=lambda z: _zone_score(z, workload_kwh))
    state = _zone_to_state(best_zone, workload_kwh)
    return best_zone, state

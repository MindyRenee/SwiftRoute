"""Tests for the energy zone router."""

import pytest

from src.router import (
    RouterError,
    StaticGridClient,
    Zone,
    find_optimal_zone,
)


class TestStaticGridClient:
    async def test_returns_multiple_zones(self) -> None:
        client = StaticGridClient()
        zones = await client.fetch_zones()
        assert len(zones) == 3
        for zone in zones:
            assert isinstance(zone, Zone)
            assert zone.id
            assert zone.compute_cost_usd_per_kwh > 0


class TestFindOptimalZone:
    async def test_selects_lowest_cost_zone(self) -> None:
        zone, state = await find_optimal_zone()
        assert zone.id == "ca-hydro-03"  # lowest cost in static data
        assert state.compute_cost_usd == pytest.approx(0.25, abs=0.01)

    async def test_workload_kwh_scaling(self) -> None:
        zone, state = await find_optimal_zone(workload_kwh=20.0)
        assert state.compute_cost_usd == pytest.approx(0.50, abs=0.01)
        assert state.carbon_g == pytest.approx(160.0, abs=0.1)

    async def test_raises_on_empty_zones(self) -> None:
        class EmptyClient:
            async def fetch_zones(self) -> list[Zone]:
                return []

        with pytest.raises(RouterError, match="No surplus zones available"):
            await find_optimal_zone(client=EmptyClient())

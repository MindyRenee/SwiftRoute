"""Tests for the Synthetic Grid Elasticity engine."""

from src.sge import GridEvent, SGEEventType, SyntheticGridElasticity


class TestSyntheticGridElasticity:
    async def test_disabled_sge_ignores_events(self) -> None:
        sge = SyntheticGridElasticity()
        sge._enabled = False
        event = GridEvent(
            event_type=SGEEventType.PEAK_DEMAND,
            zone_id="test",
            price_usd_per_mwh=6000.0,
            timestamp="2026-01-01T00:00:00Z",
        )
        result = await sge.process_event(event, "job-1")
        assert result["action"] == "ignored"

    async def test_peak_demand_freezes_job(self) -> None:
        sge = SyntheticGridElasticity()
        event = GridEvent(
            event_type=SGEEventType.PEAK_DEMAND,
            zone_id="test",
            price_usd_per_mwh=6000.0,
            timestamp="2026-01-01T00:00:00Z",
        )
        result = await sge.process_event(event, "job-1")
        assert result["action"] == "frozen"
        assert result["job_id"] == "job-1"
        assert result["premium_captured_usd"] > 0
        assert sge.is_paused("job-1") is True

    async def test_below_threshold_ignored(self) -> None:
        sge = SyntheticGridElasticity()
        event = GridEvent(
            event_type=SGEEventType.PEAK_DEMAND,
            zone_id="test",
            price_usd_per_mwh=100.0,
            timestamp="2026-01-01T00:00:00Z",
        )
        result = await sge.process_event(event, "job-1")
        assert result["action"] == "ignored"
        assert result["reason"] == "below_peak_threshold"

    async def test_grid_stabilized_resumes_job(self) -> None:
        sge = SyntheticGridElasticity()
        # First freeze
        freeze_event = GridEvent(
            event_type=SGEEventType.PEAK_DEMAND,
            zone_id="test",
            price_usd_per_mwh=6000.0,
            timestamp="2026-01-01T00:00:00Z",
        )
        await sge.process_event(freeze_event, "job-1")
        assert sge.is_paused("job-1") is True

        # Then stabilize
        stable_event = GridEvent(
            event_type=SGEEventType.GRID_STABILIZED,
            zone_id="test",
            price_usd_per_mwh=0.0,
            timestamp="2026-01-01T00:15:00Z",
        )
        result = await sge.process_event(stable_event, "job-1")
        assert result["action"] == "resumed"
        assert result["free_compute"] is True
        assert sge.is_paused("job-1") is False

    async def test_stabilize_without_pause_ignored(self) -> None:
        sge = SyntheticGridElasticity()
        event = GridEvent(
            event_type=SGEEventType.GRID_STABILIZED,
            zone_id="test",
            price_usd_per_mwh=0.0,
            timestamp="2026-01-01T00:00:00Z",
        )
        result = await sge.process_event(event, "job-1")
        assert result["action"] == "ignored"
        assert result["reason"] == "job_not_paused"

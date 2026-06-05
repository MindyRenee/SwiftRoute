"""Synthetic Grid Elasticity (SGE) engine."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from src.config import settings


class SGEEventType(Enum):
    """Types of grid events that can trigger SGE."""

    PEAK_DEMAND = "peak_demand"
    EMERGENCY_SPIKE = "emergency_spike"
    GRID_STABILIZED = "grid_stabilized"


@dataclass(frozen=True)
class GridEvent:
    """A grid event webhook payload."""

    event_type: SGEEventType
    zone_id: str
    price_usd_per_mwh: float
    timestamp: str


class SGEHandler(Protocol):
    """Callback interface for SGE state changes."""

    async def on_freeze(self, job_id: str, event: GridEvent) -> None: ...
    async def on_resume(self, job_id: str, event: GridEvent) -> None: ...
    async def on_payout(self, job_id: str, amount_usd: float) -> None: ...


class SyntheticGridElasticity:
    """Monetizes idle AI runtime tunnels during grid balancing events."""

    def __init__(self) -> None:
        self._paused_jobs: dict[str, GridEvent] = {}
        self._handlers: list[SGEHandler] = []
        self._enabled = settings.sge_enabled

    def register_handler(self, handler: SGEHandler) -> None:
        self._handlers.append(handler)

    async def process_event(self, event: GridEvent, job_id: str) -> dict[str, Any]:
        """Handle an incoming grid event for a running job."""
        if not self._enabled:
            return {"action": "ignored", "reason": "SGE disabled"}

        if event.event_type in (SGEEventType.PEAK_DEMAND, SGEEventType.EMERGENCY_SPIKE):
            return await self._handle_peak(event, job_id)
        elif event.event_type == SGEEventType.GRID_STABILIZED:
            return await self._handle_stabilized(event, job_id)

        return {"action": "ignored", "reason": "unknown_event_type"}

    async def _handle_peak(self, event: GridEvent, job_id: str) -> dict[str, Any]:
        """Freeze compute and capture emergency utility payout."""
        if event.price_usd_per_mwh < settings.sge_peak_threshold_usd_per_mwh:
            return {"action": "ignored", "reason": "below_peak_threshold"}

        self._paused_jobs[job_id] = event

        for handler in self._handlers:
            await handler.on_freeze(job_id, event)

        # Elasticity Spread: up to 500% premium margin
        base_payout = 1.0  # normalized per-job baseline
        premium_multiplier = min(event.price_usd_per_mwh / 1000.0, 5.0)
        payout = round(base_payout * premium_multiplier, 2)

        for handler in self._handlers:
            await handler.on_payout(job_id, payout)

        return {
            "action": "frozen",
            "job_id": job_id,
            "pause_minutes": settings.sge_pause_minutes,
            "premium_captured_usd": payout,
            "client_credit_applied": True,
        }

    async def _handle_stabilized(self, event: GridEvent, job_id: str) -> dict[str, Any]:
        """Resume previously frozen jobs."""
        if job_id not in self._paused_jobs:
            return {"action": "ignored", "reason": "job_not_paused"}

        del self._paused_jobs[job_id]

        for handler in self._handlers:
            await handler.on_resume(job_id, event)

        return {
            "action": "resumed",
            "job_id": job_id,
            "free_compute": True,
            "enhanced_tax_certificate": True,
        }

    def is_paused(self, job_id: str) -> bool:
        return job_id in self._paused_jobs

"""In-memory async job queue with SGE freeze/resume support."""

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.config import settings
from src.persistence import Persistence

logger = logging.getLogger("sap.jobs")


class JobStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    FROZEN = "frozen"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    job_id: str
    script_b64: str
    zone_id: str
    zone_name: str
    payment_id: str
    total_charge: float
    status: JobStatus = JobStatus.QUEUED
    vm_logs: dict[str, Any] = field(default_factory=dict)
    receipt: dict[str, Any] = field(default_factory=dict)
    payment_result: dict[str, Any] = field(default_factory=dict)
    _freeze_event: asyncio.Event = field(default_factory=asyncio.Event)
    _resume_event: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        self._freeze_event.set()  # Not frozen by default

    def freeze(self) -> None:
        if self.status == JobStatus.RUNNING:
            self.status = JobStatus.FROZEN
            self._freeze_event.clear()

    def resume(self) -> None:
        if self.status == JobStatus.FROZEN:
            self.status = JobStatus.RUNNING
            self._resume_event.set()
            self._freeze_event.set()

    async def wait_if_frozen(self) -> None:
        """Block execution until the job is resumed or cancelled."""
        if self.status == JobStatus.FROZEN:
            await self._freeze_event.wait()


class JobQueue:
    """Async FIFO job queue with SGE demand-response hooks."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._jobs: dict[str, Job] = {}
        self._task: asyncio.Task[Any] | None = None
        self._persistence = Persistence()

    def start_worker(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._worker())
            logger.info("Job queue worker started")

    def stop_worker(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def enqueue(self, job: Job) -> None:
        self._jobs[job.job_id] = job
        await self._queue.put(job)
        self._persistence.create_job(
            job_id=job.job_id,
            script_b64=job.script_b64,
            zone_id=job.zone_id,
            zone_name=job.zone_name,
        )
        logger.info("Job enqueued: job_id=%s", job.job_id)

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def freeze_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job and job.status == JobStatus.RUNNING:
            job.freeze()
            self._persistence.update_job_status(job_id, "frozen")
            logger.info("Job frozen by SGE: job_id=%s", job_id)
            return True
        return False

    def resume_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job and job.status == JobStatus.FROZEN:
            job.resume()
            self._persistence.update_job_status(job_id, "running")
            logger.info("Job resumed by SGE: job_id=%s", job_id)
            return True
        return False

    async def _worker(self) -> None:
        """Background worker that continuously processes queued jobs."""
        while True:
            try:
                job = await self._queue.get()
                await self._process_job(job)
            except asyncio.CancelledError:
                logger.info("Job queue worker cancelled")
                raise
            except Exception:
                logger.exception("Unhandled error in job queue worker")

    async def _process_job(self, job: Job) -> None:
        """Execute a single job end-to-end with SGE freeze/resume support."""
        job.status = JobStatus.RUNNING
        self._persistence.update_job_status(job.job_id, "running")

        # Lazy imports to avoid circular deps at module load time
        from src.payments import get_processor
        from src.pricing import calculate_pricing
        from src.receipts import ReceiptGenerator
        from src.router import find_optimal_zone
        from src.sge import SyntheticGridElasticity
        from src.tunnel_manager import WireGuardTunnelManager
        from src.vm_manager import MicroVMManager

        # These are the global managers initialized in gateway lifespan
        from src import gateway as gw

        vm_mgr = gw.vm_mgr
        tunnel_mgr = gw.tunnel_mgr
        payment_proc = gw.payment_proc
        sge = gw.sge

        if vm_mgr is None or tunnel_mgr is None:
            job.status = JobStatus.FAILED
            self._persistence.update_job_status(job.job_id, "failed")
            logger.error("Managers not ready for job_id=%s", job.job_id)
            return

        try:
            # Create tunnel
            tunnel = await tunnel_mgr.create_tunnel()
            tunnel = tunnel_mgr.write_config(tunnel)

            # Create VM
            vm = await vm_mgr.create_vm(job.script_b64)
            gw._job_registry.add(job.job_id)

            # Check for freeze before execution
            await job.wait_if_frozen()

            # Execute VM (with periodic freeze checks in a real impl)
            logs = await vm_mgr.execute(vm)
            job.vm_logs = logs

            # Check for freeze after execution
            await job.wait_if_frozen()

            # Capture payment
            captured = await payment_proc.capture(job.payment_id, job.total_charge)
            job.payment_result = {
                "payment_id": captured.payment_id,
                "status": captured.status,
                "amount_usd": captured.amount_usd,
                "fee_usd": captured.fee_usd,
                "net_usd": captured.net_usd,
            }

            # Mint receipt
            zone, remote_state = await find_optimal_zone()
            pricing = calculate_pricing(remote_state)
            receipt = ReceiptGenerator.generate(
                job_id=job.job_id,
                carbon_diverted_g=pricing.delta_net.carbon_g,
                carbon_remote_g=remote_state.carbon_g,
                carbon_local_g=pricing.v_local.carbon_g,
                tax_credit_usd=pricing.delta_net.tax_value_usd,
                zone_name=job.zone_name,
                zone_region=job.zone_id,
            )
            job.receipt = json.loads(ReceiptGenerator.to_json(receipt))
            self._persistence.store_receipt(
                receipt_id=receipt.receipt_id,
                job_id=job.job_id,
                receipt_json=ReceiptGenerator.to_json(receipt),
            )

            # Persist results
            self._persistence.update_job_result(
                job_id=job.job_id,
                vm_logs=job.vm_logs,
                receipt=job.receipt,
                payment=job.payment_result,
            )

            job.status = JobStatus.COMPLETED
            self._persistence.update_job_status(job.job_id, "completed")
            logger.info(
                "Job completed: job_id=%s zone=%s amount_usd=%s",
                job.job_id,
                job.zone_name,
                job.total_charge,
            )

        except Exception:
            logger.exception("Job execution failed: job_id=%s", job.job_id)
            # Attempt refund
            try:
                await payment_proc.refund(job.payment_id, job.total_charge)
            except Exception:
                logger.exception("Refund failed for job_id=%s", job.job_id)
            job.status = JobStatus.FAILED
            self._persistence.update_job_status(job.job_id, "failed")

        finally:
            # Always cleanup
            try:
                if "vm" in locals():
                    await vm_mgr.destroy(vm)
            except Exception:
                logger.exception("VM destroy failed for job_id=%s", job.job_id)
            try:
                if "tunnel" in locals():
                    tunnel_mgr.tear_down(tunnel)
            except Exception:
                logger.exception("Tunnel teardown failed for job_id=%s", job.job_id)
            gw._job_registry.discard(job.job_id)

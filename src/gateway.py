"""SAP AI-native API gateway."""

import base64
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from pydantic import BaseModel, Field

from src.config import settings
from src.job_queue import Job, JobQueue
from src.payments import get_processor
from src.pricing import calculate_pricing
from src.receipts import ReceiptGenerator
from src.router import find_optimal_zone
from src.script_validator import ScriptValidationError, validate_script
from src.sge import GridEvent, SGEEventType, SyntheticGridElasticity
from src.tunnel_manager import WireGuardTunnelManager
from src.vm_manager import MicroVMManager

logger = logging.getLogger("sap.gateway")

# Simple in-memory rate limiter: {ip: [timestamp, ...]}
_rate_limit_store: dict[str, list[float]] = {}

# Valid job IDs created by this gateway instance
_job_registry: set[str] = set()

# Background job queue
_job_queue: JobQueue = JobQueue()

# Stripe is an optional dependency; import once at module level with graceful fallback.
try:
    import stripe as _stripe_module
except ImportError:
    _stripe_module = None  # type: ignore[assignment]


class RateLimitMiddleware:
    """Basic in-memory rate-limiting middleware with bounded store."""

    def __init__(self, fastapi_app: FastAPI, max_requests: int = 60, window_seconds: int = 60) -> None:
        self.app = fastapi_app
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def __call__(self, scope: Any, receive: Callable[[], Any], send: Callable[[Any], Any]) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window = self.window_seconds
        reqs = _rate_limit_store.setdefault(client_ip, [])
        # Prune old entries
        while reqs and reqs[0] < now - window:
            reqs.pop(0)
        # Enforce bounded store to prevent memory exhaustion
        if len(_rate_limit_store) > settings.rate_limit_max_entries:
            oldest_ip = min(
                _rate_limit_store,
                key=lambda k: _rate_limit_store[k][0] if _rate_limit_store[k] else now,
            )
            _rate_limit_store.pop(oldest_ip, None)
        if len(reqs) >= self.max_requests:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
            )
            await response(scope, receive, send)
            return
        reqs.append(now)
        await self.app(scope, receive, send)


class TicketRequest(BaseModel):
    """AI agent request payload."""

    action: str = Field(default="buy_subgrid_compute_ticket")
    execution_script_b64: str = Field(..., description="Base64 encoded python or bash code")
    max_budget_usd: float | None = Field(None, description="Maximum price agent will pay")
    stripe_payment_method_id: str | None = Field(
        None,
        description="Stripe PaymentMethod ID (pm_...) for instant charge. If omitted, gateway returns a PaymentIntent client_secret.",
    )


class TicketResponse(BaseModel):
    """Ticket purchase response."""

    job_id: str
    status: str
    zone: dict[str, Any]
    pricing: dict[str, Any]
    receipt: dict[str, Any]
    vm_logs: dict[str, Any]
    payment: dict[str, Any]


class GridWebhook(BaseModel):
    """Utility grid event webhook."""

    event_type: str
    zone_id: str
    price_usd_per_mwh: float
    timestamp: str
    job_id: str


class StripeWebhookSetup(BaseModel):
    """Request to create a Stripe webhook endpoint."""

    url: str = Field(..., description="HTTPS URL of your /webhook/stripe endpoint")
    description: str | None = Field(None, description="Optional description")
    connect: bool = Field(False, description="Receive events from connected accounts")


class CheckoutSessionRequest(BaseModel):
    """Request to create a Stripe Checkout Session for a human buyer."""

    amount_usd: float = Field(..., gt=0, description="Amount to charge in USD")
    customer_email: str | None = Field(None, description="Pre-fill customer email")
    success_url: str = Field(..., description="URL to redirect after successful payment")
    cancel_url: str = Field(..., description="URL to redirect if user cancels")
    script_b64: str = Field(..., description="Base64-encoded script to execute after payment")


class JobStatusResponse(BaseModel):
    """Job status lookup response."""

    job_id: str
    status: str
    zone: dict[str, Any] | None = None
    pricing: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    vm_logs: dict[str, Any] | None = None
    payment: dict[str, Any] | None = None


class ConnectPayoutRequest(BaseModel):
    """Request a Stripe Connect payout to a connected account."""

    stripe_account_id: str = Field(..., description="Connected account ID (acct_...)")
    amount_usd: float | None = Field(None, description="Amount to transfer; defaults to full balance")


# Global managers (initialized in lifespan)
vm_mgr: MicroVMManager | None = None
tunnel_mgr: WireGuardTunnelManager | None = None
sge: SyntheticGridElasticity | None = None
payment_proc = get_processor()


def _prune_job_registry() -> None:
    """Remove stale job IDs to prevent unbounded growth."""
    max_jobs = settings.rate_limit_max_entries
    if len(_job_registry) > max_jobs:
        active = set(_job_queue._jobs.keys())
        _job_registry.intersection_update(active)


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI) -> AsyncGenerator[None, None]:
    global vm_mgr, tunnel_mgr, sge, payment_proc
    vm_mgr = MicroVMManager()
    tunnel_mgr = WireGuardTunnelManager()
    sge = SyntheticGridElasticity()
    payment_proc = get_processor()
    _job_queue.start_worker()
    yield
    _job_queue.stop_worker()
    if vm_mgr:
        await vm_mgr.destroy_all()
    if tunnel_mgr:
        tunnel_mgr.teardown_all()


app = FastAPI(
    title="SubGrid Automaton Protocol",
    description="AI-native orchestration engine for routing agentic workloads to surplus-energy zones",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/")
async def root() -> dict[str, str]:
    return {"protocol": "SAP", "status": "online"}


@app.get("/tunnel_route_and_price")
async def tunnel_route_and_price() -> dict[str, Any]:
    """Returns the current optimal tunnel route and pricing breakdown."""
    zone, remote_state = await find_optimal_zone()
    pricing = calculate_pricing(remote_state)
    return {
        "optimal_zone": {
            "id": zone.id,
            "name": zone.name,
            "region": zone.region,
            "lat": zone.lat,
            "lon": zone.lon,
            "compute_cost_usd_per_kwh": zone.compute_cost_usd_per_kwh,
            "carbon_g_per_kwh": zone.carbon_g_per_kwh,
            "tax_credit_usd_per_kwh": zone.tax_credit_usd_per_kwh,
            "available_mw": zone.available_mw,
        },
        "pricing": {
            "local": {
                "compute_cost_usd": pricing.v_local.compute_cost_usd,
                "carbon_g": pricing.v_local.carbon_g,
                "tax_value_usd": pricing.v_local.tax_value_usd,
            },
            "remote": {
                "compute_cost_usd": pricing.v_remote.compute_cost_usd,
                "carbon_g": pricing.v_remote.carbon_g,
                "tax_value_usd": pricing.v_remote.tax_value_usd,
            },
            "fees": {
                "compute_cost_usd": pricing.v_fees.compute_cost_usd,
                "carbon_g": pricing.v_fees.carbon_g,
                "tax_value_usd": pricing.v_fees.tax_value_usd,
            },
            "delta_net": {
                "compute_cost_usd": pricing.delta_net.compute_cost_usd,
                "carbon_g": pricing.delta_net.carbon_g,
                "tax_value_usd": pricing.delta_net.tax_value_usd,
            },
            "creator_yield_usd": pricing.creator_yield_usd,
            "client_net_value_usd": pricing.client_net_value_usd,
            "is_profitable": pricing.is_profitable,
        },
        "ticket_endpoint": "POST /ticket",
        "parameters": {
            "execution_script_b64": {
                "type": "string",
                "description": "Base64 encoded python or bash code your agent needs to execute.",
            },
            "max_budget_usd": {
                "type": "number",
                "description": "The maximum price your agent is willing to pay for this run.",
            },
        },
    }


@app.get("/llms-full.txt")
async def llms_full_txt() -> str:
    """Extended machine-readable protocol documentation."""
    return (
        "# SubGrid Automaton Protocol (SAP)\n\n"
        "## Endpoint\n"
        "POST /ticket\n\n"
        "## Authentication\n"
        "None required for public gateway nodes.\n\n"
        "## Tool Schema\n"
        "See /tunnel_route_and_price for live route and pricing.\n\n"
        "## Parameters\n"
        "- execution_script_b64 (required): Base64-encoded script.\n"
        "- max_budget_usd (optional): Ceiling price.\n\n"
        "## Returns\n"
        "- job_id: UUID of the routed job.\n"
        "- zone: Surplus-energy zone details.\n"
        "- pricing: Full delta-net breakdown.\n"
        "- receipt: Signed GHG Scope 3 compliance receipt.\n"
        "- vm_logs: Verified execution logs from Firecracker MicroVM.\n"
    )


@app.get("/ai.txt")
async def ai_txt() -> str:
    """ai.txt standard — machine-readable API info for AI agents and crawlers."""
    return (
        "# AI Agent Access Protocol (AAP) for SAP\n\n"
        "## Contact\n"
        "- Name: SubGrid Automaton Protocol\n"
        "- URL: https://swiftroute-sap.onrender.com\n"
        "- Docs: https://swiftroute-sap.onrender.com/docs\n\n"
        "## Authentication\n"
        "- Type: None (public gateway)\n"
        "- Rate limit: 60 requests/minute per IP\n\n"
        "## Endpoints\n"
        "- GET /tunnel_route_and_price — live route and pricing\n"
        "- POST /ticket — buy compute ticket\n"
        "- POST /payment/intent — create Stripe PaymentIntent\n"
        "- POST /webhook/stripe — Stripe webhook receiver\n\n"
        "## Pricing Model\n"
        "- Base: local compute cost + creator fee\n"
        "- Fee: 4% transaction + $0.05 signature mint\n"
        "- Currency: USD\n\n"
        "## Execution Model\n"
        "- Runtime: Firecracker MicroVM (isolated)\n"
        "- Tunnel: WireGuard (kernel-level)\n"
        "- Auto-destruct: VM destroyed after execution\n"
    )


def _validate_script(decoded: bytes) -> None:
    """Reject scripts containing dangerous system-level patterns via AST whitelist."""
    try:
        validate_script(decoded)
    except ScriptValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Script validation failed: {exc}") from exc


@app.post("/ticket", response_model=TicketResponse)
async def buy_ticket(req: TicketRequest) -> TicketResponse:
    """Primary gateway: AI agent buys a compute ticket."""
    if req.action != "buy_subgrid_compute_ticket":
        raise HTTPException(status_code=400, detail="Unknown action")

    # 1. Decode and validate script
    try:
        decoded = base64.b64decode(req.execution_script_b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64: {exc}") from exc

    if len(decoded) > 10 * 1024 * 1024:  # 10 MiB limit
        raise HTTPException(status_code=400, detail="Script exceeds 10 MiB")

    _validate_script(decoded)

    # 2. Route to optimal surplus zone
    zone, remote_state = await find_optimal_zone()

    # 3. Verify profitability
    pricing = calculate_pricing(remote_state)
    if not pricing.is_profitable:
        raise HTTPException(status_code=409, detail="Route is not profitable; try later")

    if req.max_budget_usd is not None and remote_state.compute_cost_usd > req.max_budget_usd:
        raise HTTPException(status_code=402, detail="Remote cost exceeds max_budget_usd")

    # 4. Authorize payment for remote cost + creator yield
    total_charge = round(remote_state.compute_cost_usd + pricing.creator_yield_usd, 2)
    payment = await payment_proc.authorize(
        amount_usd=total_charge,
        description=f"SAP compute ticket: {zone.name}",
        payment_method_id=req.stripe_payment_method_id or "",
    )
    if payment.status == "failed":
        raise HTTPException(status_code=402, detail="Payment authorization failed")

    # 5. Enqueue job for async execution
    job_id = str(uuid.uuid4())
    _job_registry.add(job_id)
    _prune_job_registry()

    job = Job(
        job_id=job_id,
        script_b64=req.execution_script_b64,
        zone_id=zone.id,
        zone_name=zone.name,
        payment_id=payment.payment_id,
        total_charge=total_charge,
    )
    await _job_queue.enqueue(job)

    return TicketResponse(
        job_id=job_id,
        status="queued",
        zone={
            "id": zone.id,
            "name": zone.name,
            "region": zone.region,
            "lat": zone.lat,
            "lon": zone.lon,
        },
        pricing={
            "local": {
                "compute_cost_usd": pricing.v_local.compute_cost_usd,
                "carbon_g": pricing.v_local.carbon_g,
                "tax_value_usd": pricing.v_local.tax_value_usd,
            },
            "remote": {
                "compute_cost_usd": pricing.v_remote.compute_cost_usd,
                "carbon_g": pricing.v_remote.carbon_g,
                "tax_value_usd": pricing.v_remote.tax_value_usd,
            },
            "fees": {
                "compute_cost_usd": pricing.v_fees.compute_cost_usd,
                "carbon_g": pricing.v_fees.carbon_g,
                "tax_value_usd": pricing.v_fees.tax_value_usd,
            },
            "delta_net": {
                "compute_cost_usd": pricing.delta_net.compute_cost_usd,
                "carbon_g": pricing.delta_net.carbon_g,
                "tax_value_usd": pricing.delta_net.tax_value_usd,
            },
            "creator_yield_usd": pricing.creator_yield_usd,
            "client_net_value_usd": pricing.client_net_value_usd,
        },
        receipt={},
        vm_logs={},
        payment={
            "payment_id": payment.payment_id,
            "status": payment.status,
            "amount_usd": payment.amount_usd,
            "fee_usd": payment.fee_usd,
            "net_usd": payment.net_usd,
        },
    )


@app.get("/job/{job_id}")
async def get_job_status(job_id: str) -> JobStatusResponse:
    """Look up the current status and results of a queued job."""
    job = _job_queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job_id")
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status.value,
        zone={"id": job.zone_id, "name": job.zone_name} if job.zone_id else None,
        receipt=job.receipt if job.receipt else None,
        vm_logs=job.vm_logs if job.vm_logs else None,
        payment=job.payment_result if job.payment_result else None,
    )


@app.post("/webhook/grid")
async def grid_webhook(webhook: GridWebhook) -> dict[str, Any]:
    """Receive grid events for SGE demand-response."""
    if sge is None:
        raise HTTPException(status_code=503, detail="SGE engine not ready")

    if webhook.job_id not in _job_registry:
        raise HTTPException(status_code=404, detail="Unknown job_id")

    try:
        event_type = SGEEventType(webhook.event_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid event_type: {exc}") from exc

    event = GridEvent(
        event_type=event_type,
        zone_id=webhook.zone_id,
        price_usd_per_mwh=webhook.price_usd_per_mwh,
        timestamp=webhook.timestamp,
    )

    # Delegate to SGE engine for payout calculation, then freeze/resume via job queue
    result = await sge.process_event(event, webhook.job_id)

    if result.get("action") == "frozen":
        _job_queue.freeze_job(webhook.job_id)
    elif result.get("action") == "resumed":
        _job_queue.resume_job(webhook.job_id)

    logger.info("SGE event processed: job_id=%s action=%s", webhook.job_id, result.get("action"))
    return result


def _handle_charge_succeeded(obj: dict[str, Any]) -> dict[str, Any]:
    amount = obj.get("amount", 0) / 100.0
    logger.info("Charge succeeded: id=%s amount_usd=%.2f", obj.get("id"), amount)
    return {"status": "charge_succeeded", "charge_id": obj.get("id"), "amount_usd": amount}


def _handle_charge_failed(obj: dict[str, Any]) -> dict[str, Any]:
    failure = obj.get("failure_message", "unknown")
    logger.warning("Charge failed: id=%s reason=%s", obj.get("id"), failure)
    return {"status": "charge_failed", "charge_id": obj.get("id"), "reason": failure}


def _handle_charge_refunded(obj: dict[str, Any]) -> dict[str, Any]:
    amount_refunded = obj.get("amount_refunded", 0) / 100.0
    logger.info("Charge refunded: id=%s amount_usd=%.2f", obj.get("id"), amount_refunded)
    return {"status": "charge_refunded", "charge_id": obj.get("id"), "amount_refunded_usd": amount_refunded}


def _handle_dispute_created(obj: dict[str, Any]) -> dict[str, Any]:
    reason = obj.get("reason", "unknown")
    amount = obj.get("amount", 0) / 100.0
    logger.warning("Dispute created: id=%s charge=%s reason=%s amount_usd=%.2f", obj.get("id"), obj.get("charge"), reason, amount)
    return {"status": "dispute_created", "dispute_id": obj.get("id"), "charge_id": obj.get("charge"), "reason": reason, "amount_usd": amount}


def _handle_payout_event(event_type: str, obj: dict[str, Any]) -> dict[str, Any]:
    amount = obj.get("amount", 0) / 100.0
    status = obj.get("status", "unknown")
    logger.info("Payout event: type=%s id=%s amount_usd=%.2f status=%s", event_type, obj.get("id"), amount, status)
    return {"status": event_type.replace(".", "_"), "payout_id": obj.get("id"), "amount_usd": amount, "payout_status": status}


def _handle_transfer_created(obj: dict[str, Any]) -> dict[str, Any]:
    amount = obj.get("amount", 0) / 100.0
    logger.info("Transfer created: id=%s amount_usd=%.2f destination=%s", obj.get("id"), amount, obj.get("destination"))
    return {"status": "transfer_created", "transfer_id": obj.get("id"), "amount_usd": amount, "destination": obj.get("destination")}


def _handle_transfer_reversed(obj: dict[str, Any]) -> dict[str, Any]:
    amount = obj.get("amount_reversed", 0) / 100.0
    logger.info("Transfer reversed: id=%s amount_usd=%.2f", obj.get("id"), amount)
    return {"status": "transfer_reversed", "transfer_id": obj.get("id"), "amount_reversed_usd": amount}


@app.post("/webhook/stripe")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    """Receive and process Stripe webhooks with signature verification."""
    if settings.payment_mode != "stripe" or not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Stripe webhooks not configured")

    if _stripe_module is None:
        raise HTTPException(status_code=503, detail="Stripe library not installed")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = _stripe_module.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Webhook verification failed: {exc}") from exc

    event_type = event["type"]
    obj: dict[str, Any] = event["data"]["object"]
    logger.info("Stripe webhook received: type=%s event_id=%s", event_type, event.get("id"))

    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "charge.succeeded": _handle_charge_succeeded,
        "charge.failed": _handle_charge_failed,
        "charge.refunded": _handle_charge_refunded,
        "charge.dispute.created": _handle_dispute_created,
        "payment_intent.succeeded": _handle_charge_succeeded,
        "payment_intent.payment_failed": _handle_charge_failed,
        "payout.created": lambda o: _handle_payout_event("payout.created", o),
        "payout.paid": lambda o: _handle_payout_event("payout.paid", o),
        "payout.failed": lambda o: _handle_payout_event("payout.failed", o),
        "transfer.created": _handle_transfer_created,
        "transfer.reversed": _handle_transfer_reversed,
    }

    handler = handlers.get(event_type)
    if handler:
        return handler(obj)

    logger.debug("Unhandled Stripe event type: %s", event_type)
    return {"status": "ignored", "event_type": event_type}


@app.post("/stripe/setup-webhook")
async def setup_stripe_webhook(req: StripeWebhookSetup) -> dict[str, Any]:
    """Create a Stripe webhook endpoint via the Stripe API."""
    if settings.payment_mode != "stripe" or not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")

    if _stripe_module is None:
        raise HTTPException(status_code=503, detail="Stripe library not installed")

    try:
        _stripe_module.api_key = settings.stripe_secret_key
        params: dict[str, Any] = {
            "enabled_events": [
                "charge.succeeded",
                "charge.failed",
                "charge.refunded",
                "charge.dispute.created",
                "payment_intent.succeeded",
                "payment_intent.payment_failed",
                "payout.created",
                "payout.paid",
                "payout.failed",
                "transfer.created",
                "transfer.reversed",
            ],
            "url": req.url,
        }
        if req.description:
            params["description"] = req.description
        if req.connect:
            params["connect"] = True
        endpoint = _stripe_module.WebhookEndpoint.create(**params)
        return {
            "webhook_endpoint_id": endpoint.id,
            "secret": endpoint.secret,
            "url": endpoint.url,
            "status": endpoint.status,
            "enabled_events": endpoint.enabled_events,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc}") from exc


@app.post("/payment/intent")
async def create_payment_intent(request: dict[str, Any]) -> dict[str, Any]:
    """Create a Stripe PaymentIntent for an agent that needs to collect card details."""
    if settings.payment_mode != "stripe" or not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe payments not configured")

    if _stripe_module is None:
        raise HTTPException(status_code=503, detail="Stripe library not installed")

    amount = request.get("amount_usd", 0.0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount_usd must be greater than 0")

    try:
        _stripe_module.api_key = settings.stripe_secret_key
        intent = _stripe_module.PaymentIntent.create(
            amount=int(amount * 100),
            currency="usd",
            description="SAP compute ticket",
            capture_method="manual",
            automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
        )
        return {
            "payment_intent_id": intent.id,
            "client_secret": intent.client_secret,
            "status": intent.status,
            "amount_usd": amount,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc}") from exc


@app.get("/health")
async def health() -> dict[str, Any]:
    """Probe all critical dependencies and return granular status."""
    checks: dict[str, Any] = {}
    healthy = True

    # VM manager
    checks["vm_manager"] = "ready" if vm_mgr is not None else "not_ready"
    healthy &= checks["vm_manager"] == "ready"

    # Tunnel manager
    checks["tunnel_manager"] = "ready" if tunnel_mgr is not None else "not_ready"
    healthy &= checks["tunnel_manager"] == "ready"

    # SGE engine
    checks["sge"] = "ready" if sge is not None else "not_ready"
    healthy &= checks["sge"] == "ready"

    # Payment processor
    checks["payment"] = "ready" if payment_proc is not None else "not_ready"
    healthy &= checks["payment"] == "ready"

    # Job queue worker
    checks["job_queue"] = (
        "running" if _job_queue._task and not _job_queue._task.done() else "not_running"
    )
    healthy &= checks["job_queue"] == "running"

    status = "ok" if healthy else "degraded"
    return {"status": status, "checks": checks}


@app.get("/creator/balance")
async def creator_balance() -> dict[str, Any]:
    """Current creator earnings balance."""
    return {
        "balance_usd": payment_proc.creator_balance,
        "payout_threshold_usd": settings.payout_threshold_usd,
        "eligible_for_payout": payment_proc.creator_balance >= settings.payout_threshold_usd,
    }


@app.post("/checkout/create")
async def create_checkout_session(req: CheckoutSessionRequest) -> dict[str, Any]:
    """Create a Stripe Checkout Session for human buyers (hosted payment page)."""
    if settings.payment_mode != "stripe" or not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe checkout not configured")

    if _stripe_module is None:
        raise HTTPException(status_code=503, detail="Stripe library not installed")

    try:
        decoded = base64.b64decode(req.script_b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64: {exc}") from exc

    if len(decoded) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Script exceeds 10 MiB")

    _validate_script(decoded)

    try:
        _stripe_module.api_key = settings.stripe_secret_key
        # Store a one-way hash reference instead of raw script to avoid leaking code to Stripe
        import hashlib
        script_hash = hashlib.sha256(req.script_b64.encode()).hexdigest()[:16]
        params: dict[str, Any] = {
            "mode": "payment",
            "line_items": [{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": "SAP Compute Ticket", "description": "Secure compute on surplus energy"},
                    "unit_amount": int(req.amount_usd * 100),
                },
                "quantity": 1,
            }],
            "success_url": req.success_url,
            "cancel_url": req.cancel_url,
            "metadata": {
                "script_ref": script_hash,
                "gateway": "SAP",
            },
        }
        if req.customer_email:
            params["customer_email"] = req.customer_email
        session = _stripe_module.checkout.Session.create(**params)
        return {
            "checkout_url": session.url,
            "session_id": session.id,
            "amount_usd": req.amount_usd,
            "status": session.status,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc}") from exc


@app.post("/connect/payout")
async def connect_payout(req: ConnectPayoutRequest) -> dict[str, Any]:
    """Transfer earnings to a Stripe Connect account."""
    balance = payment_proc.creator_balance
    amount = req.amount_usd if req.amount_usd is not None else balance
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than 0")
    if balance < amount:
        raise HTTPException(status_code=402, detail=f"Balance {balance} below requested amount {amount}")

    if settings.payment_mode != "stripe" or not settings.stripe_secret_key:
        # Mock mode: debit and return
        payment_proc._creator_balance -= amount
        payment_proc._save_balances()
        return {
            "payout_id": f"payout_{uuid.uuid4().hex[:12]}",
            "amount_usd": amount,
            "status": "initiated",
            "method": "mock_transfer",
            "destination": req.stripe_account_id,
        }

    if _stripe_module is None:
        raise HTTPException(status_code=503, detail="Stripe library not installed")

    try:
        _stripe_module.api_key = settings.stripe_secret_key
        transfer = _stripe_module.Transfer.create(
            amount=int(amount * 100),
            currency="usd",
            destination=req.stripe_account_id,
            description="SAP creator payout",
        )
        payment_proc._save_balances()
        return {
            "payout_id": transfer.id,
            "amount_usd": amount,
            "status": "initiated",
            "method": "stripe_connect_transfer",
            "destination": req.stripe_account_id,
            "transfer_reversed": False,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc}") from exc


@app.post("/creator/payout")
async def creator_payout() -> dict[str, Any]:
    """Request a creator payout if balance meets threshold (legacy endpoint)."""
    balance = payment_proc.creator_balance
    if balance < settings.payout_threshold_usd:
        raise HTTPException(
            status_code=402,
            detail=f"Balance {balance} below threshold {settings.payout_threshold_usd}",
        )
    payout_id = f"payout_{uuid.uuid4().hex[:12]}"
    payment_proc._save_balances()
    return {
        "payout_id": payout_id,
        "amount_usd": balance,
        "status": "initiated",
        "method": "stripe_connect" if settings.payment_mode == "stripe" else "mock_transfer",
    }

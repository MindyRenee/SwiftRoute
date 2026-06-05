"""SAP AI-native API gateway."""

import base64
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from pydantic import BaseModel, Field

from src.config import settings
from src.payments import get_processor
from src.pricing import calculate_pricing
from src.receipts import ReceiptGenerator
from src.router import find_optimal_zone
from src.sge import GridEvent, SGEEventType, SyntheticGridElasticity
from src.tunnel_manager import WireGuardTunnelManager
from src.vm_manager import MicroVMManager

logger = logging.getLogger("sap.gateway")

# Simple in-memory rate limiter: {ip: [timestamp, ...]}
_rate_limit_store: dict[str, list[float]] = {}

# Valid job IDs created by this gateway instance
_job_registry: set[str] = set()


class RateLimitMiddleware:
    """Basic in-memory rate-limiting middleware."""

    def __init__(self, app: FastAPI, max_requests: int = 60, window_seconds: int = 60) -> None:
        self.app = app
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


# Global managers (initialized in lifespan)
vm_mgr: MicroVMManager | None = None
tunnel_mgr: WireGuardTunnelManager | None = None
sge: SyntheticGridElasticity | None = None
payment_proc = get_processor()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global vm_mgr, tunnel_mgr, sge, payment_proc
    vm_mgr = MicroVMManager()
    tunnel_mgr = WireGuardTunnelManager()
    sge = SyntheticGridElasticity()
    payment_proc = get_processor()
    yield
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


def _validate_script(decoded: bytes) -> None:
    """Reject scripts containing dangerous system-level patterns."""
    try:
        text = decoded.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Script is not valid UTF-8: {exc}") from exc

    dangerous = [
        r"\bos\.system\b",
        r"\bsubprocess\b",
        r"\beval\b",
        r"\bexec\b",
        r"\bcompile\b",
        r"\b__import__\b",
        r"\bimport\s+socket\b",
        r"\bopen\s*\(",
        r"\burllib\b",
        r"\brequests\b",
    ]
    for pattern in dangerous:
        if re.search(pattern, text, re.IGNORECASE):
            raise HTTPException(status_code=400, detail=f"Script contains forbidden pattern: {pattern}")


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

    # 5. Create secure tunnel
    if tunnel_mgr is None:
        raise HTTPException(status_code=503, detail="Tunnel manager not ready")
    tunnel = tunnel_mgr.create_tunnel()

    # 6. Spin up MicroVM and execute
    if vm_mgr is None:
        await payment_proc.refund(payment.payment_id, total_charge)
        raise HTTPException(status_code=503, detail="VM manager not ready")
    vm = await vm_mgr.create_vm(req.execution_script_b64)
    _job_registry.add(vm.vm_id)
    try:
        logs = await vm_mgr.execute(vm)
    except Exception:
        await payment_proc.refund(payment.payment_id, total_charge)
        _job_registry.discard(vm.vm_id)
        raise HTTPException(status_code=500, detail="Execution failed; payment refunded")
    finally:
        await vm_mgr.destroy(vm)

    # 7. Capture payment on success
    captured = await payment_proc.capture(payment.payment_id, total_charge)

    # 8. Mint tax receipt
    receipt = ReceiptGenerator.generate(
        job_id=vm.vm_id,
        carbon_diverted_g=pricing.delta_net.carbon_g,
        carbon_remote_g=remote_state.carbon_g,
        carbon_local_g=pricing.v_local.carbon_g,
        tax_credit_usd=pricing.delta_net.tax_value_usd,
        zone_name=zone.name,
        zone_region=zone.region,
    )

    logger.info("Ticket completed: job_id=%s zone=%s amount_usd=%s", vm.vm_id, zone.name, total_charge)

    # 9. Tear down tunnel
    tunnel_mgr.tear_down(tunnel)

    return TicketResponse(
        job_id=vm.vm_id,
        status="completed",
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
        receipt=json.loads(ReceiptGenerator.to_json(receipt)),
        vm_logs=logs,
        payment={
            "payment_id": captured.payment_id,
            "status": captured.status,
            "amount_usd": captured.amount_usd,
            "fee_usd": captured.fee_usd,
            "net_usd": captured.net_usd,
        },
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
    result = await sge.process_event(event, webhook.job_id)
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

    import stripe

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]
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
        "payment_intent.succeeded": lambda o: _handle_charge_succeeded(o),
        "payment_intent.payment_failed": lambda o: _handle_charge_failed(o),
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

    try:
        import stripe
        stripe.api_key = settings.stripe_secret_key
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
        endpoint = stripe.WebhookEndpoint.create(**params)
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

    amount = request.get("amount_usd", 0.0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount_usd must be greater than 0")

    try:
        import stripe
        stripe.api_key = settings.stripe_secret_key
        intent = stripe.PaymentIntent.create(
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
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/creator/balance")
async def creator_balance() -> dict[str, Any]:
    """Current creator earnings balance."""
    return {
        "balance_usd": payment_proc.creator_balance,
        "payout_threshold_usd": settings.payout_threshold_usd,
        "eligible_for_payout": payment_proc.creator_balance >= settings.payout_threshold_usd,
    }


@app.post("/creator/payout")
async def creator_payout() -> dict[str, Any]:
    """Request a creator payout if balance meets threshold."""
    balance = payment_proc.creator_balance
    if balance < settings.payout_threshold_usd:
        raise HTTPException(
            status_code=402,
            detail=f"Balance {balance} below threshold {settings.payout_threshold_usd}",
        )
    # In production this would trigger a Stripe Connect transfer.
    # Here we debit the balance and return a payout receipt.
    payout_id = f"payout_{uuid.uuid4().hex[:12]}"
    if hasattr(payment_proc, "_creator_balance"):
        payment_proc._creator_balance = 0.0
    return {
        "payout_id": payout_id,
        "amount_usd": balance,
        "status": "initiated",
        "method": "stripe_connect" if settings.payment_mode == "stripe" else "mock_transfer",
    }

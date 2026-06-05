"""Payment processing for agent tickets and creator payouts."""

import uuid
from dataclasses import dataclass
from typing import Protocol

from src.config import settings


@dataclass(frozen=True)
class PaymentResult:
    """Result of a payment authorization or capture."""

    payment_id: str
    status: str  # "authorized" | "captured" | "failed" | "refunded"
    amount_usd: float
    fee_usd: float
    net_usd: float
    receipt_url: str = ""


class PaymentProcessor(Protocol):
    """Abstract payment processor."""

    @property
    def creator_balance(self) -> float: ...

    async def authorize(
        self, amount_usd: float, description: str, payment_method_id: str = ""
    ) -> PaymentResult: ...

    async def capture(self, payment_id: str, amount_usd: float) -> PaymentResult: ...

    async def refund(self, payment_id: str, amount_usd: float) -> PaymentResult: ...


class MockPaymentProcessor:
    """In-memory payment processor for development and testing."""

    def __init__(self) -> None:
        self._ledger: dict[str, PaymentResult] = {}
        self._creator_balance: float = 0.0
        self._node_payout_usd: float = 0.0

    @property
    def creator_balance(self) -> float:
        return round(self._creator_balance, 2)

    async def authorize(
        self, amount_usd: float, description: str, payment_method_id: str = ""
    ) -> PaymentResult:
        pid = f"mock_pay_{uuid.uuid4().hex[:12]}"
        fee = round(amount_usd * settings.transaction_fee_rate, 2)
        net = round(amount_usd - fee, 2)
        result = PaymentResult(
            payment_id=pid,
            status="authorized",
            amount_usd=amount_usd,
            fee_usd=fee,
            net_usd=net,
        )
        self._ledger[pid] = result
        return result

    async def capture(self, payment_id: str, amount_usd: float) -> PaymentResult:
        auth = self._ledger.get(payment_id)
        if not auth:
            return PaymentResult(
                payment_id=payment_id,
                status="failed",
                amount_usd=amount_usd,
                fee_usd=0.0,
                net_usd=0.0,
            )
        fee = round(amount_usd * settings.transaction_fee_rate, 2)
        net = round(amount_usd - fee, 2)
        captured = PaymentResult(
            payment_id=payment_id,
            status="captured",
            amount_usd=amount_usd,
            fee_usd=fee,
            net_usd=net,
        )
        self._ledger[payment_id] = captured
        self._creator_balance += fee + settings.signature_mint_fee_usd
        self._node_payout_usd += net
        return captured

    async def refund(self, payment_id: str, amount_usd: float) -> PaymentResult:
        auth = self._ledger.get(payment_id)
        if not auth:
            return PaymentResult(
                payment_id=payment_id,
                status="failed",
                amount_usd=amount_usd,
                fee_usd=0.0,
                net_usd=0.0,
            )
        # Revert only the creator fee that was actually earned on this payment
        fee = round(amount_usd * settings.transaction_fee_rate, 2)
        earned = fee + settings.signature_mint_fee_usd
        self._creator_balance = max(0.0, self._creator_balance - earned)
        self._node_payout_usd = max(0.0, self._node_payout_usd - (amount_usd - fee))
        result = PaymentResult(
            payment_id=payment_id,
            status="refunded",
            amount_usd=amount_usd,
            fee_usd=0.0,
            net_usd=amount_usd,
        )
        self._ledger[payment_id] = result
        return result


class StripePaymentProcessor:
    """Production Stripe payment processor."""

    def __init__(self, secret_key: str = settings.stripe_secret_key) -> None:
        self.secret_key = secret_key
        self._creator_balance: float = 0.0
        self._node_payout_usd: float = 0.0

    @property
    def creator_balance(self) -> float:
        return round(self._creator_balance, 2)

    async def authorize(
        self, amount_usd: float, description: str, payment_method_id: str = ""
    ) -> PaymentResult:
        try:
            import stripe
            from typing import Any
            stripe.api_key = self.secret_key
            params: dict[str, Any] = {
                "amount": int(amount_usd * 100),
                "currency": "usd",
                "description": description,
                "capture_method": "manual",
                "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"},
            }
            if payment_method_id:
                params["payment_method"] = payment_method_id
                params["confirm"] = True
            intent = stripe.PaymentIntent.create(**params)
            status = "authorized" if intent.status in ("requires_capture", "succeeded") else "failed"
            return PaymentResult(
                payment_id=intent.id,
                status=status,
                amount_usd=amount_usd,
                fee_usd=0.0,
                net_usd=0.0,
            )
        except Exception:
            return PaymentResult(
                payment_id="",
                status="failed",
                amount_usd=amount_usd,
                fee_usd=0.0,
                net_usd=0.0,
            )

    async def capture(self, payment_id: str, amount_usd: float) -> PaymentResult:
        try:
            import stripe
            stripe.api_key = self.secret_key
            stripe.PaymentIntent.capture(payment_id)
            fee = round(amount_usd * settings.transaction_fee_rate, 2)
            self._creator_balance += fee + settings.signature_mint_fee_usd
            self._node_payout_usd += amount_usd - fee
            return PaymentResult(
                payment_id=payment_id,
                status="captured",
                amount_usd=amount_usd,
                fee_usd=fee,
                net_usd=round(amount_usd - fee, 2),
            )
        except Exception:
            return PaymentResult(
                payment_id=payment_id,
                status="failed",
                amount_usd=amount_usd,
                fee_usd=0.0,
                net_usd=0.0,
            )

    async def refund(self, payment_id: str, amount_usd: float) -> PaymentResult:
        try:
            import stripe
            stripe.api_key = self.secret_key
            stripe.Refund.create(payment_intent=payment_id, amount=int(amount_usd * 100))
            fee = round(amount_usd * settings.transaction_fee_rate, 2)
            earned = fee + settings.signature_mint_fee_usd
            self._creator_balance = max(0.0, self._creator_balance - earned)
            self._node_payout_usd = max(0.0, self._node_payout_usd - (amount_usd - fee))
            return PaymentResult(
                payment_id=payment_id,
                status="refunded",
                amount_usd=amount_usd,
                fee_usd=0.0,
                net_usd=amount_usd,
            )
        except Exception:
            return PaymentResult(
                payment_id=payment_id,
                status="failed",
                amount_usd=amount_usd,
                fee_usd=0.0,
                net_usd=0.0,
            )


def get_processor() -> PaymentProcessor:
    if settings.payment_mode == "stripe" and settings.stripe_secret_key:
        return StripePaymentProcessor()
    return MockPaymentProcessor()

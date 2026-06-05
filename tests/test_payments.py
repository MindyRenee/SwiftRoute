"""Tests for the payment processor."""

import pytest

from src.config import settings
from src.payments import MockPaymentProcessor


class TestMockPaymentProcessor:
    async def test_authorize(self) -> None:
        proc = MockPaymentProcessor()
        result = await proc.authorize(5.0, "Test ticket")
        assert result.status == "authorized"
        assert result.amount_usd == 5.0
        assert result.fee_usd > 0
        assert result.payment_id.startswith("mock_pay_")

    async def test_authorize_with_payment_method_id(self) -> None:
        proc = MockPaymentProcessor()
        result = await proc.authorize(5.0, "Test", payment_method_id="pm_test_123")
        assert result.status == "authorized"
        assert result.payment_id.startswith("mock_pay_")

    async def test_capture_adds_to_creator_balance(self) -> None:
        proc = MockPaymentProcessor()
        auth = await proc.authorize(5.0, "Test")
        assert proc.creator_balance == 0.0
        captured = await proc.capture(auth.payment_id, 5.0)
        assert captured.status == "captured"
        assert proc.creator_balance > 0

    async def test_refund_deducts_balance(self) -> None:
        proc = MockPaymentProcessor()
        auth = await proc.authorize(5.0, "Test")
        await proc.capture(auth.payment_id, 5.0)
        balance_before = proc.creator_balance
        await proc.refund(auth.payment_id, 5.0)
        assert proc.creator_balance < balance_before

    async def test_capture_unknown_payment_fails(self) -> None:
        proc = MockPaymentProcessor()
        result = await proc.capture("bad_id", 5.0)
        assert result.status == "failed"

    async def test_multiple_captures_accumulate_balance(self) -> None:
        proc = MockPaymentProcessor()
        for _ in range(3):
            auth = await proc.authorize(5.0, "Test")
            await proc.capture(auth.payment_id, 5.0)
        assert proc.creator_balance > 0.0

    async def test_refund_reverts_only_earned_fee(self) -> None:
        proc = MockPaymentProcessor()
        auth = await proc.authorize(5.0, "Test")
        await proc.capture(auth.payment_id, 5.0)
        balance_before = proc.creator_balance
        node_before = proc._node_payout_usd
        await proc.refund(auth.payment_id, 5.0)
        fee = round(5.0 * settings.transaction_fee_rate, 2)
        earned = fee + settings.signature_mint_fee_usd
        assert proc.creator_balance == pytest.approx(balance_before - earned, abs=0.001)
        assert proc._node_payout_usd == pytest.approx(node_before - (5.0 - fee), abs=0.001)

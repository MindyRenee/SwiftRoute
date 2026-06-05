"""Tests for the deterministic pricing engine."""

import pytest

from src.pricing import PricingResult, StateVector, calculate_fees, calculate_pricing
from src.config import settings


class TestStateVector:
    def test_subtraction(self) -> None:
        a = StateVector(10.0, 100.0, 5.0)
        b = StateVector(3.0, 40.0, 2.0)
        result = a - b
        assert result.compute_cost_usd == 7.0
        assert result.carbon_g == 60.0
        assert result.tax_value_usd == 3.0

    def test_total_optimized_value(self) -> None:
        sv = StateVector(3.0, 100.0, 0.5)
        assert sv.total_optimized_value == 3.5

    def test_subtraction_rounding(self) -> None:
        a = StateVector(3.50, 4500.0, 0.0)
        b = StateVector(0.25, 80.0, 0.15)
        c = StateVector(0.01, 0.0, -0.05)
        result = a - b - c
        assert result.compute_cost_usd == 3.24
        assert result.carbon_g == 4420.0
        assert result.tax_value_usd == -0.1


class TestCalculateFees:
    def test_transaction_fee_and_signature(self) -> None:
        fees = calculate_fees()
        expected_fee = round(settings.local_compute_cost_usd * settings.transaction_fee_rate, 2)
        assert fees.compute_cost_usd == pytest.approx(expected_fee, abs=0.001)
        assert fees.carbon_g == 0.0
        assert fees.tax_value_usd == -settings.signature_mint_fee_usd


class TestCalculatePricing:
    def test_profitable_job(self) -> None:
        remote = StateVector(0.40, 350.0, 0.21)
        result = calculate_pricing(remote)
        assert isinstance(result, PricingResult)
        assert result.is_profitable is True
        assert result.creator_yield_usd > 0
        assert result.v_local.compute_cost_usd == settings.local_compute_cost_usd

    def test_delta_net_components(self) -> None:
        remote = StateVector(0.25, 80.0, 0.15)
        result = calculate_pricing(remote)
        # delta = local - remote - fees
        expected_delta_cost = round(
            settings.local_compute_cost_usd - remote.compute_cost_usd - result.v_fees.compute_cost_usd, 2
        )
        assert result.delta_net.compute_cost_usd == expected_delta_cost

    def test_unprofitable_when_remote_exceeds_local(self) -> None:
        remote = StateVector(10.0, 100.0, 0.0)
        result = calculate_pricing(remote)
        assert result.is_profitable is False

    def test_creator_yield_formula(self) -> None:
        remote = StateVector(0.40, 350.0, 0.21)
        result = calculate_pricing(remote)
        expected = round(result.v_fees.compute_cost_usd + abs(result.v_fees.tax_value_usd), 2)
        assert result.creator_yield_usd == expected

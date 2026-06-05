"""Deterministic pricing and delta math engine."""

from dataclasses import dataclass

from src.config import settings


@dataclass(frozen=True)
class StateVector:
    """3-dimensional job state vector: [compute_cost, carbon, tax_value]."""

    compute_cost_usd: float
    carbon_g: float
    tax_value_usd: float

    def __sub__(self, other: "StateVector") -> "StateVector":
        return StateVector(
            compute_cost_usd=round(self.compute_cost_usd - other.compute_cost_usd, 2),
            carbon_g=round(self.carbon_g - other.carbon_g, 1),
            tax_value_usd=round(self.tax_value_usd - other.tax_value_usd, 2),
        )

    @property
    def total_optimized_value(self) -> float:
        """Net optimized business value for the client."""
        return self.compute_cost_usd + self.tax_value_usd


@dataclass(frozen=True)
class PricingResult:
    """Complete pricing breakdown for a job."""

    v_local: StateVector
    v_remote: StateVector
    v_fees: StateVector
    delta_net: StateVector
    creator_yield_usd: float
    client_net_value_usd: float
    is_profitable: bool


def calculate_fees(compute_cost: float = settings.local_compute_cost_usd) -> StateVector:
    """Calculate the fixed protocol fee state on the local baseline cost."""
    transaction_fee = compute_cost * settings.transaction_fee_rate
    return StateVector(
        compute_cost_usd=round(transaction_fee, 2),
        carbon_g=0.0,
        tax_value_usd=-settings.signature_mint_fee_usd,
    )


def calculate_pricing(remote_state: StateVector) -> PricingResult:
    """Execute the rapid algebraic subtraction loop (Delta_net)."""
    v_local = StateVector(
        compute_cost_usd=settings.local_compute_cost_usd,
        carbon_g=settings.local_carbon_g,
        tax_value_usd=settings.local_tax_value_usd,
    )
    v_fees = calculate_fees()
    delta_net = v_local - remote_state - v_fees

    # Creator yield is the transaction fee + signature mint fee
    creator_yield_usd = round(v_fees.compute_cost_usd + abs(v_fees.tax_value_usd), 2)
    client_net_value = round(delta_net.total_optimized_value, 2)
    is_profitable = delta_net.compute_cost_usd > 0 and creator_yield_usd > 0

    return PricingResult(
        v_local=v_local,
        v_remote=remote_state,
        v_fees=v_fees,
        delta_net=delta_net,
        creator_yield_usd=creator_yield_usd,
        client_net_value_usd=client_net_value,
        is_profitable=is_profitable,
    )

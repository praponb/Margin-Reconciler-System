"""Reverse margin calculator (spec §4.4).

    effective_fee_rate = (commission% + transaction% + service%) / 100 * (1 + VAT)
    denominator        = 1 - effective_fee_rate - target_margin% / 100
    recommended_price  = (unit COGS + unit packaging + unit shipping buffer) / denominator

VAT (default 7%, Thailand) is charged on the platform fees, so it scales the fee rate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

DEFAULT_VAT_RATE = 0.07


@dataclass(frozen=True)
class PriceBreakdown:
    recommended_price: float
    effective_fee_rate: float
    platform_fees: float
    vat_on_fees: float
    unit_cost: float
    net_profit: float
    net_margin_pct: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def _validate(**values: float) -> None:
    for name, value in values.items():
        if value is None or value != value:  # None or NaN
            raise ValueError(f"{name} is required.")
        if value < 0:
            raise ValueError(f"{name} must not be negative (got {value}).")


def calculate_recommended_price(
    cogs: float,
    packaging: float,
    shipping_buffer: float,
    commission_pct: float,
    transaction_pct: float,
    service_pct: float,
    target_margin_pct: float,
    vat_rate: float = DEFAULT_VAT_RATE,
) -> float:
    """Calculate the listing price that achieves the target margin after fees and VAT."""
    _validate(
        cogs=cogs,
        packaging=packaging,
        shipping_buffer=shipping_buffer,
        commission_pct=commission_pct,
        transaction_pct=transaction_pct,
        service_pct=service_pct,
        vat_rate=vat_rate,
    )
    if target_margin_pct is None or target_margin_pct != target_margin_pct:
        raise ValueError("target_margin_pct is required.")
    effective_fee_rate = ((commission_pct + transaction_pct + service_pct) / 100.0) * (1.0 + vat_rate)
    denominator = 1.0 - effective_fee_rate - (target_margin_pct / 100.0)
    if denominator <= 0:
        raise ValueError("Combined fees and target margin exceed 100% of unit price.")
    numerator = cogs + packaging + shipping_buffer
    return round(numerator / denominator, 4)


def price_breakdown(
    cogs: float,
    packaging: float,
    shipping_buffer: float,
    commission_pct: float,
    transaction_pct: float,
    service_pct: float,
    target_margin_pct: float,
    vat_rate: float = DEFAULT_VAT_RATE,
) -> PriceBreakdown:
    """Recommended price plus where each baht of it goes."""
    price = calculate_recommended_price(
        cogs,
        packaging,
        shipping_buffer,
        commission_pct,
        transaction_pct,
        service_pct,
        target_margin_pct,
        vat_rate,
    )
    fee_rate = (commission_pct + transaction_pct + service_pct) / 100.0
    platform_fees = price * fee_rate
    vat_on_fees = platform_fees * vat_rate
    unit_cost = cogs + packaging + shipping_buffer
    net_profit = price - platform_fees - vat_on_fees - unit_cost
    return PriceBreakdown(
        recommended_price=price,
        effective_fee_rate=round(fee_rate * (1.0 + vat_rate), 6),
        platform_fees=round(platform_fees, 4),
        vat_on_fees=round(vat_on_fees, 4),
        unit_cost=round(unit_cost, 4),
        net_profit=round(net_profit, 4),
        net_margin_pct=round(net_profit / price * 100.0, 2) if price else 0.0,
    )

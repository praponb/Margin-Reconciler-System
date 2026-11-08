import math

import pytest

from reconciler_app.core.pricing import calculate_recommended_price, price_breakdown


def test_golden_recommended_price():
    # (100 + 5 + 10) / (1 - 0.10 * 1.07 - 0.20) = 115 / 0.693
    assert calculate_recommended_price(100, 5, 10, 5, 2, 3, 20) == 165.9452


def test_zero_fees_zero_margin_is_cost():
    assert calculate_recommended_price(50, 0, 0, 0, 0, 0, 0) == 50.0


def test_custom_vat_rate():
    assert calculate_recommended_price(100, 0, 0, 10, 0, 0, 0, vat_rate=0.0) == pytest.approx(111.1111)


@pytest.mark.parametrize("target", [100, 95, 89.3])
def test_denominator_not_positive_raises(target):
    with pytest.raises(ValueError, match="exceed 100%"):
        calculate_recommended_price(100, 5, 10, 5, 2, 3, target)


@pytest.mark.parametrize("field", ["cogs", "packaging", "shipping_buffer", "commission_pct"])
def test_negative_inputs_rejected(field):
    kwargs = dict(
        cogs=100,
        packaging=5,
        shipping_buffer=10,
        commission_pct=5,
        transaction_pct=2,
        service_pct=3,
        target_margin_pct=20,
    )
    kwargs[field] = -1
    with pytest.raises(ValueError, match="must not be negative"):
        calculate_recommended_price(**kwargs)


def test_nan_rejected():
    with pytest.raises(ValueError):
        calculate_recommended_price(math.nan, 0, 0, 0, 0, 0, 10)


def test_breakdown_achieves_target_margin():
    b = price_breakdown(100, 5, 10, 5, 2, 3, 20)
    assert b.recommended_price == 165.9452
    assert b.net_margin_pct == pytest.approx(20.0, abs=0.01)
    assert b.platform_fees + b.vat_on_fees + b.unit_cost + b.net_profit == pytest.approx(
        b.recommended_price, abs=1e-3
    )


def test_negative_target_margin_allowed_for_loss_leaders():
    assert calculate_recommended_price(100, 0, 0, 0, 0, 0, -10) == pytest.approx(90.9091)

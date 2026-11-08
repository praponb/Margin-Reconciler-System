from datetime import UTC, date, datetime

import polars as pl
import pytest

from reconciler_app.core.engine import MART_COLUMNS, reconcile_orders

M = "merchant-1"
T0 = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)


def _orders(*rows):
    return pl.DataFrame(
        [
            {"merchant_id": M, "channel": ch, "channel_order_id": oid, "order_date": T0, "order_status": st}
            for ch, oid, st in rows
        ]
    )


def _items(*rows):
    return pl.DataFrame(
        [
            {
                "merchant_id": M,
                "channel": ch,
                "channel_order_id": oid,
                "channel_sku": sku,
                "quantity": qty,
                "unit_original_price": price,
                "seller_discount": 0.0,
                "subtotal": subtotal,
            }
            for ch, oid, sku, qty, price, subtotal in rows
        ]
    )


def _settlement(ch, oid, *, commission=0.0, transaction=0.0, service=0.0, buyer_ship=0.0, actual_ship=0.0):
    return {
        "merchant_id": M,
        "channel": ch,
        "channel_order_id": oid,
        "settlement_date": date(2026, 11, 5),
        "commission_fee": commission,
        "transaction_fee": transaction,
        "service_fee": service,
        "shipping_fee_paid_by_buyer": buyer_ship,
        "actual_shipping_fee_charged": actual_ship,
        "voucher_platform_subsidy": 0.0,
        "net_payout_amount": 0.0,
    }


PRODUCTS = pl.DataFrame(
    [
        {"merchant_id": M, "internal_sku": "A", "cogs_amount": 100.0, "packaging_cost": 5.0},
        {"merchant_id": M, "internal_sku": "B", "cogs_amount": 40.0, "packaging_cost": 2.0},
        {"merchant_id": M, "internal_sku": "C", "cogs_amount": 10.0, "packaging_cost": 0.0},
    ]
)


def _by_sku(df):
    return {r["internal_sku"]: r for r in df.iter_rows(named=True)}


def test_value_weighted_allocation_and_shipping_gap_golden():
    orders = _orders(("SHOPEE", "O1", "Completed"))
    items = _items(("SHOPEE", "O1", "A", 2, 150.0, 300.0), ("SHOPEE", "O1", "B", 1, 100.0, 100.0))
    sets = pl.DataFrame([_settlement("SHOPEE", "O1", commission=20, buyer_ship=30, actual_ship=50)])
    out = _by_sku(reconcile_orders(orders, items, sets, PRODUCTS))
    assert out["A"]["allocated_commission"] == 15.0
    assert out["B"]["allocated_commission"] == 5.0
    assert out["A"]["allocated_shipping_gap"] == 15.0
    assert out["B"]["allocated_shipping_gap"] == 5.0
    # 300 - 200 cogs - 10 packaging - 15 commission - 15 gap
    assert out["A"]["net_profit"] == 60.0
    assert out["A"]["net_margin_pct"] == 20.0
    assert out["A"]["reconcile_status"] == "SHIPPING_OVERCHARGE"


def test_negative_shipping_gap_is_floored_at_zero():
    orders = _orders(("SHOPEE", "O1", "Completed"))
    items = _items(("SHOPEE", "O1", "C", 1, 100.0, 100.0))
    sets = pl.DataFrame([_settlement("SHOPEE", "O1", buyer_ship=50, actual_ship=30)])
    row = reconcile_orders(orders, items, sets, PRODUCTS).row(0, named=True)
    assert row["allocated_shipping_gap"] == 0.0
    assert row["reconcile_status"] == "HEALTHY"


def test_zero_subtotal_order_uses_quantity_weights():
    orders = _orders(("TIKTOK", "O1", "Completed"))
    items = _items(("TIKTOK", "O1", "A", 2, 0.0, 0.0), ("TIKTOK", "O1", "B", 1, 0.0, 0.0))
    sets = pl.DataFrame([_settlement("TIKTOK", "O1", actual_ship=45)])
    out = _by_sku(reconcile_orders(orders, items, sets, PRODUCTS))
    assert out["A"]["allocated_shipping_gap"] == 30.0
    assert out["B"]["allocated_shipping_gap"] == 15.0
    assert out["A"]["net_margin_pct"] == 0.0


def test_rounding_residual_keeps_order_total_exact():
    orders = _orders(("LAZADA", "O1", "delivered"))
    items = _items(
        ("LAZADA", "O1", "A", 1, 10.0, 10.0),
        ("LAZADA", "O1", "B", 1, 10.0, 10.0),
        ("LAZADA", "O1", "C", 1, 10.0, 10.0),
    )
    sets = pl.DataFrame([_settlement("LAZADA", "O1", commission=10.0, service=0.01)])
    out = reconcile_orders(orders, items, sets, PRODUCTS)
    assert out["allocated_commission"].sum() == pytest.approx(10.0, abs=1e-9)
    assert out["allocated_service_fee"].sum() == pytest.approx(0.01, abs=1e-9)
    assert sorted(out["allocated_commission"].to_list()) == [3.3333, 3.3333, 3.3334]


def test_status_precedence():
    orders = _orders(
        ("SHOPEE", "UNMAPPED", "Completed"),
        ("SHOPEE", "PENDING", "Completed"),
        ("SHOPEE", "LOSS", "Completed"),
        ("SHOPEE", "OK", "Completed"),
    )
    items = _items(
        ("SHOPEE", "UNMAPPED", "ZZZ", 1, 10.0, 10.0),
        ("SHOPEE", "PENDING", "A", 1, 10.0, 10.0),  # would be a loss, but not yet settled
        ("SHOPEE", "LOSS", "A", 1, 10.0, 10.0),
        ("SHOPEE", "OK", "C", 1, 100.0, 100.0),
    )
    sets = pl.DataFrame(
        [_settlement("SHOPEE", o, actual_ship=5) for o in ("UNMAPPED", "LOSS")]
        + [_settlement("SHOPEE", "OK")]
    )
    out = reconcile_orders(orders, items, sets, PRODUCTS)
    status = dict(zip(out["channel_order_id"], out["reconcile_status"], strict=True))
    assert status == {
        "UNMAPPED": "UNMAPPED_SKU",
        "PENDING": "PENDING_PAYOUT",
        "LOSS": "LOSS_MAKING",
        "OK": "HEALTHY",
    }
    unmapped = out.filter(pl.col("channel_order_id") == "UNMAPPED").row(0, named=True)
    assert unmapped["internal_sku"] == "ZZZ"


def test_cancelled_orders_are_excluded():
    orders = _orders(("SHOPEE", "X", "ยกเลิกแล้ว"), ("TIKTOK", "Y", "Cancelled"), ("TIKTOK", "Z", "Completed"))
    items = _items(
        ("SHOPEE", "X", "A", 1, 10.0, 10.0),
        ("TIKTOK", "Y", "A", 1, 10.0, 10.0),
        ("TIKTOK", "Z", "A", 1, 10.0, 10.0),
    )
    out = reconcile_orders(orders, items, None, PRODUCTS)
    assert out["channel_order_id"].to_list() == ["Z"]


def test_same_order_id_on_different_channels_does_not_collide():
    orders = _orders(("SHOPEE", "123", "Completed"), ("TIKTOK", "123", "Completed"))
    items = _items(("SHOPEE", "123", "C", 1, 100.0, 100.0), ("TIKTOK", "123", "C", 1, 100.0, 100.0))
    sets = pl.DataFrame([_settlement("SHOPEE", "123", commission=10.0)])
    out = reconcile_orders(orders, items, sets, PRODUCTS)
    by_channel = {r["channel"]: r for r in out.iter_rows(named=True)}
    assert out.height == 2
    assert by_channel["SHOPEE"]["allocated_commission"] == 10.0
    assert by_channel["TIKTOK"]["allocated_commission"] == 0.0
    assert by_channel["TIKTOK"]["reconcile_status"] == "PENDING_PAYOUT"


def test_multiple_settlement_slices_are_summed_not_multiplied():
    orders = _orders(("SHOPEE", "O1", "Completed"))
    items = _items(("SHOPEE", "O1", "C", 1, 100.0, 100.0))
    first = _settlement("SHOPEE", "O1", commission=8.0)
    adjustment = {**_settlement("SHOPEE", "O1", commission=2.0), "settlement_date": date(2026, 11, 9)}
    out = reconcile_orders(orders, items, pl.DataFrame([first, adjustment]), PRODUCTS)
    assert out.height == 1
    row = out.row(0, named=True)
    assert row["allocated_commission"] == 10.0
    assert row["settlement_date"] == date(2026, 11, 9)


def test_sku_mapping_table_is_used():
    orders = _orders(("LAZADA", "O1", "delivered"))
    items = _items(("LAZADA", "O1", "LZ-A", 1, 200.0, 200.0))
    mappings = pl.DataFrame(
        [{"merchant_id": M, "channel": "LAZADA", "channel_sku": "LZ-A", "internal_sku": "A"}]
    )
    row = reconcile_orders(orders, items, None, PRODUCTS, mappings).row(0, named=True)
    assert row["internal_sku"] == "A"
    assert row["total_cogs"] == 100.0


def test_no_nulls_or_nans_in_financial_output():
    orders = _orders(("SHOPEE", "O1", "Completed"))
    items = _items(("SHOPEE", "O1", "A", 1, 10.0, None), ("SHOPEE", "O1", "ZZZ", 1, 10.0, 10.0))
    out = reconcile_orders(orders, items, None, PRODUCTS)
    numeric = out.select(pl.col(pl.Float64))
    assert numeric.null_count().sum_horizontal()[0] == 0
    assert not any(numeric.select(pl.all().is_nan().any()).row(0))
    assert set(MART_COLUMNS) <= set(out.columns)


def test_empty_inputs_return_empty_frame():
    out = reconcile_orders(pl.DataFrame(), pl.DataFrame(), None, None)
    assert out.height == 0
    assert set(MART_COLUMNS) <= set(out.columns)

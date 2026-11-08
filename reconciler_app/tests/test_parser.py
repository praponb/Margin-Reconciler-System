from datetime import date, datetime
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from reconciler_app.core import normalize as nz
from reconciler_app.core.canonical_mapping import MappingError
from reconciler_app.core.parser import parse, parse_simple_table
from reconciler_app.core.sniffer import UnknownReportError

BKK = ZoneInfo("Asia/Bangkok")


def test_thai_and_english_headers_map_to_same_schema(fixture_bytes):
    th = parse(fixture_bytes("shopee_orders_th.csv"), "shopee_orders_th.csv")
    en_text = (
        "Order ID,Order Status,Order Creation Date,SKU Reference No.,Original Price,Seller Rebate,Quantity,"
        "Grand Total\n2611010001,Completed,2026-11-01 10:15,SP-TS-01,150,0,2,430\n"
    )
    en = parse(en_text.encode(), "en.csv")
    assert th.items.columns == en.items.columns
    assert th.orders.columns == en.orders.columns
    assert th.items.row(0) == en.items.row(0)


def test_buddhist_era_dates_are_converted(fixture_bytes):
    batch = parse(fixture_bytes("shopee_orders_th.csv"))
    first = batch.orders.filter(pl.col("channel_order_id") == "2611010001")["order_date"][0]
    assert first == datetime(2026, 11, 1, 10, 15, tzinfo=BKK)


def test_unknown_columns_are_dropped(fixture_bytes):
    batch = parse(fixture_bytes("shopee_settlement_en.csv"))
    assert "New Platform Column 2026" in batch.dropped_columns
    assert "New Platform Column 2026" not in batch.settlements.columns


def test_fees_are_positive_costs(fixture_bytes):
    s = parse(fixture_bytes("shopee_settlement_en.csv")).settlements.row(0, named=True)
    assert s["commission_fee"] == 20.0
    assert s["actual_shipping_fee_charged"] == 50.0
    assert s["net_payout_amount"] == 340.0


def test_lazada_unit_rows_are_summed_per_sku(fixture_bytes):
    items = parse(fixture_bytes("lazada_orders_en.xlsx"), "lazada_orders_en.xlsx").items
    row = items.filter(pl.col("channel_sku") == "LZ-TS-RED").row(0, named=True)
    assert row["quantity"] == 2
    assert row["subtotal"] == 378.0
    assert row["seller_discount"] == 20.0


def test_lazada_long_settlement_is_pivoted(fixture_bytes):
    s = parse(fixture_bytes("lazada_settlement_th_cp874.csv")).settlements
    row = s.filter(pl.col("channel_order_id") == "3001").row(0, named=True)
    assert row["settlement_date"] == date(2026, 11, 5)
    assert row["commission_fee"] == pytest.approx(38.16)
    assert row["transaction_fee"] == pytest.approx(10.0)
    assert row["service_fee"] == pytest.approx(5.0)  # unknown negative fee line
    assert row["shipping_fee_paid_by_buyer"] == pytest.approx(40.0)
    assert row["actual_shipping_fee_charged"] == pytest.approx(40.0)
    assert row["net_payout_amount"] == pytest.approx(423.84)


def test_tiktok_description_row_is_skipped(fixture_bytes):
    batch = parse(fixture_bytes("tiktok_orders_en.csv"))
    assert batch.skipped_rows == 1
    assert set(batch.orders["channel_order_id"]) == {"5701", "5702", "5703"}


def test_missing_required_column_raises():
    data = b"Order ID,Payout Completed Date,Transaction Fee,Service Fee\n1,2026-11-01,1,1\n"
    with pytest.raises(MappingError, match="commission_fee"):
        parse(data)


def test_unknown_file_raises():
    with pytest.raises(UnknownReportError):
        parse(b"a,b,c\n1,2,3\n")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("฿1,234.50", 1234.5),
        ("(12.00)", -12.0),
        ("-", 0.0),
        ("", 0.0),
        (None, 0.0),
        ("THB 5", 5.0),
        ("-3.5", -3.5),
    ],
)
def test_money_cleaning(raw, expected):
    out = pl.DataFrame({"v": [raw]}, schema={"v": pl.Utf8}).select(nz.money("v"))
    assert out["v"][0] == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-11-03 19:45:00", datetime(2026, 11, 3, 19, 45)),
        ("03/11/2569 19:45", datetime(2026, 11, 3, 19, 45)),
        ("03 Nov 2026 19:45", datetime(2026, 11, 3, 19, 45)),
        ("2026-11-03", datetime(2026, 11, 3)),
    ],
)
def test_datetime_formats(raw, expected):
    out = pl.DataFrame({"d": [raw]}).select(nz.parse_datetime("d"))
    assert out["d"][0] == expected.replace(tzinfo=BKK)


def test_product_master_upload(fixture_bytes):
    df = parse_simple_table(fixture_bytes("products.csv"), "PRODUCT_MASTER")
    assert df.columns == ["internal_sku", "product_name", "cogs_amount", "packaging_cost"]
    assert df.height == 4

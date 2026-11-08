import pytest

from reconciler_app.core.sniffer import UNKNOWN, decode_bytes, sniff


@pytest.mark.parametrize(
    "name, report_type, encoding, header_row",
    [
        ("shopee_orders_th.csv", "SHOPEE_ORDER", "utf-8-sig", 3),
        ("shopee_settlement_en.csv", "SHOPEE_SETTLEMENT", "utf-8", 2),
        ("lazada_orders_en.xlsx", "LAZADA_ORDER", None, 0),
        ("lazada_settlement_th_cp874.csv", "LAZADA_SETTLEMENT", "cp874", 1),
        ("tiktok_orders_en.csv", "TIKTOK_ORDER", "utf-8-sig", 0),
        ("tiktok_settlement_th.xlsx", "TIKTOK_SETTLEMENT", None, 1),
    ],
)
def test_detects_platform_report_and_header(fixture_bytes, name, report_type, encoding, header_row):
    result = sniff(fixture_bytes(name), name)
    assert result.report_type == report_type
    assert result.encoding == encoding
    assert result.header_row == header_row


@pytest.mark.parametrize("metadata_lines", [0, 1, 5, 10])
def test_skips_arbitrary_metadata_block(metadata_lines):
    meta = "".join(f"Report line {i},,\n" for i in range(metadata_lines))
    body = (
        "Order ID,Payout Completed Date,Commission Fee,Transaction Fee,Service Fee\n1,2026-11-01,-1,-1,-1\n"
    )
    result = sniff((meta + body).encode("utf-8"))
    assert result.report_type == "SHOPEE_SETTLEMENT"
    assert result.header_row == metadata_lines


def test_header_beyond_sniff_window_is_unknown():
    meta = "meta\n" * 15
    body = "Order ID,Payout Completed Date,Commission Fee,Transaction Fee\n"
    assert sniff((meta + body).encode()).report_type == UNKNOWN


def test_shared_tokens_alone_do_not_decide_platform():
    # "Order ID" and "Order Status" appear in both Shopee and TikTok order signatures.
    result = sniff(b"Order ID,Order Status,Quantity,Something\n1,ok,1,x\n")
    assert result.report_type == UNKNOWN


def test_unrelated_file_is_unknown():
    assert sniff(b"name,age\nalice,3\n").report_type == UNKNOWN


def test_cp874_and_tis620_thai_text_decodes():
    thai = "หมายเลขคำสั่งซื้อ,จำนวนเงิน"
    for enc in ("cp874", "tis-620"):
        text, used = decode_bytes(thai.encode(enc))
        assert text == thai
        assert used in ("cp874", "tis-620")


def test_utf8_bom_is_stripped_from_first_header():
    data = "﻿Order ID,Payout Completed Date,Commission Fee,Transaction Fee\n".encode()
    result = sniff(data)
    assert result.encoding == "utf-8-sig"
    assert result.rows[0][0] == "Order ID"


def test_semicolon_delimited_file():
    data = b"Order ID;Payout Completed Date;Commission Fee;Transaction Fee\n1;2026-11-01;-1;-1\n"
    assert sniff(data).report_type == "SHOPEE_SETTLEMENT"

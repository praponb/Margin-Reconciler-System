"""Generate synthetic marketplace exports used by tests and demos.

Run:  uv run python -m reconciler_app.tests.make_fixtures

The files cover Thai and English headers, UTF-8 with and without BOM, CP874,
vendor metadata blocks above the header, XLSX, long-format (Lazada) settlements,
unknown extra columns and planted reconciliation cases:

  Shopee 2611010001  2 items, shipping overcharge (actual 50 vs buyer 30)
  Shopee 2611010002  loss-making (price below COGS)
  Shopee 2611010003  no settlement -> PENDING_PAYOUT
  Shopee 2611010004  cancelled -> excluded
  Lazada 3001        one row per unit (2x same SKU) + Thai CP874 fee lines
  Lazada 3002        unmapped SKU -> UNMAPPED_SKU
  TikTok 5701        healthy; one SKU matches the internal SKU directly
  TikTok 5702        zero-subtotal free-gift order -> quantity weights
  TikTok 5703        cancelled -> excluded
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import xlsxwriter

FIXTURES = Path(__file__).parent / "fixtures"


def _csv_text(rows: list[list[object]]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def _write_csv(name: str, rows: list[list[object]], encoding: str) -> None:
    (FIXTURES / name).write_bytes(_csv_text(rows).encode(encoding))


def _write_xlsx(name: str, rows: list[list[object]]) -> None:
    wb = xlsxwriter.Workbook(str(FIXTURES / name))
    ws = wb.add_worksheet("Sheet1")
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            ws.write(r, c, value)
    wb.close()


def products() -> list[list[object]]:
    return [
        ["SKU", "ชื่อสินค้า", "ต้นทุน", "ค่าบรรจุภัณฑ์"],
        ["TSHIRT-001", "เสื้อยืดคอตตอน", 80, 5],
        ["MUG-002", "แก้วมัคเซรามิก", 60, 8],
        ["SERUM-003", "เซรั่มบำรุงผิว", 250, 10],
        ["CABLE-004", "สายชาร์จ USB-C", 30, 2],
    ]


def sku_mappings() -> list[list[object]]:
    return [
        ["Channel", "Channel SKU", "Internal SKU"],
        ["SHOPEE", "SP-TS-01", "TSHIRT-001"],
        ["SHOPEE", "SP-MUG-02", "MUG-002"],
        ["SHOPEE", "SP-SER-03", "SERUM-003"],
        ["LAZADA", "LZ-TS-RED", "TSHIRT-001"],
        ["LAZADA", "LZ-CBL", "CABLE-004"],
        ["TIKTOK", "TT-MUG", "MUG-002"],
        ["TIKTOK", "TT-CABLE", "CABLE-004"],
    ]


def shopee_orders_th() -> list[list[object]]:
    header = [
        "หมายเลขคำสั่งซื้อ",
        "สถานะการสั่งซื้อ",
        "วันที่ทำการสั่งซื้อ",
        "เลขอ้างอิง SKU (SKU Reference No.)",
        "ราคาตั้งต้น",
        "ส่วนลดจากผู้ขาย",
        "จำนวน",
        "ยอดรวมทั้งหมด",
        "ชื่อผู้ใช้ (ผู้ซื้อ)",  # unknown column -> dropped
    ]
    return [
        ["รายงานคำสั่งซื้อ Shopee"],
        ["ร้านค้า: ตัวอย่างช็อป", "ช่วงเวลา: 01/11/2569 - 07/11/2569"],
        [],
        header,
        ["2611010001", "สำเร็จแล้ว", "01/11/2569 10:15", "SP-TS-01", "฿150.00", "0", "2", "฿430.00", "buyer_a"],
        ["2611010001", "สำเร็จแล้ว", "01/11/2569 10:15", "SP-MUG-02", "100", "0", "1", "฿430.00", "buyer_a"],
        ["2611010002", "สำเร็จแล้ว", "02/11/2569 09:00", "SP-SER-03", "199", "", "1", "229", "buyer_b"],
        ["2611010003", "ที่ต้องจัดส่ง", "06/11/2569 20:30", "SP-TS-01", "159", "0", "1", "159", "buyer_c"],
        ["2611010004", "ยกเลิกแล้ว", "06/11/2569 21:00", "SP-MUG-02", "100", "0", "1", "100", "buyer_d"],
    ]


def shopee_settlement_en() -> list[list[object]]:
    return [
        ["Shopee Income Report"],
        ["Seller: demo_shop", "From 2026-11-01 to 2026-11-07"],
        [
            "Order ID",
            "Payout Completed Date",
            "Commission Fee",
            "Transaction Fee",
            "Service Fee",
            "Shipping Fee Paid by Buyer",
            "Actual Shipping Fee",
            "Shopee Voucher",
            "Total Released Amount",
            "New Platform Column 2026",  # schema drift -> dropped
        ],
        ["2611010001", "2026-11-05", "-20.00", "-8.00", "-12.00", "30.00", "-50.00", "0", "340.00", "x"],
        ["2611010002", "2026-11-05", "-10.00", "-4.00", "-6.00", "30.00", "-30.00", "0", "209.00", "y"],
    ]


def lazada_orders_en() -> list[list[object]]:
    return [
        [
            "Order Item Id",
            "Order Number",
            "Created at",
            "Seller SKU",
            "Unit Price",
            "Seller Discount Total",
            "Paid Price",
            "Status",
            "Buyer Name",
        ],
        [
            "9001",
            "3001",
            "03 Nov 2026 14:20",
            "LZ-TS-RED",
            "199.00",
            "10.00",
            "189.00",
            "delivered",
            "Somchai",
        ],
        [
            "9002",
            "3001",
            "03 Nov 2026 14:20",
            "LZ-TS-RED",
            "199.00",
            "10.00",
            "189.00",
            "delivered",
            "Somchai",
        ],
        ["9003", "3001", "03 Nov 2026 14:20", "LZ-CBL", "99.00", "0.00", "99.00", "delivered", "Somchai"],
        [
            "9004",
            "3002",
            "04 Nov 2026 08:00",
            "LZ-UNKNOWN-9",
            "120.00",
            "0.00",
            "120.00",
            "delivered",
            "Malee",
        ],
    ]


def lazada_settlement_th() -> list[list[object]]:
    header = ["วันที่ทำรายการ", "ชื่อค่าธรรมเนียม", "จำนวนเงิน", "หมายเลขคำสั่งซื้อ", "รหัสรายการสินค้า"]
    d = "05/11/2569"
    return [
        ["รายงานธุรกรรม Lazada"],
        header,
        [d, "ราคาสินค้า", "189.00", "3001", "9001"],
        [d, "ราคาสินค้า", "189.00", "3001", "9002"],
        [d, "ราคาสินค้า", "99.00", "3001", "9003"],
        [d, "ค่าคอมมิชชั่น", "-15.12", "3001", "9001"],
        [d, "ค่าคอมมิชชั่น", "-15.12", "3001", "9002"],
        [d, "ค่าคอมมิชชั่น", "-7.92", "3001", "9003"],
        [d, "ค่าธรรมเนียมการชำระเงิน", "-10.00", "3001", ""],
        [d, "ค่าจัดส่งที่ลูกค้าชำระ", "40.00", "3001", ""],
        [d, "ค่าจัดส่งที่ลาซาด้าเรียกเก็บ", "-40.00", "3001", ""],
        [d, "ค่าธรรมเนียมอื่นๆ", "-5.00", "3001", ""],  # unknown -> service_fee
        [d, "ราคาสินค้า", "120.00", "3002", "9004"],
        [d, "ค่าคอมมิชชั่น", "-9.60", "3002", "9004"],
        [d, "ค่าธรรมเนียมการชำระเงิน", "-2.40", "3002", ""],
    ]


def tiktok_orders_en() -> list[list[object]]:
    header = [
        "Order ID",
        "Order Status",
        "Seller SKU",
        "Quantity",
        "SKU Unit Original Price",
        "SKU Seller Discount",
        "SKU Subtotal After Discount",
        "Order Amount",
        "Created Time",
    ]
    description = [
        "Platform unique order ID.",
        "Current order status.",
        "Seller SKU",
        "Quantity",
        "Unit original price",
        "Seller discount",
        "Subtotal",
        "Order amount",
        "Order created time",
    ]
    return [
        header,
        description,  # TikTok puts a description row under the header; skipped by the parser
        ["5701", "Completed", "TSHIRT-001", "1", "199", "20", "179", "308", "2026-11-03 19:45:00"],
        ["5701", "Completed", "TT-MUG", "1", "129", "0", "129", "308", "2026-11-03 19:45:00"],
        ["5702", "Completed", "TT-CABLE", "2", "0", "0", "0", "0", "2026-11-04 11:00:00"],
        ["5702", "Completed", "TT-MUG", "1", "0", "0", "0", "0", "2026-11-04 11:00:00"],
        ["5703", "Cancelled", "TT-MUG", "1", "129", "0", "129", "129", "2026-11-04 12:00:00"],
    ]


def tiktok_settlement_th() -> list[list[object]]:
    return [
        ["รายงานการชำระเงิน TikTok Shop"],
        [
            "รหัสคำสั่งซื้อ/การปรับยอด",
            "เวลาที่ชำระเงินคำสั่งซื้อ",
            "ค่าธรรมเนียมแพลตฟอร์ม",
            "ค่าธรรมเนียมการชำระเงิน",
            "ค่าธรรมเนียมบริการ",
            "ค่าจัดส่งที่ลูกค้าชำระ",
            "ค่าจัดส่งจริง",
            "ส่วนลดจากแพลตฟอร์ม",
            "ยอดชำระทั้งหมด",
            "หมายเหตุ",
        ],
        ["5701", "2026-11-08", "-24.64", "-6.16", "-9.24", "25", "-25", "0", "267.96", ""],
        ["5702", "2026-11-08", "0", "0", "0", "0", "-45", "0", "-45", "ของแถม"],
    ]


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)
    _write_csv("products.csv", products(), "utf-8-sig")
    _write_csv("sku_mappings.csv", sku_mappings(), "utf-8")
    _write_csv("shopee_orders_th.csv", shopee_orders_th(), "utf-8-sig")
    _write_csv("shopee_settlement_en.csv", shopee_settlement_en(), "utf-8")
    _write_xlsx("lazada_orders_en.xlsx", lazada_orders_en())
    _write_csv("lazada_settlement_th_cp874.csv", lazada_settlement_th(), "cp874")
    _write_csv("tiktok_orders_en.csv", tiktok_orders_en(), "utf-8-sig")
    _write_xlsx("tiktok_settlement_th.xlsx", tiktok_settlement_th())
    print(f"Fixtures written to {FIXTURES}")


if __name__ == "__main__":
    main()

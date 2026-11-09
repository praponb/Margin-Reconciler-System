"""Generate a larger, realistic mock dataset in marketplace export formats.

    uv run python -m reconciler_app.tests.make_mock_data                 # 1000 orders -> mock_data/
    uv run python -m reconciler_app.tests.make_mock_data --orders 5000 --seed 7
    uv run python -m reconciler_app.tests.make_mock_data --load http://127.0.0.1:8000
    uv run python -m reconciler_app.tests.make_mock_data --batch 2 --orders 200 --out ~/Downloads/batch2

Output (same formats the importer expects; see make_fixtures.py):
  products_mock.csv, sku_mappings_mock.csv
  shopee_orders_mock.csv (Thai, UTF-8 BOM, Buddhist-Era dates, metadata block)
  shopee_settlement_mock.csv (English)
  lazada_orders_mock.xlsx (one row per unit)
  lazada_settlement_mock.csv (Thai, CP874, long fee-line format)
  tiktok_orders_mock.csv (English, description row under header)
  tiktok_settlement_mock.xlsx (Thai)

Built-in mix: ~5% cancelled, recent orders often unpaid (pending payout),
~10% of settled orders with a shipping overcharge, a few products priced near
cost (loss-making), and ~2% of lines with an unmapped marketplace SKU.
Order IDs don't overlap the small sample fixtures.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import xlsxwriter

from reconciler_app.tests.make_fixtures import _csv_text

OUT_DIR = Path(__file__).resolve().parents[2] / "mock_data"
MERCHANT_ID = "00000000-0000-0000-0000-000000000001"
VAT = 1.07

# (internal_sku, Thai name, cogs, packaging, list price)
PRODUCTS = [
    ("TSHIRT-001", "เสื้อยืดคอตตอน", 80, 5, 199),
    ("MUG-002", "แก้วมัคเซรามิก", 60, 8, 129),
    ("SERUM-003", "เซรั่มบำรุงผิว", 250, 10, 199),  # priced below cost -> loss-making
    ("CABLE-004", "สายชาร์จ USB-C", 30, 2, 99),
    ("CASE-005", "เคสโทรศัพท์ซิลิโคน", 25, 3, 149),
    ("BOTTLE-006", "ขวดน้ำสแตนเลส", 110, 10, 290),
    ("TOTE-007", "กระเป๋าผ้าแคนวาส", 45, 4, 159),
    ("CANDLE-008", "เทียนหอมอโรมา", 70, 12, 249),
    ("SOCKS-009", "ถุงเท้าข้อสั้น 3 คู่", 35, 2, 89),
    ("CAP-010", "หมวกแก๊ป", 65, 6, 219),
    ("EARBUD-011", "หูฟังบลูทูธ", 320, 15, 590),
    ("LAMP-012", "โคมไฟตั้งโต๊ะ LED", 210, 25, 459),
    ("SOAP-013", "สบู่สมุนไพร", 22, 3, 59),
    ("TEA-014", "ชาเขียวมัทฉะ 100 ก.", 150, 8, 189),  # thin margin -> loss after fees
    ("NOTE-015", "สมุดโน้ตปกแข็ง", 40, 3, 120),
    ("PILLOW-016", "หมอนเมมโมรี่โฟม", 260, 30, 690),
    ("SPOON-017", "ช้อนส้อมพกพา", 28, 2, 79),
    ("MASK-018", "มาสก์หน้า 10 แผ่น", 95, 5, 129),
    ("CHARGER-019", "หัวชาร์จเร็ว 20W", 140, 6, 199),  # thin margin
    ("YOGA-020", "เสื่อโยคะ", 280, 35, 650),
]

CHANNELS = {
    # prefix for channel SKU, commission %, transaction %, service %, share of orders
    "SHOPEE": ("SP", 6.0, 2.14, 3.0, 0.45),
    "LAZADA": ("LZ", 5.0, 2.14, 2.0, 0.30),
    "TIKTOK": ("TT", 4.5, 3.0, 2.5, 0.25),
}
UNMAPPED = {"SHOPEE": ["SP-NEW-901", "SP-NEW-902"], "LAZADA": ["LZ-NEW-911"], "TIKTOK": ["TT-NEW-921"]}

START = datetime(2026, 10, 1, 8, 0)
END = datetime(2026, 11, 8, 22, 0)
TODAY = datetime(2026, 11, 9)


@dataclass
class Line:
    channel_sku: str
    qty: int
    unit_price: float
    discount: float

    @property
    def subtotal(self) -> float:
        return round(self.qty * self.unit_price - self.discount, 2)


@dataclass
class Order:
    channel: str
    order_id: str
    created: datetime
    cancelled: bool
    lines: list[Line]
    buyer_ship: float
    actual_ship: float
    settled_on: datetime | None

    @property
    def subtotal(self) -> float:
        return round(sum(li.subtotal for li in self.lines), 2)

    def fees(self) -> tuple[float, float, float]:
        _, com, txn, svc, _ = CHANNELS[self.channel]
        base = self.subtotal
        return tuple(round(base * pct / 100 * VAT, 2) for pct in (com, txn, svc))


def channel_sku(channel: str, internal_sku: str) -> str:
    prefix = CHANNELS[channel][0]
    if channel == "TIKTOK" and internal_sku.endswith(("1", "5")):
        return internal_sku  # identical code -> auto-matched without a mapping row
    return f"{prefix}-{internal_sku}"


def make_orders(n: int, rng: random.Random, batch: int = 0) -> list[Order]:
    channels = list(CHANNELS)
    weights = [CHANNELS[c][4] for c in channels]
    popularity = [rng.uniform(0.3, 3.0) for _ in PRODUCTS]
    # Each batch gets its own ID range so new batches add orders instead of replacing them.
    counters = {
        "SHOPEE": 2611100001 + batch * 100_000,
        "LAZADA": 4100001 + batch * 100_000,
        "TIKTOK": 5800000000000001 + batch * 1_000_000_000,
    }
    span = (END - START).total_seconds()
    orders = []
    for _ in range(n):
        channel = rng.choices(channels, weights)[0]
        oid = str(counters[channel])
        counters[channel] += rng.randint(1, 7)
        created = START + timedelta(seconds=rng.uniform(0, span))
        created = created.replace(second=0, microsecond=0)

        lines: dict[str, Line] = {}
        for _ in range(rng.choices([1, 2, 3, 4], [60, 25, 10, 5])[0]):
            if rng.random() < 0.02:
                sku = rng.choice(UNMAPPED[channel])
                price, cogs_sku = rng.choice([99, 159, 249]), None
            else:
                p = rng.choices(PRODUCTS, popularity)[0]
                sku, price, cogs_sku = channel_sku(channel, p[0]), p[4], p[0]
            if sku in lines:
                continue
            qty = rng.choices([1, 2, 3, 5], [70, 20, 7, 3])[0]
            discount = round(qty * price * rng.choice([0, 0, 0, 0.05, 0.1, 0.15]), 2)
            if cogs_sku and rng.random() < 0.03:
                price, discount = 0.0, 0.0  # free gift line
            lines[sku] = Line(sku, qty, float(price), discount)

        cancelled = rng.random() < 0.05
        buyer_ship = rng.choice([0, 0, 25, 30, 40, 45])
        actual_ship = buyer_ship
        if rng.random() < 0.10:  # weight/dimension overcharge
            actual_ship = buyer_ship + rng.choice([10, 15, 20, 30, 45])
        elif buyer_ship and rng.random() < 0.2:
            actual_ship = max(0, buyer_ship - rng.choice([5, 10]))

        settle_lag = timedelta(days=rng.randint(3, 10))
        settled_on = created + settle_lag
        if cancelled or settled_on >= TODAY or rng.random() < 0.03:
            settled_on = None
        orders.append(
            Order(channel, oid, created, cancelled, list(lines.values()), buyer_ship, actual_ship, settled_on)
        )
    return orders


# --- writers ------------------------------------------------------------------------------


def write_csv(path: Path, rows: list[list[object]], encoding: str) -> None:
    path.write_bytes(_csv_text(rows).encode(encoding))


def write_xlsx(path: Path, rows: list[list[object]]) -> None:
    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet("Sheet1")
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            ws.write(r, c, value)
    wb.close()


def be_date(dt: datetime, with_time: bool = True) -> str:
    s = f"{dt.day:02d}/{dt.month:02d}/{dt.year + 543}"
    return f"{s} {dt:%H:%M}" if with_time else s


def write_master_data(out: Path, tag: str = "mock") -> None:
    write_csv(
        out / f"products_{tag}.csv",
        [["SKU", "ชื่อสินค้า", "ต้นทุน", "ค่าบรรจุภัณฑ์"]] + [[s, n, c, p] for s, n, c, p, _ in PRODUCTS],
        "utf-8-sig",
    )
    rows = [["Channel", "Channel SKU", "Internal SKU"]]
    for channel in CHANNELS:
        for sku, *_ in PRODUCTS:
            code = channel_sku(channel, sku)
            if code != sku:
                rows.append([channel, code, sku])
    write_csv(out / f"sku_mappings_{tag}.csv", rows, "utf-8")


def write_shopee(out: Path, orders: list[Order], tag: str = "mock") -> None:
    rows = [
        ["รายงานคำสั่งซื้อ Shopee"],
        ["ร้านค้า: ตัวอย่างช็อป", f"ช่วงเวลา: {be_date(START, False)} - {be_date(END, False)}"],
        [],
        [
            "หมายเลขคำสั่งซื้อ",
            "สถานะการสั่งซื้อ",
            "วันที่ทำการสั่งซื้อ",
            "เลขอ้างอิง SKU (SKU Reference No.)",
            "ราคาตั้งต้น",
            "ส่วนลดจากผู้ขาย",
            "จำนวน",
            "ยอดรวมทั้งหมด",
            "ชื่อผู้ใช้ (ผู้ซื้อ)",
        ],
    ]
    settle = [
        ["Shopee Income Report"],
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
        ],
    ]
    for o in orders:
        status = "ยกเลิกแล้ว" if o.cancelled else ("สำเร็จแล้ว" if o.settled_on else "ที่ต้องจัดส่ง")
        total = o.subtotal + o.buyer_ship
        for li in o.lines:
            rows.append(
                [
                    o.order_id,
                    status,
                    be_date(o.created),
                    li.channel_sku,
                    f"฿{li.unit_price:,.2f}",
                    f"{li.discount:.2f}",
                    li.qty,
                    f"฿{total:,.2f}",
                    f"buyer_{o.order_id[-4:]}",
                ]
            )
        if o.settled_on:
            com, txn, svc = o.fees()
            net = o.subtotal + o.buyer_ship - com - txn - svc - o.actual_ship
            settle.append(
                [
                    o.order_id,
                    f"{o.settled_on:%Y-%m-%d}",
                    f"-{com:.2f}",
                    f"-{txn:.2f}",
                    f"-{svc:.2f}",
                    f"{o.buyer_ship:.2f}",
                    f"-{o.actual_ship:.2f}",
                    "0",
                    f"{net:.2f}",
                ]
            )
    write_csv(out / f"shopee_orders_{tag}.csv", rows, "utf-8-sig")
    write_csv(out / f"shopee_settlement_{tag}.csv", settle, "utf-8")


def write_lazada(out: Path, orders: list[Order], tag: str = "mock") -> None:
    rows = [
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
        ]
    ]
    settle = [
        ["รายงานธุรกรรม Lazada"],
        ["วันที่ทำรายการ", "ชื่อค่าธรรมเนียม", "จำนวนเงิน", "หมายเลขคำสั่งซื้อ", "รหัสรายการสินค้า"],
    ]
    item_id = 91000001  # Lazada item IDs are informational; the importer drops them
    for o in orders:
        status = "canceled" if o.cancelled else ("delivered" if o.settled_on else "shipped")
        item_ids = []
        for li in o.lines:
            unit_discount = round(li.discount / li.qty, 2)
            for _ in range(li.qty):
                rows.append(
                    [
                        str(item_id),
                        o.order_id,
                        f"{o.created:%d %b %Y %H:%M}",
                        li.channel_sku,
                        f"{li.unit_price:.2f}",
                        f"{unit_discount:.2f}",
                        f"{li.unit_price - unit_discount:.2f}",
                        status,
                        "Customer",
                    ]
                )
                item_ids.append((str(item_id), li.unit_price - unit_discount))
                item_id += 1
        if o.settled_on:
            d = be_date(o.settled_on, False)
            com, txn, svc = o.fees()
            for iid, paid in item_ids:
                settle.append([d, "ราคาสินค้า", f"{paid:.2f}", o.order_id, iid])
            settle.append([d, "ค่าคอมมิชชั่น", f"-{com:.2f}", o.order_id, ""])
            settle.append([d, "ค่าธรรมเนียมการชำระเงิน", f"-{txn:.2f}", o.order_id, ""])
            settle.append([d, "ค่าธรรมเนียมโปรแกรมส่งฟรี", f"-{svc:.2f}", o.order_id, ""])
            if o.buyer_ship:
                settle.append([d, "ค่าจัดส่งที่ลูกค้าชำระ", f"{o.buyer_ship:.2f}", o.order_id, ""])
            if o.actual_ship:
                settle.append([d, "ค่าจัดส่งที่ลาซาด้าเรียกเก็บ", f"-{o.actual_ship:.2f}", o.order_id, ""])
    write_xlsx(out / f"lazada_orders_{tag}.xlsx", rows)
    write_csv(out / f"lazada_settlement_{tag}.csv", settle, "cp874")


def write_tiktok(out: Path, orders: list[Order], tag: str = "mock") -> None:
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
    rows = [header, ["Platform unique order ID.", "Current order status.", *header[2:]]]
    settle = [
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
        ],
    ]
    for o in orders:
        status = "Cancelled" if o.cancelled else ("Completed" if o.settled_on else "Shipped")
        for li in o.lines:
            rows.append(
                [
                    o.order_id,
                    status,
                    li.channel_sku,
                    li.qty,
                    f"{li.unit_price:.2f}",
                    f"{li.discount:.2f}",
                    f"{li.subtotal:.2f}",
                    f"{o.subtotal + o.buyer_ship:.2f}",
                    f"{o.created:%Y-%m-%d %H:%M:%S}",
                ]
            )
        if o.settled_on:
            com, txn, svc = o.fees()
            net = o.subtotal + o.buyer_ship - com - txn - svc - o.actual_ship
            settle.append(
                [
                    o.order_id,
                    f"{o.settled_on:%Y-%m-%d}",
                    f"-{com:.2f}",
                    f"-{txn:.2f}",
                    f"-{svc:.2f}",
                    f"{o.buyer_ship:.2f}",
                    f"-{o.actual_ship:.2f}",
                    "0",
                    f"{net:.2f}",
                ]
            )
    write_csv(out / f"tiktok_orders_{tag}.csv", rows, "utf-8-sig")
    write_xlsx(out / f"tiktok_settlement_{tag}.xlsx", settle)


def generate(n: int, seed: int, out: Path = OUT_DIR, batch: int = 0) -> list[Order]:
    tag = f"batch{batch}" if batch else "mock"
    rng = random.Random(seed)
    orders = make_orders(n, rng, batch)
    out.mkdir(exist_ok=True)
    write_master_data(out, tag)
    by_channel = {c: [o for o in orders if o.channel == c] for c in CHANNELS}
    write_shopee(out, by_channel["SHOPEE"], tag)
    write_lazada(out, by_channel["LAZADA"], tag)
    write_tiktok(out, by_channel["TIKTOK"], tag)
    return orders


def load(api: str, out: Path = OUT_DIR, merchant_id: str = MERCHANT_ID, tag: str = "mock") -> None:
    import httpx

    q = {"merchant_id": merchant_id}
    with httpx.Client(base_url=api, timeout=300) as client:
        for path, name in (
            ("/products/upload", f"products_{tag}.csv"),
            ("/sku-mappings/upload", f"sku_mappings_{tag}.csv"),
        ):
            res = client.post(path, params=q, files={"file": (name, (out / name).read_bytes())})
            res.raise_for_status()
            print(f"{name}: {res.json()}")
        exports = sorted(
            p
            for p in out.iterdir()
            if p.stem.endswith(f"_{tag}") and p.stem.split("_")[0] in ("shopee", "lazada", "tiktok")
        )
        res = client.post("/ingest", data=q, files=[("files", (p.name, p.read_bytes())) for p in exports])
        res.raise_for_status()
        for r in res.json():
            print(f"{r['filename']}: {r['status']} {r['report_type']} rows={r['rows']} {r['message'] or ''}")
        res = client.post("/reconcile", json=q)
        res.raise_for_status()
        print("reconcile:", res.json())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--orders", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--batch", type=int, default=0, help="batch number >= 1 gives new order IDs and *_batchN files"
    )
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="output folder (default: mock_data/)")
    ap.add_argument(
        "--load", metavar="API_URL", help="upload the generated files to a running API and reconcile"
    )
    args = ap.parse_args()
    orders = generate(args.orders, args.seed, args.out, args.batch)
    lines = sum(len(o.lines) for o in orders)
    print(f"Generated {len(orders)} orders / {lines} lines in {args.out}")
    if args.load:
        load(args.load, args.out, tag=f"batch{args.batch}" if args.batch else "mock")


if __name__ == "__main__":
    main()

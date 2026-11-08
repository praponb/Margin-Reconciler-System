"""Multilingual (Thai / English) header dictionary -> canonical field tokens.

This is the single place to edit when a marketplace renames a column or when real
export headers become available. Aliases are matched after `normalize_header`, so
case, repeated whitespace, BOMs and zero-width characters don't matter.

The sniffer derives its platform signatures from these aliases, so adding an alias
here also improves detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Canonical field tokens -------------------------------------------------

ORDER_FIELDS = (
    "channel_order_id",
    "order_date",
    "order_status",
    "channel_sku",
    "quantity",
    "unit_original_price",
    "seller_discount",
    "subtotal",
    "buyer_total_paid",
)

SETTLEMENT_FEE_FIELDS = (
    "commission_fee",
    "transaction_fee",
    "service_fee",
    "shipping_fee_paid_by_buyer",
    "actual_shipping_fee_charged",
    "voucher_platform_subsidy",
)

SETTLEMENT_FIELDS = ("channel_order_id", "settlement_date", *SETTLEMENT_FEE_FIELDS, "net_payout_amount")

LONG_SETTLEMENT_FIELDS = ("channel_order_id", "settlement_date", "fee_name", "amount")

CHANNELS = ("SHOPEE", "LAZADA", "TIKTOK")


@dataclass(frozen=True)
class ReportSpec:
    key: str
    channel: str
    kind: str  # "ORDER" | "SETTLEMENT" | "PRODUCT_MASTER" | "SKU_MAPPING"
    fields: dict[str, tuple[str, ...]]
    required: tuple[str, ...]
    layout: str = "wide"  # "wide" | "long" (one row per fee line)
    detectable: bool = True  # included in sniffer signature matching
    _alias_index: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        index: dict[str, str] = {}
        for canonical, aliases in self.fields.items():
            for alias in (canonical, *aliases):
                index.setdefault(normalize_header(alias), canonical)
        object.__setattr__(self, "_alias_index", index)

    def lookup(self, header: str) -> str | None:
        return self._alias_index.get(normalize_header(header))

    @property
    def signature(self) -> frozenset[str]:
        """Normalized aliases used by the sniffer (canonical tokens excluded)."""
        return frozenset(normalize_header(a) for aliases in self.fields.values() for a in aliases)


class MappingError(ValueError):
    """Raised when a file lacks columns required for its report type."""

    def __init__(self, report_type: str, missing: list[str]):
        self.report_type = report_type
        self.missing = missing
        super().__init__(f"{report_type}: missing required column(s): {', '.join(missing)}")


_INVISIBLE = {ord(c): " " for c in "﻿​‌‍ \t\r\n"}


def normalize_header(value: object) -> str:
    """Strip BOM/zero-width chars, collapse whitespace, casefold."""
    return " ".join(str(value or "").translate(_INVISIBLE).split()).casefold()


# --- Platform report specs --------------------------------------------------

REPORT_SPECS: dict[str, ReportSpec] = {}


def _register(spec: ReportSpec) -> None:
    REPORT_SPECS[spec.key] = spec


_register(
    ReportSpec(
        key="SHOPEE_ORDER",
        channel="SHOPEE",
        kind="ORDER",
        fields={
            "channel_order_id": ("Order ID", "หมายเลขคำสั่งซื้อ"),
            "order_date": ("Order Creation Date", "วันที่ทำการสั่งซื้อ"),
            "order_status": ("Order Status", "สถานะการสั่งซื้อ"),
            "channel_sku": ("SKU Reference No.", "เลขอ้างอิง SKU (SKU Reference No.)", "เลขอ้างอิง SKU"),
            "quantity": ("Quantity", "จำนวน"),
            "unit_original_price": ("Original Price", "ราคาตั้งต้น"),
            "seller_discount": ("Seller Rebate", "ส่วนลดจากผู้ขาย"),
            "subtotal": ("Product Subtotal", "ราคาขายสุทธิ"),
            "buyer_total_paid": ("Grand Total", "ยอดรวมทั้งหมด", "ยอดคำสั่งซื้อทั้งหมด"),
        },
        required=("channel_order_id", "order_date", "channel_sku", "unit_original_price"),
    )
)

_register(
    ReportSpec(
        key="SHOPEE_SETTLEMENT",
        channel="SHOPEE",
        kind="SETTLEMENT",
        fields={
            "channel_order_id": ("Order ID", "หมายเลขคำสั่งซื้อ"),
            "settlement_date": ("Payout Completed Date", "วันที่โอนชำระเงินสำเร็จ"),
            "commission_fee": ("Commission Fee", "ค่าคอมมิชชั่น"),
            "transaction_fee": ("Transaction Fee", "ค่าธรรมเนียมการทำธุรกรรม"),
            "service_fee": ("Service Fee", "ค่าบริการ"),
            "shipping_fee_paid_by_buyer": ("Shipping Fee Paid by Buyer", "ค่าจัดส่งที่ชำระโดยผู้ซื้อ"),
            "actual_shipping_fee_charged": ("Actual Shipping Fee", "ค่าจัดส่งจริง"),
            "voucher_platform_subsidy": ("Shopee Voucher", "โค้ดส่วนลดจาก Shopee"),
            "net_payout_amount": ("Total Released Amount", "จำนวนเงินทั้งหมดที่ได้รับ"),
        },
        required=("channel_order_id", "settlement_date", "commission_fee"),
    )
)

_register(
    ReportSpec(
        key="LAZADA_ORDER",
        channel="LAZADA",
        kind="ORDER",
        fields={
            "channel_order_id": ("Order Number", "orderNumber", "หมายเลขคำสั่งซื้อ"),
            "order_date": ("Created at", "createTime", "วันที่สร้าง"),
            "order_status": ("Status", "สถานะ"),
            "channel_sku": ("Seller SKU", "sellerSku", "SKU ผู้ขาย"),
            "unit_original_price": ("Unit Price", "unitPrice", "ราคาต่อหน่วย"),
            "seller_discount": ("Seller Discount Total", "sellerDiscountTotal", "ส่วนลดจากผู้ขายทั้งหมด"),
            "subtotal": ("Paid Price", "paidPrice", "ราคาที่ชำระ"),
        },
        # Lazada exports one row per unit; quantity defaults to 1 and rows are summed.
        required=("channel_order_id", "order_date", "channel_sku", "unit_original_price"),
    )
)

_register(
    ReportSpec(
        key="LAZADA_SETTLEMENT",
        channel="LAZADA",
        kind="SETTLEMENT",
        layout="long",
        fields={
            "channel_order_id": ("Order No.", "Order Number", "หมายเลขคำสั่งซื้อ"),
            "settlement_date": ("Release Date", "Transaction Date", "วันที่โอนเงิน", "วันที่ทำรายการ"),
            "fee_name": ("Fee Name", "ชื่อค่าธรรมเนียม", "รายการ"),
            "amount": ("Amount", "จำนวนเงิน"),
            # Present in real exports; not needed for order-level reconciliation.
            "order_item_id": ("Order Item Id", "รหัสรายการสินค้า"),
        },
        required=("channel_order_id", "settlement_date", "fee_name", "amount"),
    )
)

_register(
    ReportSpec(
        key="TIKTOK_ORDER",
        channel="TIKTOK",
        kind="ORDER",
        fields={
            "channel_order_id": ("Order ID", "รหัสคำสั่งซื้อ"),
            "order_date": ("Created Time", "เวลาที่สร้าง"),
            "order_status": ("Order Status", "สถานะคำสั่งซื้อ"),
            "channel_sku": ("Seller SKU", "SKU ของผู้ขาย"),
            "quantity": ("Quantity", "จำนวน"),
            "unit_original_price": ("SKU Unit Original Price", "ราคาเดิมต่อหน่วยของ SKU"),
            "seller_discount": ("SKU Seller Discount", "ส่วนลดจากผู้ขายของ SKU"),
            "subtotal": ("SKU Subtotal After Discount", "ยอดรวมย่อยของ SKU หลังหักส่วนลด"),
            "buyer_total_paid": ("Order Amount", "ยอดคำสั่งซื้อ"),
        },
        required=("channel_order_id", "order_date", "channel_sku", "unit_original_price"),
    )
)

_register(
    ReportSpec(
        key="TIKTOK_SETTLEMENT",
        channel="TIKTOK",
        kind="SETTLEMENT",
        fields={
            "channel_order_id": ("Order/adjustment ID", "Order ID", "รหัสคำสั่งซื้อ/การปรับยอด"),
            "settlement_date": ("Order settled time", "เวลาที่ชำระเงินคำสั่งซื้อ"),
            "commission_fee": ("Platform Fee", "Platform commission fee", "ค่าธรรมเนียมแพลตฟอร์ม"),
            "transaction_fee": ("Transaction fee", "ค่าธรรมเนียมการชำระเงิน"),
            "service_fee": ("SFP service fee", "Service fee", "ค่าธรรมเนียมบริการ"),
            "shipping_fee_paid_by_buyer": ("Customer-paid shipping fee", "ค่าจัดส่งที่ลูกค้าชำระ"),
            "actual_shipping_fee_charged": ("Actual shipping fee", "ค่าจัดส่งจริง"),
            "voucher_platform_subsidy": ("Platform discount", "ส่วนลดจากแพลตฟอร์ม"),
            "net_payout_amount": ("Total settlement amount", "ยอดชำระทั้งหมด"),
            "sku_id": ("SKU ID", "รหัส SKU"),
        },
        required=("channel_order_id", "settlement_date", "commission_fee"),
    )
)

# --- Non-marketplace uploads (not used for sniffing) -------------------------

_register(
    ReportSpec(
        key="PRODUCT_MASTER",
        channel="INTERNAL",
        kind="PRODUCT_MASTER",
        detectable=False,
        fields={
            "internal_sku": ("SKU", "Internal SKU", "รหัสสินค้า"),
            "product_name": ("Product Name", "Name", "ชื่อสินค้า"),
            "cogs_amount": ("COGS", "Cost", "Unit Cost", "ต้นทุน", "ต้นทุนต่อหน่วย"),
            "packaging_cost": ("Packaging", "Packaging Cost", "ค่าบรรจุภัณฑ์"),
        },
        required=("internal_sku", "product_name", "cogs_amount"),
    )
)

_register(
    ReportSpec(
        key="SKU_MAPPING",
        channel="INTERNAL",
        kind="SKU_MAPPING",
        detectable=False,
        fields={
            "channel": ("Channel", "Platform", "ช่องทาง"),
            "channel_sku": ("Channel SKU", "Marketplace SKU", "SKU ช่องทาง"),
            "internal_sku": ("Internal SKU", "SKU", "รหัสสินค้า"),
        },
        required=("channel", "channel_sku", "internal_sku"),
    )
)


# --- Lazada long-format fee buckets ------------------------------------------
# Each fee line's name is mapped to a bucket. Cost buckets are stored as positive
# costs (sign flipped from the export). Unknown names with a negative amount fall
# into service_fee, so unrecognised deductions are never hidden.

LAZADA_FEE_BUCKETS: dict[str, tuple[str, ...]] = {
    "item_price": ("Item Price Credit", "ราคาสินค้า"),
    "commission_fee": ("Commission", "Commission Fee", "ค่าคอมมิชชั่น"),
    "transaction_fee": ("Payment Fee", "Payment Fee - Correction", "ค่าธรรมเนียมการชำระเงิน"),
    "service_fee": (
        "Free Shipping Max Fee",
        "LazCoins Discount Promotion Fee",
        "Marketing Solution Fee",
        "ค่าบริการ",
        "ค่าธรรมเนียมโปรแกรมส่งฟรี",
    ),
    "shipping_fee_paid_by_buyer": ("Shipping Fee (Paid By Customer)", "ค่าจัดส่งที่ลูกค้าชำระ"),
    "actual_shipping_fee_charged": (
        "Shipping Fee (Charged by Lazada)",
        "Shipping Fee Charged by Lazada",
        "ค่าจัดส่งที่ลาซาด้าเรียกเก็บ",
    ),
    "voucher_platform_subsidy": ("Lazada Bonus", "Voucher Subsidy", "เงินสนับสนุนโค้ดส่วนลด"),
}

LAZADA_FEE_LOOKUP: dict[str, str] = {
    normalize_header(alias): bucket for bucket, aliases in LAZADA_FEE_BUCKETS.items() for alias in aliases
}

# --- Order status --------------------------------------------------------------

CANCELLED_STATUS_KEYWORDS = ("cancel", "ยกเลิก", "unpaid", "ยังไม่ชำระ")


def map_columns(headers: list[str], spec: ReportSpec) -> tuple[dict[str, str], list[str]]:
    """Return ({original_header: canonical}, dropped_headers).

    The first header matching a canonical field wins. Unknown headers are dropped
    (schema-drift tolerance). Raises MappingError if required fields are missing.
    """
    mapping: dict[str, str] = {}
    seen: set[str] = set()
    dropped: list[str] = []
    for header in headers:
        canonical = spec.lookup(header)
        if canonical is None or canonical in seen:
            dropped.append(header)
            continue
        mapping[header] = canonical
        seen.add(canonical)
    missing = [f for f in spec.required if f not in seen]
    if missing:
        raise MappingError(spec.key, missing)
    return mapping, dropped

# Multi-Channel Fee & Margin Reconciler

Reconciles Shopee, Lazada and TikTok Shop order and settlement exports. It:

- splits order-level platform fees across individual SKUs,
- finds shipping overcharges, and loss-making or unmapped SKUs,
- suggests listing prices that reach a target net margin.

The system is built from `spec.docx`. See [Where this differs from the spec](#where-this-differs-from-the-spec) for the deliberate changes.

**Stack:** Python 3.12, Polars, SQLAlchemy 2.0 + Alembic (SQLite or PostgreSQL), FastAPI, Streamlit.

## Quick start

```bash
uv sync                                                  # install
uv run uvicorn reconciler_app.api.main:app --reload      # API on :8000 (runs migrations on startup)
uv run streamlit run reconciler_app/app.py               # dashboard on :8501
```

To try it with the synthetic sample data:
1. In **Products & SKU mapping**, upload `reconciler_app/tests/fixtures/products.csv` and `sku_mappings.csv`.
2. In **Upload & reconcile**, upload the six marketplace files from the same folder and click **Reconcile**.

API docs are at http://127.0.0.1:8000/docs.

For a bigger dataset, `uv run python -m reconciler_app.tests.make_mock_data --load http://127.0.0.1:8000` generates 1,000 mock orders into `mock_data/` (git-ignored) and loads them. Use `--orders N --seed S` for other sizes.

### PostgreSQL

```bash
docker compose up -d
export DATABASE_URL=postgresql+psycopg://reconciler:reconciler@localhost:5432/reconciler
uv run alembic upgrade head
```

Settings come from environment variables or `.env`; see `.env.example`.

## Tests

```bash
uv run pytest                    # 92 tests, in-memory SQLite (includes rendering every UI page)
uv run ruff check .
TEST_DATABASE_URL=postgresql+psycopg://reconciler:reconciler@localhost:5432/reconciler uv run pytest
uv run python -m reconciler_app.tests.make_fixtures      # regenerate the synthetic exports
```

## How it works

```
upload ─▶ SHA-256 check ─▶ sniffer ─▶ canonical mapping ─▶ parser ─▶ DB (orders/items/settlements)
                                                                         │
                         fact_sku_margins ◀─ engine (Polars) ◀─ reconcile┘
```

| Module | Role |
|---|---|
| `core/sniffer.py` | Decodes the file and finds the platform, report type and header row (see below). |
| `core/canonical_mapping.py` | **The one place to edit when platforms change their headers.** Holds the Thai and English aliases for every field, Lazada fee-name buckets and cancelled-status keywords. |
| `core/parser.py`, `core/normalize.py` | Cleans data (`฿1,234.50`, `(12.00)`, `-`, Buddhist-Era dates, day-first formats). Sums Lazada's one-row-per-unit order rows and pivots its fee lines into one row per order. Drops columns it doesn't recognise. |
| `core/engine.py` | Splits fees across lines by value weight, audits shipping gaps, and computes profit, margin and status. |
| `core/pricing.py` | Reverse pricing (spec §4.4), plus a cost breakdown. |
| `services/` | Idempotent ingestion; rebuilds the margin data for a date range; builds the pipeline status (`services/pipeline.py`). Every upload attempt and reconcile is logged in `pipeline_runs`. |
| `api/` | FastAPI routes. |
| `app.py`, `views/` | Streamlit UI. |

**How the sniffer works:**
- **Encoding:** checks for a BOM first, then tries strict UTF-8, then CP874 / TIS-620. XLSX files are read with calamine.
- **Detection:** scores each of the first 15 rows against every report's aliases. The best row is the header, and anything above it is skipped as vendor metadata.
- **Thresholds:** a match needs at least 3 hits and no tie between report types.

### API

| Method | Path | Purpose |
|---|---|---|
| POST | `/ingest` | Multipart `merchant_id` + one or more `files`. Returns a result for each file (`ingested` / `skipped_duplicate` / `error`). |
| GET | `/ingest/batches` | Upload history |
| GET/POST | `/products`, `/products/upload` | Product master (COGS, packaging) |
| GET/POST | `/sku-mappings`, `/sku-mappings/upload` | Marketplace SKU → internal SKU |
| POST | `/reconcile` | Rebuild `fact_sku_margins` for `{merchant_id, date_from?, date_to?}`. Dates are inclusive Bangkok-local dates. |
| GET | `/margins`, `/margins/summary`, `/exceptions` | Line detail, P&L by SKU and channel, exception queue (worst profit first) |
| GET | `/margins/export.csv` | CSV export in UTF-8 with BOM, so it opens correctly in Excel |
| POST | `/pricing/recommend` | Recommended price and breakdown |
| GET | `/pipeline/status` | Stage-by-stage counts, payout and SKU coverage, freshness, run history |

## Conventions

- **Fee signs:** exports show fees as negative numbers. They are stored as **positive costs**. Credits (shipping paid by the buyer, platform vouchers) keep their sign.
- **VAT:** settlement fees are used exactly as charged, so they already include VAT. Reverse pricing adds VAT (`VAT_RATE`, default 7%) on top of the fee *rates*, as spec §4.4 does.
- **Time:**
  - **Parsing:** timestamps without a time zone in exports are treated as Asia/Bangkok.
  - **Storage:** timestamps are stored in UTC.
  - **Display:** the API and UI show them in Bangkok time.
- **Cancelled orders** are excluded from margins. A status counts as cancelled if it contains `cancel`, `ยกเลิก`, `unpaid` or `ยังไม่ชำระ`.
- **SKU resolution order:**
  1. the line's own `internal_sku`,
  2. then the mapping table,
  3. then an internal SKU with the same code.
- **Unknown Lazada fee lines** with a negative amount count as `service_fee`, so no deduction is hidden.

## Where this differs from the spec

| Spec | Implemented | Why |
|---|---|---|
| The sniffer returns on the first `any()` token match | Scored match with a minimum and tie detection | "Order ID" appears in three platforms' signatures, so the first match always said Shopee |
| `chardet` | Fixed decoding order: BOM → UTF-8 → CP874 → TIS-620 | chardet often misreads Thai code pages |
| Joins on `channel_order_id` only | Joins on `(merchant_id, channel, channel_order_id)` | Order IDs collide across channels and merchants |
| Settlements joined row by row | Collapsed to one row per order first | Adjustment rows and Lazada fee lines multiplied the item rows |
| Status order: loss > overcharge > pending | `UNMAPPED_SKU` > `PENDING_PAYOUT` > `LOSS_MAKING` > `SHIPPING_OVERCHARGE` > `HEALTHY` | Before payout, fees are unknown, so profit is provisional. Without a COGS, profit means nothing |
| `internal_sku NOT NULL` in the mart | Unmapped lines keep the channel SKU with status `UNMAPPED_SKU` | Unmapped lines surface in the exception queue instead of disappearing |
| Float allocation | Rounded to 4 dp, with the rounding remainder added to the highest-weight line | Each order's allocated fees sum exactly to its fee |
| — | `ingestion_batches` table (SHA-256), plus a `uq_settlement_slice` unique key | Idempotent re-ingestion |
| — | `pipeline_runs` table (migration `0002`) | Run history, failures and freshness for the Data pipeline page |
| Mart "partitioned by merchant + date" | Indexes only | A Postgres partition key must be part of the PK. Partitioning later means changing the PK to `(reconcile_id, merchant_id, order_date)` |
| DuckDB | Not used | Polars covers every computation |

## Known limitations

- The header aliases in `canonical_mapping.py` are based on **synthetic** fixtures. Check them against real platform exports before production; only that file should need changes.
- There is no authentication or tenant isolation beyond the `merchant_id` parameter.

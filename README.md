# Procurement Audit & Demand Forecasting

**A purchasing control system for distributors running ERP Profit 2K12.** It reads purchases, sales and stock directly from SQL Server (read-only), finds where the money is stuck, and produces a prioritized purchase-order list with business rules instead of gut feeling.

Built and used in production at a pharmaceutical wholesaler in Venezuela, where it audits ~$450K of annual purchases across ~3,000 SKUs.

```
Python 3.11+ · pandas · scikit-learn · Flask · SQL Server (pyodbc) · openpyxl · 44 tests
```

---

## What it answers

| Question | Module | Output |
|---|---|---|
| Did we buy well? What are we missing? | **Audit** | 10-sheet Excel + HTML dashboard |
| What should we order this week? | **Purchase orders** | 11-sheet Excel with priorities, EOL and price checks |
| What sells, to whom, at what margin? | **Sales BI** | `/ventas` dashboard + JSON API + Excel/PDF export |
| Which warehouses are in scope? | **Warehouse filter** | Persistent selector, applied to every query |
| How is each decision made? | **Rules** | `ACT − COM`, in-transit blocking, EOL lifecycle, price ceiling |

---

## Results from the live deployment

Cut-off 12/11/2026, after warehouse filtering:

| Metric | Value |
|---|---:|
| Purchases / sales reviewed | **$452,770 / $561,770** |
| Gross margin | **35.0%** |
| Real stockouts detected | **347 SKUs** |
| Overstocked SKUs | **304** |
| **Capital tied up in excess stock** | **$116,823** |
| End-of-life / obsolete SKUs blocked from purchasing | **40** |
| Purchase orders generated | **180** (10,536 units, $32,340) |

Conservative annual benefit estimated between **$15,000 and $25,000**, plus a one-off cash release of **$20,000–$28,000** if the liquidation is executed. These are estimates, not guarantees: they depend on Purchasing acting on the recommendations.

A second, independent cut over the same data found that **allocation, not volume, was the problem**: the company bought 99.7% of what it sold, but concentrated capital in SKUs that did not rotate while 249 SKUs were out of stock.

---

## How it works

```
                    ┌──────────────────────────┐
   Profit 2K12 ────▶│  read-only SQL connector │
   (SQL Server)     │  SELECT / WITH only      │
                    └────────────┬─────────────┘
                                 ▼
   Excel mode ─────▶  ┌───────────────────────┐
   (no DB needed)     │  data_processing      │  canonical dataframes
                      └──────────┬────────────┘
                                 ▼
        ┌────────────────────────┼─────────────────────────┐
        ▼                        ▼                         ▼
 ┌─────────────┐          ┌─────────────┐          ┌──────────────┐
 │  audit      │          │  forecast   │          │  orders      │
 │  (10 sheets)│          │  MA / ML    │          │  (11 sheets) │
 └─────────────┘          └─────────────┘          └──────────────┘
        │                        │                         │
        └────────────────────────┴─────────────────────────┘
                                 ▼
                    Excel · HTML dashboard · Flask UI
```

### Decision rules (the interesting part)

- **`ACT − COM` base.** In-transit stock (`LLE`) is **never** added as available: it *blocks* the order until the ETA is confirmed. This single rule removed most duplicate orders.
- **EOL lifecycle.** `ACTIVO → EN_OBSERVACION (90d) → OBSOLETO (180d) → EOL/LIQUIDACION`. Blocked states generate no automatic order.
- **Price ceiling.** Weighted 90-day reference price; >20% variation blocks the purchase order, and any order above $2,000 impact is escalated for approval.
- **Warehouse filter.** Profit's `saAlmacen` has no `active` flag, so the scope is an explicit, validated selection — filtering out reception, transit, expired and adjustment warehouses changed stock from 75,831 to 67,637 units and fixed a contaminated KPI.

### Forecasting: an honest result

Six-month temporal validation, WAPE:

| Model | WAPE |
|---|---:|
| MA4 × 4 | 101.0% |
| MA8 × 4 | 97.0% |
| Gradient Boosting | 116.0% |
| MLP (2 hidden layers) | 101.9% |
| **Hybrid (MLP + MA8)** | **96.2%** |

The hybrid beats the moving average by less than one point. **The classical engine stays as the default** and ML is offered as a monitored alternative. The real gain would come from recording stockouts, lead times and actual stock — not from a better algorithm. The README says so on purpose.

---

## Quick start

```bash
pip install -r requirements.txt

# 1) Purchase orders from Profit
python main.py --source profit --as-of 12/11/2026

# 2) Full purchasing audit
python auditoria.py --year 2026 --as-of 12/11/2026

# 3) Local web UI
python ui/app.py        # http://127.0.0.1:5050
```

No database? Run in Excel mode:

```bash
python main.py --sales data/raw/ventas.xlsx \
               --catalog data/raw/catalogo.xlsx \
               --stock data/raw/stock.xlsx
```

### Configuration

Copy `config/database.example.yaml` to `config/database.local.yaml` (git-ignored) or use environment variables:

```bash
PROFIT_DB_SERVER=...   PROFIT_DB_NAME=...   PROFIT_DB_USER=...   PROFIT_DB_PASSWORD=...
```

Requirements for the live connection: SQL Server, ODBC Driver 17, and a **read-only** database user.

> **Security:** the connector rejects anything that is not `SELECT` / `WITH`, blocks `INSERT, UPDATE, DELETE, DROP, ALTER, EXEC, MERGE, TRUNCATE, GRANT`, and opens the connection with `ApplicationIntent=ReadOnly`. Credentials never reach the browser or the logs.

---

## Tests

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

44 tests covering order math, ABC/XYZ segmentation, business rules (`ACT−COM`, LLE blocking, EOL, price ceiling), Excel generation, read-only enforcement and the sales cache.

---

## Repository layout

```
main.py / auditoria.py     entry points (orders / audit)
src/
  data_processing/         loaders, read-only Profit connector, warehouse filter
  analytics/               ABC/XYZ, sales analytics
  models/                  classical forecast + global ML/DL
  recommendation/          orders, pricing, alerts
  audit/                   purchasing audit
  reporting/               Excel, dashboards, PDF export
ui/                        Flask app + HTMX-style templates
config/                    parameters, rules, example DB config
docs/                      methodology, rules, ML notes, user guide (ES)
tests/                     44 tests
```

---

## What is *not* in this repository

The system was built for a real company, so the repository ships **code and documentation only**:

- no sales, stock or customer data (`data/` is git-ignored);
- no generated reports (`outputs/`);
- no real credentials, server addresses or supplier catalog;
- no production database backups.

`config/proveedores.example.csv` shows the expected format with fictional suppliers.

---

## Limitations

1. In-transit stock blocks orders by design; if it does not reflect real purchase orders, the suggestions will drift.
2. Cost is the weighted average of purchases, not an accounting cost per invoice.
3. Historical stockouts are not recorded, so the system detects the symptom (a sale without stock), not the cause.
4. Real lead time is still configured, not measured from PO to receipt.
5. The BI caches 90-day queries for 15 minutes; a cold query takes ~7 s.

---

## Author

**Alexis Yari** — Electronic Engineer · IT/OT automation & infrastructure
[LinkedIn](https://linkedin.com/in/alexis-yari-ing) · alexjr001@gmail.com

> Shared as a professional portfolio. The code and business logic belong to their author; no open-source license is granted. Redistribution or commercial use requires written permission.

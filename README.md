# Celerity

**An Agentic AI Performance Engineer**

Find bottlenecks. Test fixes. Verify improvements.

Celerity will use a supervisor, investigator, and optimizer to diagnose backend
performance problems and validate changes with real experiments. This repository
currently implements **Phase 1: the target application**, not the future agents.

## What works in Phase 1

- A FastAPI shopping API backed by real PostgreSQL.
- Products with images, customers, order history, checkout quotes, and order creation.
- Integer-cent prices, validated carts, transactional stock updates, and sorted row locks.
- A separate local HTTP shipping service; no real purchases or payment provider.
- Three independently switchable faults with identical expected response data.
- Deterministic synthetic seeding, integration tests, and a fault reproduction script.
- Docker Compose and a Windows-native development option.

## Quick start on this machine

Open PowerShell in `C:\Users\sumit\Desktop\Celerity`.

The local Python environment and project-local PostgreSQL have been prepared.
Start PostgreSQL if it has stopped, then start the two web services:

```powershell
.\.venv\Scripts\python.exe scripts/bootstrap_postgres.py
.\scripts\start.ps1 -Scenario healthy
```

Open <http://127.0.0.1:8000/docs> for the interactive API.
Use <http://127.0.0.1:8000/lab/scenario> to inspect the active flags and actual index.

Keep the launcher terminal open. To stop it, press Ctrl+C, or from another terminal:

```powershell
.\scripts\stop.ps1
```

Stop before changing scenarios. Then run one of:

```powershell
.\scripts\start.ps1 -Scenario n-plus-one
.\scripts\start.ps1 -Scenario missing-index
.\scripts\start.ps1 -Scenario redundant-shipping
.\scripts\start.ps1 -Scenario all
```

The start script initializes the database idempotently and applies the selected
index configuration. It does not erase existing rows. Fault flags are fixed for
each process; they are not changed in the middle of a request.

The portable PostgreSQL server remains running after stopping the web services.
To stop that project-local server as well:

```powershell
& .\.runtime\pgsql\bin\pg_ctl.exe stop -D .\.runtime\pgdata -m fast -w
```

If PowerShell blocks a script, use the equivalent Python launcher without changing
your machine's execution policy:

```powershell
.\.venv\Scripts\python.exe scripts/dev.py --scenario healthy
.\.venv\Scripts\python.exe scripts/dev.py --stop
```

## Fresh checkout: Windows-native setup

Install Python 3.12+ first, then:

```powershell
.\scripts\setup.ps1 -PortablePostgres
.\scripts\start.ps1 -Scenario healthy
```

Pass `-PythonPath 'C:\path\to\python.exe'` if needed. The optional PostgreSQL helper
downloads PostgreSQL 17.11 binaries from EDB into `.runtime/`, initializes a local
cluster, and binds it to loopback port 55432. It does not install a Windows service
or change the global PATH. Its recorded SHA-256 identifies the downloaded file;
it is not an independently verified publisher checksum.

## Docker Compose alternative

With Docker and Compose available:

```sh
docker compose up --build -d
docker compose run --rm test
```

Compose waits for PostgreSQL, runs the database initializer, waits for shipping,
and then starts the API. Database and API host ports are bound to loopback.
Stop the native launcher/PostgreSQL first if they occupy 8000 or 55432.

To enable a fault, edit its flag in `.env`, then rerun:

```sh
docker compose up --build -d
```

The completed initializer is rerun by `compose up`; check `/lab/scenario` afterwards.
For an explicit index refresh on an already-running stack, stop API requests, run
`docker compose run --rm db-init`, and recreate the API with `docker compose up -d api`.
Keep the named volume to preserve data. Local development credentials in this lab
are disposable examples, not production credentials.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Check the API's database connection |
| GET | `/lab/scenario` | Show fault settings and index presence |
| GET | `/api/products?limit=20&offset=0` | List products and their images |
| GET | `/api/products/{id}` | Read one product |
| GET | `/api/customers` | List synthetic customers |
| POST | `/api/customers` | Create a customer |
| GET | `/api/customers/{id}/orders` | Read recent orders for one customer |
| GET | `/api/orders/{id}` | Read one order and its items |
| GET | `/api/checkout/quote?zone=national` | Ask the shipping service for a quote |
| POST | `/api/orders` | Create an order and reduce stock atomically |

In `/docs`, try this body for `POST /api/orders`:

```json
{
  "customer_id": 1,
  "items": [{"product_id": 1, "quantity": 2}],
  "shipping_zone": "national"
}
```

The server calculates prices. Clients cannot choose the order total.
Demo currency is USD; `1125` means $11.25. This is synthetic teaching data.

## The faults

| Flag | Target | Healthy | Faulty |
| --- | --- | --- | --- |
| `CELERITY_ENABLE_N_PLUS_ONE` | Product list | Two SELECTs for 20 products | 21 SELECTs for the same response |
| `CELERITY_ENABLE_MISSING_INDEX` | Customer order lookup | Composite lookup index present | Index physically absent |
| `CELERITY_ENABLE_REDUNDANT_SHIPPING` | Quotes and new orders | One downstream quote call | Three identical downstream calls |

The missing-index scenario changes database work, not an artificial sleep. The
shipping service itself has configurable 40 ms delay to model a controlled remote
dependency. The API does not sleep to fake a database bottleneck.

Default seed: 2,000 products, 4,000 images, 1,000 customers, 30,000 orders, and
30,000 order items. Re-running initialization does not duplicate them. Larger
seed counts apply only to an empty database; they do not resize an existing seed.

## Verify

With PostgreSQL running:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Tests use isolated, randomly named PostgreSQL schemas. They cover functional
responses, cart validation, duplicate emails, rollback on errors, concurrent
stock reservations, and the three fault mechanisms. They do not reset lab data.
Shipping is mocked only in the automated correctness tests.

With the shipping stub running and the lab otherwise idle:

```powershell
.\.venv\Scripts\python.exe scripts/verify_phase1.py
```

This additionally uses real outbound HTTP and PostgreSQL execution plans. It
temporarily removes/recreates the lookup index and restores its original state.
Results go to `var/phase1-verification.json`. This is **not** a statistically
controlled performance benchmark; that belongs to Phase 2.

## Learn the code

Read `docs/PHASE_1_EXPLAINED.md` first. It walks through the request lifecycle,
tables, files, faults, tests, and exercises in plain language.
`docs/ROADMAP.md` records the seven phases.

## Current scope

This is a local synthetic lab with no authentication, real payment processing,
agent execution, performance dashboard, or telemetry backend yet. Keep it on
localhost. Phase 2 adds load testing/tracing; Phase 3 adds the first agent.
Order submission is not idempotent: submitting the same valid body twice creates
two orders. Preventing overselling is a separate property covered by row locks.

Generated data, downloaded runtimes, local credentials, logs, and Python packages
are excluded by `.gitignore`. No GitHub repository or push is required to run it.

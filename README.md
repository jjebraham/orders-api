# orders-api

[![CI](https://github.com/jjebraham/orders-api/actions/workflows/ci.yml/badge.svg)](https://github.com/jjebraham/orders-api/actions/workflows/ci.yml)
![Python 3.11](https://img.shields.io/badge/python-3.11-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

A small async FastAPI service for a fictional online shop. It manages products with stock and lets customers place orders that **reserve** that stock. The data is made up. The point is how the service behaves under load and retries: it never oversells, never double-reserves on a retried request, and never releases the same stock twice.

## The problem

Two shoppers click "buy" on the last unit at the same moment. A client retries a request after a timeout. A customer cancels an order twice. Each case can corrupt stock if the code is naive. This service handles all three:

- **Oversell:** stock is read and changed under a row lock (`SELECT ... FOR UPDATE`) in a single transaction. A database `CHECK (stock >= 0)` is a second safety net.
- **Retries:** `POST /orders` requires an `Idempotency-Key`. Replaying a key returns the original order and reserves nothing new.
- **Double release:** cancelling is idempotent. The stock goes back once, however many times the cancel is called.

## Architecture

```mermaid
flowchart LR
    Client[Client / curl] -->|HTTP JSON| API

    subgraph API [FastAPI app · uvicorn]
        MW[Request-ID + access log middleware]
        Routes[Routes: products, orders, health]
        Svc[Services: stock reservation logic]
        MW --> Routes --> Svc
    end

    Svc -->|one transaction per request<br/>SELECT ... FOR UPDATE| DB[(PostgreSQL 16<br/>or SQLite for local runs)]
    Alembic[Alembic migrations] -.->|upgrade head at container start| DB
```

Each request gets its own database session. The business rules live in `app/services.py`, and the routes only translate HTTP to and from those calls.

## Quick start (Docker)

Requirements: Docker with Compose.

```bash
docker compose up --build
```

This starts PostgreSQL and the API. The API runs `alembic upgrade head` before it starts. The API is at <http://localhost:8080> (the host port is 8080 so it does not clash with other local services). Interactive docs are at <http://localhost:8080/docs>.

## Run locally (without Docker)

Requirements: Python 3.11. Postgres is optional, because SQLite works for local runs.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # defaults to SQLite (./orders.db)

alembic upgrade head
uvicorn app.main:app --reload
```

To use Postgres instead, start one (for example `docker compose up -d postgres`), then set `DATABASE_URL` in `.env` to `postgresql+asyncpg://orders:orders@localhost:5432/orders`.

> **SQLite is for local development only.** It ignores `FOR UPDATE`, so the race-safety guarantees below only hold on Postgres. The concurrency tests are skipped on SQLite for this reason.

## Configuration

All configuration comes from environment variables (or `.env`). See `.env.example`.

| Variable       | Default                          | Meaning                                  |
|----------------|----------------------------------|------------------------------------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./orders.db` | SQLAlchemy async URL (Postgres: `postgresql+asyncpg://...`) |
| `LOG_LEVEL`    | `INFO`                           | Log level for the JSON logger            |
| `TEST_DATABASE_URL` | unset (uses a temp SQLite file) | Set it to run the test suite against Postgres |

## Running the tests

```bash
# Against Postgres, the same way CI runs them (start the Compose Postgres first):
docker compose up -d postgres
export TEST_DATABASE_URL=postgresql+asyncpg://orders:orders@127.0.0.1:5432/orders
pytest -v

# Quick run on SQLite (concurrency tests are skipped):
unset TEST_DATABASE_URL
pytest -q

# Lint, the same as CI:
ruff check . && ruff format --check .
```

The suite uses `pytest-asyncio` and `httpx.AsyncClient` over the ASGI transport, so it runs the real app with no network. It covers:

- **Concurrency (Postgres):** two orders racing for the last unit (exactly one gets `201`, the other `409`); 20 buyers for 5 units (exactly 5 succeed); 10 concurrent retries with one key (one order, stock taken once); 5 concurrent cancels (stock returned once).
- **Idempotency:** replay returns the same order and sets `Idempotent-Replayed: true`; a reused key with a different body is rejected with `422`.
- **Cancel:** releases stock once, repeat cancels return the same result, cancelled stock can be reserved again.
- **Pagination and filtering:** `limit`/`offset`, name filter (with `%` and `_` treated literally), out-of-range values rejected.
- **Validation and errors:** unknown fields, malformed JSON, missing `Idempotency-Key`, consistent error bodies with a `request_id`.
- **Request IDs and logging:** the ID is generated or echoed, and JSON log lines carry it.

CI also runs `alembic upgrade head` / `alembic check` / `downgrade base` on a fresh database, and builds the Docker image.

## API examples

Examples use the Compose port (`8080`). For a local `uvicorn` run, use `8000`.

Create a product:

```bash
curl -s -X POST localhost:8080/products \
  -H 'content-type: application/json' \
  -d '{"name":"Espresso beans 1kg","price_cents":2490,"stock":3}'
# {"id":1,"name":"Espresso beans 1kg","price_cents":2490,"stock":3}
```

List products (paginated, filtered by name):

```bash
curl -s "localhost:8080/products?limit=5&offset=0&name=espresso"
# {"items":[{"id":1,"name":"Espresso beans 1kg","price_cents":2490,"stock":3}],"total":1,"limit":5,"offset":0}
```

Place an order. The `Idempotency-Key` header is required. Use a fresh key per logical order and reuse the same key when retrying:

```bash
curl -s -i -X POST localhost:8080/orders \
  -H 'content-type: application/json' \
  -H 'Idempotency-Key: order-2026-11-24-0001' \
  -d '{"product_id":1,"quantity":2}'
# HTTP/1.1 201 Created
# {"id":1,"product_id":1,"quantity":2,"unit_price_cents":2490,"total_cents":4980,"status":"reserved",...}
```

Retry with the same key. You get the same order back, and stock is not reserved again:

```bash
curl -s -i -X POST localhost:8080/orders \
  -H 'content-type: application/json' \
  -H 'Idempotency-Key: order-2026-11-24-0001' \
  -d '{"product_id":1,"quantity":2}'
# HTTP/1.1 201 Created
# idempotent-replayed: true
```

Not enough stock returns `409`:

```bash
curl -s -X POST localhost:8080/orders \
  -H 'content-type: application/json' \
  -H 'Idempotency-Key: order-2026-11-24-0002' \
  -d '{"product_id":1,"quantity":5}'
# {"error":{"code":"insufficient_stock","message":"Only 1 unit(s) left, requested 5","details":[],"request_id":"..."}}
```

Cancel an order (releases its stock; calling it again is safe):

```bash
curl -s -X POST localhost:8080/orders/1/cancel
# {"id":1,...,"status":"cancelled","cancelled_at":"2026-11-24T18:20:15.022488Z"}
```

Errors always use the same shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": [{"field": "body.quantity", "message": "Input should be greater than or equal to 1"}],
    "request_id": "8b5bf2e4a8f04dd387d41d8415968d01"
  }
}
```

### Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Checks the database. `503` if it is down. |
| GET | `/products` | `limit` (1-100, default 20), `offset`, `name` (substring) |
| POST | `/products` | `name`, `price_cents`, `stock` |
| GET | `/products/{id}` | `404` if missing |
| POST | `/orders` | Requires `Idempotency-Key`. Body: `product_id`, `quantity` (1-100). `201`, `409` if out of stock. |
| GET | `/orders/{id}` | |
| POST | `/orders/{id}/cancel` | Releases stock once. Safe to repeat. |

Every response has an `X-Request-ID` header. Send your own to trace a request through the logs. Logs are one JSON object per line.

## Scope

This is a portfolio project for a fictional shop. It has **no authentication and no customer ownership checks**: anyone who can reach the API can create products, place orders and cancel any order. A real shop would need auth (for example OAuth2 or API keys) and per-customer authorization before exposing order creation and cancellation. Reservations also never expire, and there is no payment step.

## Design decisions

**Why `SELECT ... FOR UPDATE` on the product row.** Stock is a number that many requests change at once. A plain `SELECT` followed by an `UPDATE` lets two requests both read `stock = 1` and both succeed. Locking the product row makes the second request wait until the first commits, then it reads the new value and gets a `409`. The lock is held only for the length of one short transaction. The service locks in a fixed order (orders before products when cancelling, products alone when reserving), so two requests cannot deadlock each other. `CHECK (stock >= 0)` in the database is a backstop: if the application logic ever had a bug, the database would refuse to oversell. I checked this: with the lock removed, the two-buyers-one-unit test fails within a few runs.

**Why idempotency keys.** Clients retry. A timeout does not tell the client whether the server reserved stock, so a retry without a key can create a second order. The key is stored with the order and has a unique constraint, so the same key always maps to one order. The request body is hashed and stored too. A reused key with a different body is an error (`422`) and not a silent success. Two concurrent requests with the same key are handled by the lock: the second one re-checks for the key after it gets the lock and returns the first one's order. A unique-constraint violation is also caught as a fallback.

**Replay after cancel.** A retry with the same key returns the order in its *current* state. If it was cancelled in the meantime, the retry gets `201` with `status: "cancelled"` and does not reserve stock again. The retry does not replay the original response body.

**Why cancel is idempotent.** The order row is locked, and its status is checked under that lock. If it is already `cancelled`, the stock is left alone. Two cancels racing each other are serialized by the lock, so the stock comes back once.

**Why one product per order.** It keeps the stock logic easy to read and test. Multi-line orders would need one transaction over several product rows, locked in a sorted order to avoid deadlocks. That is a clear next step.

**Money as integers.** Prices are stored as `price_cents` (integers). Floats are not used for money.

**The order's price is a snapshot.** `unit_price_cents` is copied onto the order when it is placed, so later price changes do not change past orders.

**What would change at scale.**
- *Many app instances:* nothing changes in the stock logic, because the database lock is the coordination point. Keep the transactions short.
- *Hot products:* a single row becomes a bottleneck for a flash sale. Options are a reservation queue per product, or splitting stock across "buckets".
- *Idempotency keys:* move them to a cache with a TTL (Redis) or scope them per client. Keep the unique constraint in the database as the final guard.
- *Name search:* `ILIKE '%term%'` cannot use a normal index. At scale, use a `pg_trgm` GIN index or a search engine.
- *Migrations and deploys:* the container runs `alembic upgrade head` at startup. With several replicas, run migrations as a separate job first.
- *Payments and expiry:* reservations here never expire. A real shop would release stock for unpaid orders after a timeout, with a background job.

## Project layout

```
app/
  main.py          app factory (create_app), wiring
  config.py        env-based settings
  db.py            per-request session dependency
  models.py        SQLAlchemy models (Product, Order)
  schemas.py       Pydantic v2 request and response models
  services.py      stock reservation, idempotency, cancel logic
  routes/          HTTP routes (products, orders, health)
  errors.py        consistent JSON error responses
  middleware.py    request-ID and access-log middleware
  logging_config.py structured JSON logging
alembic/           migrations
tests/             pytest suite (Postgres in CI)
```

## CI

GitHub Actions runs on every push and pull request. The `test` job starts a Postgres 16 service container and runs `ruff`, the full `pytest` suite against that Postgres, and the migration checks on a fresh database. The `docker` job builds the image.

## License

MIT. See [LICENSE](LICENSE).

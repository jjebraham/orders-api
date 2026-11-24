import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models import Base

# Set TEST_DATABASE_URL to run against Postgres (CI does this). Without it, tests use a
# throwaway SQLite file. Concurrency tests are skipped on SQLite, see tests/test_concurrency.py.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def database_url(tmp_path) -> str:
    return TEST_DATABASE_URL or f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def is_postgres(database_url) -> bool:
    return database_url.startswith("postgresql")


@pytest.fixture
async def app(database_url):
    application = create_app(Settings(database_url=database_url, log_level="WARNING"))
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield application
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def create_product(client: AsyncClient, *, name="Widget", price=1000, stock=5) -> dict:
    response = await client.post(
        "/products", json={"name": name, "price_cents": price, "stock": stock}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def place_order(client: AsyncClient, key: str, product_id: int, quantity: int = 1):
    return await client.post(
        "/orders",
        json={"product_id": product_id, "quantity": quantity},
        headers={"Idempotency-Key": key},
    )


async def stock_of(client: AsyncClient, product_id: int) -> int:
    return (await client.get(f"/products/{product_id}")).json()["stock"]

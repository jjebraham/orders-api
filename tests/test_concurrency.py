import asyncio

import pytest

from tests.conftest import create_product, place_order, stock_of


@pytest.fixture(autouse=True)
def _require_postgres(is_postgres):
    # SQLite ignores SELECT ... FOR UPDATE, so these races are only meaningful on Postgres.
    if not is_postgres:
        pytest.skip("needs Postgres: set TEST_DATABASE_URL (see docker-compose.yml)")


async def test_two_orders_racing_for_last_unit_exactly_one_wins(client):
    product = await create_product(client, stock=1)

    responses = await asyncio.gather(
        place_order(client, "racer-a", product["id"]),
        place_order(client, "racer-b", product["id"]),
    )

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [201, 409]
    assert await stock_of(client, product["id"]) == 0


async def test_many_concurrent_buyers_never_oversell(client):
    product = await create_product(client, stock=5)

    responses = await asyncio.gather(
        *(place_order(client, f"buyer-{i}", product["id"]) for i in range(20))
    )

    assert sum(r.status_code == 201 for r in responses) == 5
    assert sum(r.status_code == 409 for r in responses) == 15
    assert await stock_of(client, product["id"]) == 0


async def test_concurrent_retries_with_same_key_reserve_once(client):
    product = await create_product(client, stock=5)

    responses = await asyncio.gather(
        *(place_order(client, "same-key", product["id"], quantity=2) for _ in range(10))
    )

    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert await stock_of(client, product["id"]) == 3


async def test_concurrent_cancels_release_stock_once(client):
    product = await create_product(client, stock=5)
    order = (await place_order(client, "to-cancel", product["id"], quantity=3)).json()

    responses = await asyncio.gather(
        *(client.post(f"/orders/{order['id']}/cancel") for _ in range(5))
    )

    assert all(r.status_code == 200 for r in responses)
    assert await stock_of(client, product["id"]) == 5

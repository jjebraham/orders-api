from tests.conftest import create_product, place_order, stock_of


async def test_order_reserves_stock(client):
    product = await create_product(client, price=500, stock=10)

    response = await place_order(client, "order-1", product["id"], quantity=3)
    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "reserved"
    assert order["quantity"] == 3
    assert order["unit_price_cents"] == 500
    assert order["total_cents"] == 1500
    assert order["cancelled_at"] is None
    assert await stock_of(client, product["id"]) == 7


async def test_get_order(client):
    product = await create_product(client)
    created = (await place_order(client, "order-get", product["id"])).json()

    fetched = await client.get(f"/orders/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]


async def test_get_missing_order_is_404(client):
    response = await client.get("/orders/424242")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "order_not_found"


async def test_order_requires_idempotency_key(client):
    product = await create_product(client)
    response = await client.post("/orders", json={"product_id": product["id"], "quantity": 1})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"
    assert await stock_of(client, product["id"]) == 5


async def test_insufficient_stock_returns_409_and_keeps_stock(client):
    product = await create_product(client, stock=2)

    response = await place_order(client, "too-many", product["id"], quantity=3)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "insufficient_stock"
    assert "2 unit(s) left" in error["message"]
    assert await stock_of(client, product["id"]) == 2


async def test_order_for_unknown_product_is_404(client):
    response = await place_order(client, "ghost", 999999)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "product_not_found"


async def test_quantity_must_be_positive(client):
    product = await create_product(client)
    response = await place_order(client, "zero", product["id"], quantity=0)
    assert response.status_code == 422


async def test_retry_with_same_key_returns_same_order_without_double_reserving(client):
    product = await create_product(client, stock=5)

    first = await place_order(client, "retry-key", product["id"], quantity=2)
    retry = await place_order(client, "retry-key", product["id"], quantity=2)

    assert first.status_code == 201
    assert retry.status_code == 201
    assert retry.headers["idempotent-replayed"] == "true"
    assert "idempotent-replayed" not in first.headers
    assert retry.json()["id"] == first.json()["id"]
    assert await stock_of(client, product["id"]) == 3


async def test_reusing_key_with_different_body_is_rejected(client):
    product = await create_product(client, stock=5)
    await place_order(client, "shared-key", product["id"], quantity=1)

    response = await place_order(client, "shared-key", product["id"], quantity=2)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_reused"
    assert await stock_of(client, product["id"]) == 4


async def test_cancel_releases_stock(client):
    product = await create_product(client, stock=5)
    order = (await place_order(client, "cancel-me", product["id"], quantity=2)).json()

    response = await client.post(f"/orders/{order['id']}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["cancelled_at"] is not None
    assert await stock_of(client, product["id"]) == 5


async def test_cancel_is_idempotent_and_releases_stock_once(client):
    product = await create_product(client, stock=5)
    order = (await place_order(client, "cancel-twice", product["id"], quantity=2)).json()

    first = await client.post(f"/orders/{order['id']}/cancel")
    second = await client.post(f"/orders/{order['id']}/cancel")

    assert first.status_code == second.status_code == 200
    assert second.json()["status"] == "cancelled"
    assert second.json()["cancelled_at"] == first.json()["cancelled_at"]
    assert await stock_of(client, product["id"]) == 5


async def test_cancel_unknown_order_is_404(client):
    response = await client.post("/orders/777777/cancel")
    assert response.status_code == 404


async def test_cancelled_stock_can_be_reserved_again(client):
    product = await create_product(client, stock=1)
    order = (await place_order(client, "first", product["id"])).json()
    assert (await place_order(client, "second", product["id"])).status_code == 409

    await client.post(f"/orders/{order['id']}/cancel")
    assert (await place_order(client, "second", product["id"])).status_code == 201


async def test_replay_after_cancel_returns_current_cancelled_state(client):
    product = await create_product(client, stock=5)
    first = (await place_order(client, "cancelled-then-retried", product["id"], 2)).json()
    await client.post(f"/orders/{first['id']}/cancel")

    retry = await place_order(client, "cancelled-then-retried", product["id"], 2)

    assert retry.status_code == 201
    assert retry.headers["idempotent-replayed"] == "true"
    body = retry.json()
    assert body["id"] == first["id"]
    assert body["status"] == "cancelled"
    assert body["cancelled_at"] is not None
    # The retry must not reserve stock again after the order was cancelled.
    assert await stock_of(client, product["id"]) == 5

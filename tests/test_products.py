from tests.conftest import create_product


async def test_create_and_get_product(client):
    created = await create_product(client, name="Espresso beans", price=2490, stock=25)
    assert created == {
        "id": created["id"],
        "name": "Espresso beans",
        "price_cents": 2490,
        "stock": 25,
    }

    fetched = await client.get(f"/products/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created


async def test_missing_product_returns_consistent_error(client):
    response = await client.get("/products/987654")
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "product_not_found"
    assert body["message"]
    assert body["details"] == []
    assert body["request_id"]


async def test_list_products_paginates(client):
    for i in range(5):
        await create_product(client, name=f"Item {i}")

    first = (await client.get("/products", params={"limit": 2})).json()
    assert first["total"] == 5
    assert [p["name"] for p in first["items"]] == ["Item 0", "Item 1"]
    assert first["limit"] == 2 and first["offset"] == 0

    third_page = (await client.get("/products", params={"limit": 2, "offset": 4})).json()
    assert [p["name"] for p in third_page["items"]] == ["Item 4"]
    assert third_page["total"] == 5


async def test_list_products_filters_by_name(client):
    await create_product(client, name="Green tea")
    await create_product(client, name="Black tea")
    await create_product(client, name="Coffee filter")

    page = (await client.get("/products", params={"name": "TEA"})).json()
    assert page["total"] == 2
    assert {p["name"] for p in page["items"]} == {"Green tea", "Black tea"}


async def test_name_filter_treats_wildcards_literally(client):
    await create_product(client, name="50% off mug")
    await create_product(client, name="Plain mug")

    page = (await client.get("/products", params={"name": "%"})).json()
    assert page["total"] == 1
    assert page["items"][0]["name"] == "50% off mug"


async def test_invalid_pagination_returns_422_with_details(client):
    response = await client.get("/products", params={"limit": 0})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["field"] == "query.limit"


async def test_limit_above_maximum_is_rejected(client):
    response = await client.get("/products", params={"limit": 101})
    assert response.status_code == 422


async def test_negative_offset_is_rejected(client):
    response = await client.get("/products", params={"offset": -1})
    assert response.status_code == 422


async def test_create_product_validates_body(client):
    response = await client.post("/products", json={"name": "   ", "price_cents": -5, "stock": 1})
    assert response.status_code == 422
    fields = {d["field"] for d in response.json()["error"]["details"]}
    assert {"body.name", "body.price_cents"} <= fields


async def test_create_product_rejects_unknown_fields(client):
    response = await client.post(
        "/products", json={"name": "Mug", "price_cents": 100, "stock": 1, "is_admin": True}
    )
    assert response.status_code == 422


async def test_create_product_rejects_malformed_json(client):
    response = await client.post(
        "/products", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_unknown_route_uses_error_shape(client):
    response = await client.get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"

import re


async def test_health_reports_database_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


async def test_request_id_is_generated_when_missing(client):
    response = await client.get("/health")
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


async def test_request_id_from_client_is_echoed(client):
    response = await client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert response.headers["x-request-id"] == "trace-abc-123"


async def test_unsafe_request_id_is_replaced(client):
    response = await client.get("/health", headers={"X-Request-ID": "bad id with spaces"})
    assert response.headers["x-request-id"] != "bad id with spaces"


async def test_error_body_carries_request_id(client):
    response = await client.get("/products/999999", headers={"X-Request-ID": "req-42"})
    assert response.json()["error"]["request_id"] == "req-42"

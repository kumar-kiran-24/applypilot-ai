"""Health endpoint tests."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_returns_expected_payload(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "ApplyPilot AI Backend",
        "version": "0.1.0",
    }


async def test_health_is_exposed_in_openapi(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    assert "/api/v1/health" in schema["paths"]
    assert schema["info"]["title"] == "ApplyPilot AI Backend"


async def test_unknown_route_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404

"""Health endpoint tests."""

from __future__ import annotations

from app.core.config import settings
from httpx import AsyncClient


async def test_health_returns_expected_payload(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }


async def test_health_is_exposed_in_openapi(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    assert "/api/v1/health" in schema["paths"]
    assert schema["info"]["title"] == settings.APP_NAME


async def test_unknown_route_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import get_session
from app.main import app


@pytest.mark.asyncio
async def test_health_and_patient_api(session):
    async def override_session():
        yield session
    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            health = await client.get("/api/v1/health")
            assert health.status_code == 200
            await session.rollback()
            created = await client.post("/api/v1/patients", json={
                "full_name": "meera kapoor", "phone": "+919777777777",
                "preferred_language": "hi-IN",
            })
            assert created.status_code == 201
            assert created.json()["full_name"] == "Meera Kapoor"
    finally:
        app.dependency_overrides.clear()

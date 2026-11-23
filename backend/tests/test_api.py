from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings


client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_root_endpoint() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "status": "online",
        "assistant": settings.app_name,
        "version": settings.app_version,
    }

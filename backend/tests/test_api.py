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


def test_chat_endpoint_returns_placeholder_reply() -> None:
    response = client.post("/api/chat", json={"message": "Hello, Winston"})

    assert response.status_code == 200
    assert response.json() == {
        "reply": "I received: Hello, Winston\n\nWinston's AI model is not connected yet.",
        "mode": "placeholder",
    }


def test_chat_endpoint_rejects_blank_message() -> None:
    response = client.post("/api/chat", json={"message": "   "})

    assert response.status_code == 422


def test_chat_endpoint_rejects_message_over_limit() -> None:
    response = client.post("/api/chat", json={"message": "x" * 4001})

    assert response.status_code == 422

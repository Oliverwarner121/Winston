from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_winston_ai_service
from app.core.config import Settings, settings
from app.main import app
from app.services.ai_service import (
    AIProviderResponseError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)


class FakeAIService:
    async def respond(self, message: str) -> str:
        return "Hello Oliver, I am Winston."


class FailingAIService:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def respond(self, message: str) -> str:
        raise self._error


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[get_winston_ai_service] = FakeAIService
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_root_endpoint(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "status": "online",
        "assistant": settings.app_name,
        "version": settings.app_version,
    }


def test_chat_endpoint_returns_ai_response(client: TestClient) -> None:
    response = client.post("/chat", json={"message": "Who are you?"})

    assert response.status_code == 200
    assert response.json() == {"response": "Hello Oliver, I am Winston."}


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (
            AIProviderUnavailableError(),
            503,
            "The AI model service is unavailable.",
        ),
        (
            AIProviderTimeoutError(),
            504,
            "The AI model request timed out.",
        ),
        (
            AIProviderResponseError(),
            502,
            "The AI model returned an invalid response.",
        ),
    ],
)
def test_chat_endpoint_maps_provider_errors(
    client: TestClient,
    error: Exception,
    status_code: int,
    detail: str,
) -> None:
    app.dependency_overrides[get_winston_ai_service] = lambda: FailingAIService(error)

    response = client.post("/chat", json={"message": "Hello"})

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}


@pytest.mark.parametrize(
    "message",
    ["   ", "x" * 4001],
)
def test_chat_endpoint_rejects_invalid_message(
    client: TestClient,
    message: str,
) -> None:
    response = client.post("/chat", json={"message": message})

    assert response.status_code == 422


def test_ollama_settings_use_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b-test")

    configured = Settings(_env_file=None)

    assert configured.ollama_url == "http://127.0.0.1:11435"
    assert configured.ollama_model == "qwen2.5:7b-test"

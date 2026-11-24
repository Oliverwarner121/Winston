from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_winston_ai_service
from app.core.config import Settings, ToolSettings, settings
from app.main import app
from app.services.ai_service import (
    AIProviderResponseError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)
from app.tools.permissions import PermissionLevel
from app.tools.runtime import create_default_tool_runtime


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


def test_tool_runtime_registers_clock_with_default_deny(client: TestClient) -> None:
    runtime = client.app.state.tool_runtime

    tool_ids = {tool.id for tool in runtime.registry.list_tools()}
    assert {
        "utc_clock",
        "file_search",
        "file_read",
        "file_write",
        "file_move",
        "open_vscode",
        "git",
        "terminal",
        "system_info",
    }.issubset(tool_ids)
    assert ("web_search" in tool_ids) == settings.tools.web_search_enabled
    assert ("browser_read" in tool_ids) == bool(settings.tools.browser_allowed_hosts)
    assert ("docker_manage" in tool_ids) == bool(settings.tools.docker_allowed_containers)
    assert not runtime.permission_manager.is_allowed("untrusted", PermissionLevel.READ)
    assert (client.app.state.tool_agent is not None) == settings.features.tools_enabled


def test_optional_tools_register_only_when_explicitly_configured() -> None:
    runtime = create_default_tool_runtime(
        ToolSettings(
            web_search_enabled=True,
            browser_allowed_hosts=["docs.python.org"],
            docker_allowed_containers=["vaultwarden"],
        )
    )
    tool_ids = {tool.id for tool in runtime.registry.list_tools()}

    assert {"web_search", "browser_read", "docker_status", "docker_manage"} <= tool_ids
    assert runtime.registry.list_tools(category="BROWSER")
    assert runtime.registry.list_tools(category="HOMELAB")


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


def test_tool_settings_use_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLS__WEB_SEARCH_ENABLED", "true")
    monkeypatch.setenv("TOOLS__BROWSER_ALLOWED_HOSTS", '["docs.python.org"]')
    monkeypatch.setenv("TOOLS__DOCKER_ALLOWED_CONTAINERS", '["vaultwarden"]')

    configured = Settings(_env_file=None)

    assert configured.tools.web_search_enabled is True
    assert configured.tools.browser_allowed_hosts == ["docs.python.org"]
    assert configured.tools.docker_allowed_containers == ["vaultwarden"]

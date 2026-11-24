from collections.abc import Iterator
from pathlib import Path

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
from app.services.code_proposals import CodeActionResult
from app.services.tool_agent import (
    AgentRunResponse,
    AgentRunStatus,
    AgentStep,
)
from app.tools.models import ToolExecutionStatus
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


class FakeToolAgent:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.confirmations: list[tuple[str, bool]] = []

    async def run(self, message: str, *, actor_id: str) -> AgentRunResponse:
        self.requests.append((message, actor_id))
        return AgentRunResponse(
            status=AgentRunStatus.CONFIRMATION_REQUIRED,
            message="Approval is required.",
            confirmation_id="fbb15134-89e2-459b-9781-bfe130589c98",
            confirmation_message="Open Intel-Agent in VS Code?",
        )

    async def confirm(self, confirmation_id, *, actor_id: str) -> AgentRunResponse:
        self.confirmations.append((str(confirmation_id), True))
        return AgentRunResponse(
            status=AgentRunStatus.COMPLETED,
            message="Intel-Agent launched successfully.",
        )

    def cancel(self, confirmation_id, *, actor_id: str) -> AgentRunResponse:
        self.confirmations.append((str(confirmation_id), False))
        return AgentRunResponse(
            status=AgentRunStatus.CONFIRMATION_CANCELLED,
            message="Confirmation cancelled.",
        )


class FakeCodeProposalAgent(FakeToolAgent):
    async def run(self, message: str, *, actor_id: str) -> AgentRunResponse:
        return AgentRunResponse(
            status=AgentRunStatus.COMPLETED,
            message="The requested tool result is ready for review.",
            steps=[
                AgentStep(
                    tool_id="code_proposal",
                    status=ToolExecutionStatus.SUCCEEDED,
                    result={
                        "proposal_id": "fbb15134-89e2-459b-9781-bfe130589c98",
                        "summary": "Add a new capability",
                        "diff": "diff --git a/backend/app/new.py b/backend/app/new.py",
                        "files": ["backend/app/new.py"],
                    },
                    confirmed=False,
                )
            ],
        )


class FakeCodeProposalManager:
    async def apply(self, proposal_id, *, actor_id) -> CodeActionResult:
        return CodeActionResult(
            proposal_id=proposal_id,
            summary="Add a new capability",
            diff="diff preview",
            files=["backend/app/new.py"],
            message="Patch applied. Approve a backend test run when ready.",
            status="applied",
        )

    def discard(self, proposal_id, *, actor_id) -> CodeActionResult:
        return CodeActionResult(
            proposal_id=proposal_id,
            summary="Add a new capability",
            diff="diff preview",
            files=["backend/app/new.py"],
            message="Code proposal discarded; no files were changed.",
            status="discarded",
        )

    async def run_tests(self, proposal_id, *, actor_id) -> CodeActionResult:
        return CodeActionResult(
            proposal_id=proposal_id,
            summary="Add a new capability",
            diff="diff preview",
            files=["backend/app/new.py"],
            message="Backend tests passed.",
            status="tested",
            output="2 passed",
            tests_passed=True,
        )


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


def test_chat_uses_tool_agent_and_returns_confirmation() -> None:
    app.dependency_overrides[get_winston_ai_service] = FakeAIService
    agent = FakeToolAgent()
    try:
        with TestClient(app, client=("127.0.0.1", 9000)) as local_client:
            local_client.app.state.tool_agent = agent
            response = local_client.post(
                "/chat",
                json={"message": "Open Intel-Agent"},
                headers={"Origin": "http://localhost:5173"},
            )
    finally:
        app.dependency_overrides.clear()
        app.state.tool_agent = None

    assert response.status_code == 200
    assert response.json() == {
        "response": "Open Intel-Agent in VS Code?",
        "confirmation_id": "fbb15134-89e2-459b-9781-bfe130589c98",
        "confirmation_message": "Open Intel-Agent in VS Code?",
    }
    assert agent.requests == [("Open Intel-Agent", "winston-local-user")]


def test_non_loopback_chat_cannot_invoke_tools() -> None:
    agent = FakeToolAgent()
    try:
        with TestClient(app, client=("192.0.2.10", 9000)) as remote_client:
            remote_client.app.state.tool_agent = agent
            response = remote_client.post(
                "/chat",
                json={"message": "Open Intel-Agent"},
            )
    finally:
        app.state.tool_agent = None

    assert response.status_code == 403
    assert agent.requests == []


def test_confirmation_endpoint_approves_or_cancels_locally() -> None:
    agent = FakeToolAgent()
    confirmation_id = "fbb15134-89e2-459b-9781-bfe130589c98"
    try:
        with TestClient(app, client=("127.0.0.1", 9000)) as local_client:
            local_client.app.state.tool_agent = agent
            response = local_client.post(
                f"/chat/confirmations/{confirmation_id}",
                json={"approved": True},
            )
    finally:
        app.state.tool_agent = None

    assert response.status_code == 200
    assert response.json() == {"response": "Intel-Agent launched successfully."}
    assert agent.confirmations == [(confirmation_id, True)]


def test_confirmation_endpoint_can_cancel_locally() -> None:
    agent = FakeToolAgent()
    confirmation_id = "fbb15134-89e2-459b-9781-bfe130589c98"
    try:
        with TestClient(app, client=("127.0.0.1", 9000)) as local_client:
            local_client.app.state.tool_agent = agent
            response = local_client.post(
                f"/chat/confirmations/{confirmation_id}",
                json={"approved": False},
            )
    finally:
        app.state.tool_agent = None

    assert response.status_code == 200
    assert response.json() == {"response": "Confirmation cancelled."}
    assert agent.confirmations == [(confirmation_id, False)]


def test_non_loopback_peer_cannot_approve_tool_action() -> None:
    agent = FakeToolAgent()
    try:
        with TestClient(app, client=("192.0.2.10", 9000)) as remote_client:
            remote_client.app.state.tool_agent = agent
            response = remote_client.post(
                "/chat/confirmations/fbb15134-89e2-459b-9781-bfe130589c98",
                json={"approved": True},
            )
    finally:
        app.state.tool_agent = None

    assert response.status_code == 403
    assert agent.confirmations == []


def test_non_loopback_origin_cannot_use_local_proxy_for_tool_chat() -> None:
    agent = FakeToolAgent()
    try:
        with TestClient(app, client=("127.0.0.1", 9000)) as local_client:
            local_client.app.state.tool_agent = agent
            response = local_client.post(
                "/chat",
                json={"message": "Open Intel-Agent"},
                headers={"Origin": "http://192.0.2.10:5173"},
            )
    finally:
        app.state.tool_agent = None

    assert response.status_code == 403
    assert agent.requests == []


def test_chat_returns_code_diff_as_a_reviewable_proposal() -> None:
    app.dependency_overrides[get_winston_ai_service] = FakeAIService
    try:
        with TestClient(app, client=("127.0.0.1", 9000)) as local_client:
            local_client.app.state.tool_agent = FakeCodeProposalAgent()
            response = local_client.post(
                "/chat",
                json={"message": "Add a new capability"},
                headers={"Origin": "http://127.0.0.1:5173"},
            )
    finally:
        app.dependency_overrides.clear()
        app.state.tool_agent = None

    assert response.status_code == 200
    assert response.json()["proposal_summary"] == "Add a new capability"
    assert response.json()["proposal_status"] == "pending"
    assert response.json()["proposal_files"] == ["backend/app/new.py"]


def test_code_proposal_apply_and_test_endpoints_are_separate_actions() -> None:
    proposal_id = "fbb15134-89e2-459b-9781-bfe130589c98"
    try:
        with TestClient(app, client=("127.0.0.1", 9000)) as local_client:
            local_client.app.state.code_proposal_manager = FakeCodeProposalManager()
            applied = local_client.post(
                f"/chat/code-proposals/{proposal_id}/apply",
                headers={"Origin": "http://127.0.0.1:5173"},
            )
            tested = local_client.post(
                f"/chat/code-proposals/{proposal_id}/tests",
                headers={"Origin": "http://127.0.0.1:5173"},
            )
    finally:
        app.state.code_proposal_manager = None

    assert applied.status_code == 200
    assert applied.json()["proposal_status"] == "applied"
    assert tested.status_code == 200
    assert tested.json()["proposal_status"] == "tested"
    assert tested.json()["tests_passed"] is True


def test_remote_peer_cannot_apply_a_code_proposal() -> None:
    manager = FakeCodeProposalManager()
    try:
        with TestClient(app, client=("192.0.2.10", 9000)) as remote_client:
            remote_client.app.state.code_proposal_manager = manager
            response = remote_client.post(
                "/chat/code-proposals/fbb15134-89e2-459b-9781-bfe130589c98/apply",
            )
    finally:
        app.state.code_proposal_manager = None

    assert response.status_code == 403


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


def test_project_roots_use_json_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TOOLS__PROJECT_ROOTS",
        '["/home/oliverwarner121/Documents/Projects"]',
    )

    configured = Settings(_env_file=None)

    assert configured.tools.project_roots == [
        Path("/home/oliverwarner121/Documents/Projects")
    ]

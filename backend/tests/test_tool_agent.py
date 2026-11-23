import asyncio
from typing import ClassVar

from pydantic import BaseModel

from app.services.tool_agent import AgentRunStatus, ToolAgent
from app.tools.audit import AuditLogger
from app.tools.base import Tool, ToolContext
from app.tools.executor import ToolExecutor
from app.tools.models import ToolExecutionStatus
from app.tools.permissions import PermissionLevel, PermissionManager
from app.tools.registry import ToolRegistry


class CountInput(BaseModel):
    value: int


class CountOutput(BaseModel):
    value: int


class CountTool(Tool[CountInput, CountOutput]):
    id = "count"
    name = "Count"
    description = "Return the input value."
    permission_level = PermissionLevel.READ
    input_model = CountInput
    output_model = CountOutput

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, inputs: CountInput, context: ToolContext) -> CountOutput:
        self.calls += 1
        return CountOutput(value=inputs.value)


class SaveInput(BaseModel):
    value: str


class SaveOutput(BaseModel):
    saved: bool


class SaveTool(Tool[SaveInput, SaveOutput]):
    id = "save"
    name = "Save"
    description = "Save a value."
    permission_level = PermissionLevel.WRITE
    requires_confirmation = True
    input_model = SaveInput
    output_model = SaveOutput
    audit_safe_input_fields: ClassVar[frozenset[str]] = frozenset({"value"})

    def __init__(self) -> None:
        self.calls: list[str] = []

    def confirmation_message(self, inputs: SaveInput) -> str:
        return f"Save {inputs.value}?"

    async def execute(self, inputs: SaveInput, context: ToolContext) -> SaveOutput:
        self.calls.append(inputs.value)
        return SaveOutput(saved=True)


class QueueProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    async def generate_response(self, *, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append(user_prompt)
        return self.responses.pop(0)


def build_agent(
    tool: Tool,
    provider: QueueProvider,
    permissions: PermissionManager,
    *,
    max_steps: int = 3,
) -> ToolAgent:
    registry = ToolRegistry()
    registry.register(tool)
    audit_logger = AuditLogger()
    executor = ToolExecutor(registry, permissions, audit_logger)
    return ToolAgent(
        provider,
        registry,
        executor,
        permissions,
        max_steps=max_steps,
    )


def test_agent_executes_only_registered_permitted_tools() -> None:
    tool = CountTool()
    provider = QueueProvider(
        [
            '{"tool_id":"count","inputs":{"value":7},"message":""}',
            '{"tool_id":null,"inputs":{},"message":"The value is 7."}',
        ]
    )
    permissions = PermissionManager({"alice": [PermissionLevel.READ]})
    agent = build_agent(tool, provider, permissions)

    response = asyncio.run(agent.run("Return seven", actor_id="alice"))

    assert response.status == AgentRunStatus.COMPLETED
    assert response.message == "The value is 7."
    assert response.steps[0].result == {"value": 7}
    assert tool.calls == 1
    assert '"value": 7' in provider.prompts[1]


def test_agent_pauses_for_confirmation_and_binds_approval_to_actor() -> None:
    tool = SaveTool()
    provider = QueueProvider(
        [
            '{"tool_id":"save","inputs":{"value":"report"},"message":""}',
            '{"tool_id":null,"inputs":{},"message":"Saved the report."}',
        ]
    )
    permissions = PermissionManager({"alice": [PermissionLevel.WRITE]})
    agent = build_agent(tool, provider, permissions)

    pending = asyncio.run(agent.run("Save report", actor_id="alice"))
    assert pending.status == AgentRunStatus.CONFIRMATION_REQUIRED
    assert pending.confirmation_message == "Save report?"
    assert tool.calls == []
    assert pending.confirmation_id is not None

    denied = asyncio.run(
        agent.confirm(pending.confirmation_id, actor_id="mallory")
    )
    assert denied.status == AgentRunStatus.FAILED
    assert tool.calls == []

    completed = asyncio.run(
        agent.confirm(pending.confirmation_id, actor_id="alice")
    )
    assert completed.status == AgentRunStatus.COMPLETED
    assert completed.message == "Saved the report."
    assert completed.steps[0].confirmed is True
    assert tool.calls == ["report"]


def test_agent_does_not_execute_tools_without_grants() -> None:
    tool = CountTool()
    provider = QueueProvider(
        ['{"tool_id":"count","inputs":{"value":1},"message":""}']
    )
    permissions = PermissionManager()
    agent = build_agent(tool, provider, permissions)

    response = asyncio.run(agent.run("Return one", actor_id="alice"))

    assert response.status == AgentRunStatus.FAILED
    assert response.message == "The model selected a tool that is not available."
    assert tool.calls == 0


def test_agent_stops_at_configured_step_limit() -> None:
    tool = CountTool()
    provider = QueueProvider(
        [
            '{"tool_id":"count","inputs":{"value":1},"message":""}',
            '{"tool_id":"count","inputs":{"value":2},"message":""}',
        ]
    )
    permissions = PermissionManager({"alice": [PermissionLevel.READ]})
    agent = build_agent(tool, provider, permissions, max_steps=2)

    response = asyncio.run(agent.run("Count twice", actor_id="alice"))

    assert response.status == AgentRunStatus.STEP_LIMIT_REACHED
    assert response.message == "Stopped at the configured tool-step limit."
    assert len(response.steps) == 2
    assert all(step.status == ToolExecutionStatus.SUCCEEDED for step in response.steps)
    assert tool.calls == 2


def test_agent_rejects_malformed_model_plans() -> None:
    tool = CountTool()
    provider = QueueProvider(["not JSON"])
    permissions = PermissionManager({"alice": [PermissionLevel.READ]})
    agent = build_agent(tool, provider, permissions)

    response = asyncio.run(agent.run("Return one", actor_id="alice"))

    assert response.status == AgentRunStatus.FAILED
    assert response.message == "The model returned an invalid tool plan."
    assert tool.calls == 0

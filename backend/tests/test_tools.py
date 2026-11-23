import asyncio
import json
import logging
from datetime import timedelta
from typing import ClassVar
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict

from app.tools.audit import AuditLogger
from app.tools.base import Tool, ToolContext
from app.tools.executor import ToolExecutor
from app.tools.implementations.clock import ClockTool
from app.tools.models import ToolExecutionStatus
from app.tools.permissions import PermissionLevel, PermissionManager
from app.tools.registry import (
    DuplicateToolError,
    ToolNotFoundError,
    ToolRegistry,
)


class AddInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: int
    right: int


class AddOutput(BaseModel):
    total: int


class AddTool(Tool[AddInput, AddOutput]):
    id = "add"
    name = "Add"
    description = "Add two numbers."
    category = "math"
    permission_level = PermissionLevel.READ
    input_model = AddInput
    output_model = AddOutput
    audit_safe_input_fields: ClassVar[frozenset[str]] = frozenset({"left", "right"})

    async def execute(self, inputs: AddInput, context: ToolContext) -> AddOutput:
        return AddOutput(total=inputs.left + inputs.right)


class WriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class WriteOutput(BaseModel):
    saved: bool


class ConfirmingTool(Tool[WriteInput, WriteOutput]):
    id = "save_item"
    name = "Save item"
    description = "Save an item."
    category = "write"
    permission_level = PermissionLevel.WRITE
    requires_confirmation = True
    input_model = WriteInput
    output_model = WriteOutput
    audit_safe_input_fields: ClassVar[frozenset[str]] = frozenset({"value"})

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def confirmation_message(self, inputs: WriteInput) -> str:
        return f"Save {inputs.value!r}?"

    async def execute(
        self,
        inputs: WriteInput,
        context: ToolContext,
    ) -> WriteOutput:
        self.calls.append((inputs.value, context.confirmed))
        return WriteOutput(saved=True)


class SensitiveInput(BaseModel):
    secret: str


class FailingOutput(BaseModel):
    result: str


class FailingTool(Tool[SensitiveInput, FailingOutput]):
    id = "failure"
    name = "Failure"
    description = "Raises an error for testing."
    input_model = SensitiveInput
    output_model = FailingOutput

    async def execute(
        self,
        inputs: SensitiveInput,
        context: ToolContext,
    ) -> FailingOutput:
        raise RuntimeError("sensitive exception details")


class DuplicateNameTool(AddTool):
    id = "another_add"


def make_executor(
    tool: Tool,
    permission_manager: PermissionManager | None = None,
    confirmation_ttl: timedelta = timedelta(minutes=5),
) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(
        registry=registry,
        permission_manager=permission_manager or PermissionManager(),
        audit_logger=AuditLogger(),
        confirmation_ttl=confirmation_ttl,
    )


def get_audit_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        json.loads(record.getMessage().removeprefix("tool_execution "))
        for record in caplog.records
        if record.name == "winston.audit"
    ]


def test_registry_registers_and_discovers_tool() -> None:
    registry = ToolRegistry()
    registry.register(AddTool())

    definitions = registry.list_tools(category="math")
    assert [definition.id for definition in definitions] == ["add"]
    assert "left" in definitions[0].input_schema["properties"]

    with pytest.raises(DuplicateToolError):
        registry.register(AddTool())
    with pytest.raises(DuplicateToolError):
        registry.register(DuplicateNameTool())
    with pytest.raises(ToolNotFoundError):
        registry.get("missing")


def test_permissions_normalize_actor_ids_for_grants_and_revocation() -> None:
    permissions = PermissionManager()
    permissions.grant(" alice ", PermissionLevel.READ)

    assert permissions.is_allowed("alice", PermissionLevel.READ)
    permissions.revoke(" alice ", PermissionLevel.READ)
    assert not permissions.is_allowed("alice", PermissionLevel.READ)


def test_executor_requires_permission_and_validates_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    permissions = PermissionManager()
    executor = make_executor(AddTool(), permissions)
    caplog.set_level(logging.INFO, logger="winston.audit")

    denied = asyncio.run(
        executor.request_execution(
            "add",
            {"left": 2, "right": 3},
            actor_id="alice",
            user_request="Add two values",
        )
    )
    assert denied.status == ToolExecutionStatus.PERMISSION_DENIED

    permissions.grant("alice", PermissionLevel.READ)
    invalid = asyncio.run(
        executor.request_execution(
            "add",
            {"left": 2, "right": 3, "unexpected": True},
            actor_id="alice",
            user_request="Add two values",
        )
    )
    assert invalid.status == ToolExecutionStatus.INVALID_INPUT

    success = asyncio.run(
        executor.request_execution(
            "add",
            {"left": 2, "right": 3},
            actor_id="alice",
            user_request="Add two values",
        )
    )
    assert success.success is True
    assert success.result == {"total": 5}
    assert [event["status"] for event in get_audit_events(caplog)] == [
        "permission_denied",
        "invalid_input",
        "succeeded",
    ]


def test_executor_redacts_inputs_and_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    permissions = PermissionManager({"alice": [PermissionLevel.READ]})
    executor = make_executor(FailingTool(), permissions)
    caplog.set_level(logging.INFO, logger="winston.audit")

    response = asyncio.run(
        executor.request_execution(
            "failure",
            {"secret": "private-value"},
            actor_id="alice",
            user_request="Test failure",
        )
    )

    assert response.status == ToolExecutionStatus.FAILED
    assert response.error == "Tool execution failed (RuntimeError)."
    assert "sensitive exception details" not in response.error
    audit_event = get_audit_events(caplog)[0]
    assert audit_event["inputs"] == {"secret": "[REDACTED]"}
    assert "private-value" not in caplog.text
    assert "sensitive exception details" not in caplog.text


def test_confirmation_is_actor_bound_and_single_use(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tool = ConfirmingTool()
    permissions = PermissionManager({"alice": [PermissionLevel.WRITE]})
    executor = make_executor(tool, permissions)
    caplog.set_level(logging.INFO, logger="winston.audit")

    pending = asyncio.run(
        executor.request_execution(
            "save_item",
            {"value": "report"},
            actor_id="alice",
            user_request="Save the report",
        )
    )
    assert pending.status == ToolExecutionStatus.CONFIRMATION_REQUIRED
    assert pending.confirmation_message == "Save 'report'?"
    assert tool.calls == []

    confirmation_id: UUID
    assert pending.confirmation_id is not None
    confirmation_id = pending.confirmation_id
    denied = asyncio.run(
        executor.confirm_execution(confirmation_id, actor_id="mallory")
    )
    assert denied.status == ToolExecutionStatus.PERMISSION_DENIED
    assert tool.calls == []

    confirmed = asyncio.run(
        executor.confirm_execution(confirmation_id, actor_id="alice")
    )
    assert confirmed.success is True
    assert tool.calls == [("report", True)]

    replayed = asyncio.run(
        executor.confirm_execution(confirmation_id, actor_id="alice")
    )
    assert replayed.status == ToolExecutionStatus.TOOL_NOT_FOUND
    assert any(event["confirmed"] is True for event in get_audit_events(caplog))


def test_confirmation_rechecks_permissions_and_expires() -> None:
    tool = ConfirmingTool()
    permissions = PermissionManager({"alice": [PermissionLevel.WRITE]})
    executor = make_executor(tool, permissions)
    pending = asyncio.run(
        executor.request_execution(
            "save_item",
            {"value": "report"},
            actor_id="alice",
            user_request="Save the report",
        )
    )
    assert pending.confirmation_id is not None

    permissions.revoke("alice", PermissionLevel.WRITE)
    denied = asyncio.run(
        executor.confirm_execution(pending.confirmation_id, actor_id="alice")
    )
    assert denied.status == ToolExecutionStatus.PERMISSION_DENIED
    assert tool.calls == []

    permissions.grant("alice", PermissionLevel.WRITE)
    expiring_executor = make_executor(
        tool,
        permissions,
        confirmation_ttl=timedelta(0),
    )
    expiring = asyncio.run(
        expiring_executor.request_execution(
            "save_item",
            {"value": "expired"},
            actor_id="alice",
            user_request="Save an expired item",
        )
    )
    assert expiring.confirmation_id is not None
    expired = asyncio.run(
        expiring_executor.confirm_execution(expiring.confirmation_id, actor_id="alice")
    )
    assert expired.status == ToolExecutionStatus.CONFIRMATION_EXPIRED
    assert tool.calls == []


def test_confirmation_can_be_cancelled() -> None:
    tool = ConfirmingTool()
    permissions = PermissionManager({"alice": [PermissionLevel.WRITE]})
    executor = make_executor(tool, permissions)
    pending = asyncio.run(
        executor.request_execution(
            "save_item",
            {"value": "report"},
            actor_id="alice",
            user_request="Save the report",
        )
    )
    assert pending.confirmation_id is not None

    cancelled = executor.cancel_confirmation(
        pending.confirmation_id,
        actor_id="alice",
    )
    assert cancelled.status == ToolExecutionStatus.CONFIRMATION_CANCELLED
    assert tool.calls == []


def test_clock_tool_exposes_validated_utc_response() -> None:
    executor = make_executor(
        ClockTool(),
        PermissionManager({"alice": [PermissionLevel.READ]}),
    )

    response = asyncio.run(
        executor.request_execution(
            "utc_clock",
            {},
            actor_id="alice",
            user_request="What time is it?",
        )
    )

    assert response.success is True
    assert response.result is not None
    assert response.result["timezone"] == "UTC"
    assert response.result["timestamp"].endswith(("Z", "+00:00"))

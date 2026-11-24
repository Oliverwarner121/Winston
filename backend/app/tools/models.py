from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.tools.permissions import PermissionLevel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ToolExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TOOL_NOT_FOUND = "tool_not_found"
    PERMISSION_DENIED = "permission_denied"
    INVALID_INPUT = "invalid_input"
    CONFIRMATION_REQUIRED = "confirmation_required"
    CONFIRMATION_EXPIRED = "confirmation_expired"
    CONFIRMATION_CANCELLED = "confirmation_cancelled"
    CANCELLED = "cancelled"


class ToolCategory(StrEnum):
    FILES = "FILES"
    DEVELOPMENT = "DEVELOPMENT"
    SYSTEM = "SYSTEM"
    DOCKER = "DOCKER"
    BROWSER = "BROWSER"
    PRODUCTIVITY = "PRODUCTIVITY"
    HOMELAB = "HOMELAB"
    UTILITY = "UTILITY"


class ToolDefinition(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    category: str = Field(min_length=1)
    permission_level: PermissionLevel
    requires_confirmation: bool
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class ToolExecutionResponse(BaseModel):
    success: bool
    tool_id: str = Field(min_length=1, max_length=100)
    status: ToolExecutionStatus
    result: dict[str, Any] | None = None
    execution_time_ms: float = Field(ge=0)
    timestamp: datetime = Field(default_factory=utc_now)
    error: str | None = Field(default=None, max_length=2000)
    confirmed: bool = False
    confirmation_id: UUID | None = None
    confirmation_message: str | None = Field(default=None, max_length=30_000)


class ToolAuditEvent(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    tool_id: str = Field(min_length=1, max_length=100)
    actor_id: str = Field(min_length=1, max_length=200)
    user_request: str = Field(max_length=4000)
    inputs: dict[str, Any]
    status: ToolExecutionStatus
    execution_time_ms: float = Field(ge=0)
    confirmed: bool
    confirmation_id: UUID | None = None
    error: str | None = None

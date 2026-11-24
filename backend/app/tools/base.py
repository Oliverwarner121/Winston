import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel

from app.tools.models import ToolDefinition
from app.tools.permissions import PermissionLevel


class ToolExecutionError(Exception):
    """A safe, user-facing failure raised by a tool implementation."""

    def __init__(self, public_message: str) -> None:
        message = public_message.strip()
        if not message or len(message) > 2000:
            raise ValueError("Tool error messages must be 1 to 2000 characters.")
        super().__init__(message)
        self.public_message = message


InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True)
class ToolContext:
    actor_id: str
    user_request: str
    confirmed: bool
    confirmation_id: UUID | None = None


class Tool(ABC, Generic[InputT, OutputT]):
    id: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    category: ClassVar[str] = "general"
    permission_level: ClassVar[PermissionLevel] = PermissionLevel.READ
    requires_confirmation: ClassVar[bool] = False
    end_agent_run_after_success: ClassVar[bool] = False
    input_model: ClassVar[type[InputT]]
    output_model: ClassVar[type[OutputT]]
    audit_safe_input_fields: ClassVar[frozenset[str]] = frozenset()

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            id=self.id,
            name=self.name,
            description=self.description,
            category=self.category,
            permission_level=self.permission_level,
            requires_confirmation=self.requires_confirmation,
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    def audit_inputs(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value if key in self.audit_safe_input_fields else "[REDACTED]"
            for key, value in inputs.items()
        }

    def minimum_permission_level(self) -> PermissionLevel:
        return self.permission_level

    def required_permission_level(self, inputs: InputT) -> PermissionLevel:
        return self.permission_level

    def confirmation_required(self, inputs: InputT) -> bool:
        return self.requires_confirmation

    def confirmation_message(self, inputs: InputT) -> str:
        safe_inputs = self.audit_inputs(inputs.model_dump(mode="json"))
        serialized_inputs = json.dumps(safe_inputs, sort_keys=True)
        return f"{self.name} requests confirmation with inputs {serialized_inputs}."

    @abstractmethod
    async def execute(self, inputs: InputT, context: ToolContext) -> OutputT:
        """Perform this tool's action using validated inputs and execution context."""

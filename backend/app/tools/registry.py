from threading import RLock
from typing import Any

from app.tools.base import Tool
from app.tools.models import ToolDefinition


class DuplicateToolError(ValueError):
    """Raised when a tool ID is already registered."""


class ToolNotFoundError(LookupError):
    """Raised when a requested tool ID is not registered."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any, Any]] = {}
        self._names: dict[str, str] = {}
        self._lock = RLock()

    def register(self, tool: Tool[Any, Any]) -> None:
        definition = tool.definition()
        name_key = definition.name.casefold()
        with self._lock:
            if definition.id in self._tools or name_key in self._names:
                raise DuplicateToolError(
                    f"Tool ID '{definition.id}' or name '{definition.name}' "
                    "is already registered."
                )
            self._tools[definition.id] = tool
            self._names[name_key] = definition.id

    def get(self, tool_id: str) -> Tool[Any, Any]:
        with self._lock:
            tool = self._tools.get(tool_id)

        if tool is None:
            raise ToolNotFoundError("Tool was not found.")
        return tool

    def list_tools(self, category: str | None = None) -> list[ToolDefinition]:
        with self._lock:
            tools = tuple(self._tools.values())

        definitions = [tool.definition() for tool in tools]
        if category is not None:
            definitions = [
                definition
                for definition in definitions
                if definition.category == category
            ]
        return sorted(definitions, key=lambda definition: definition.id)

    def by_category(self, category: str) -> list[ToolDefinition]:
        return self.list_tools(category=category)

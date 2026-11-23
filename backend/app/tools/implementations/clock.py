from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from app.tools.base import Tool, ToolContext
from app.tools.permissions import PermissionLevel


class ClockInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClockOutput(BaseModel):
    timestamp: datetime
    timezone: str


class ClockTool(Tool[ClockInput, ClockOutput]):
    id = "utc_clock"
    name = "UTC Clock"
    description = "Get the current time in UTC."
    category = "utility"
    permission_level = PermissionLevel.READ
    input_model = ClockInput
    output_model = ClockOutput

    async def execute(
        self,
        inputs: ClockInput,
        context: ToolContext,
    ) -> ClockOutput:
        return ClockOutput(
            timestamp=datetime.now(timezone.utc),
            timezone="UTC",
        )

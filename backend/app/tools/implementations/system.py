import asyncio
import platform
from pathlib import Path

import psutil
from pydantic import BaseModel, ConfigDict

from app.core.config import ToolSettings
from app.tools.base import Tool, ToolContext, ToolExecutionError
from app.tools.implementations.workspace import resolve_workspace_path
from app.tools.models import ToolCategory
from app.tools.permissions import PermissionLevel


class SystemInfoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SystemInfoOutput(BaseModel):
    operating_system: str
    release: str
    architecture: str
    processor: str
    cpu_cores_logical: int
    cpu_percent: float
    memory_total_bytes: int
    memory_used_bytes: int
    memory_percent: float
    disk_total_bytes: int
    disk_used_bytes: int
    disk_percent: float


class SystemInfoTool(Tool[SystemInfoInput, SystemInfoOutput]):
    id = "system_info"
    name = "System information"
    description = "Read CPU, memory, disk, and operating system information."
    category = ToolCategory.SYSTEM
    permission_level = PermissionLevel.READ
    input_model = SystemInfoInput
    output_model = SystemInfoOutput

    def __init__(self, settings: ToolSettings) -> None:
        self._settings = settings

    async def execute(
        self,
        inputs: SystemInfoInput,
        context: ToolContext,
    ) -> SystemInfoOutput:
        try:
            root = resolve_workspace_path(
                ".",
                self._settings.workspace_roots,
                must_exist=True,
            ).path
        except ToolExecutionError as exc:
            raise ToolExecutionError("No configured workspace is available.") from exc
        return await asyncio.to_thread(_collect_system_info, root)


def _collect_system_info(root: Path) -> SystemInfoOutput:
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(root)
    return SystemInfoOutput(
        operating_system=platform.system(),
        release=platform.release(),
        architecture=platform.machine(),
        processor=platform.processor() or "unknown",
        cpu_cores_logical=psutil.cpu_count(logical=True) or 1,
        cpu_percent=cpu_percent,
        memory_total_bytes=memory.total,
        memory_used_bytes=memory.used,
        memory_percent=memory.percent,
        disk_total_bytes=disk.total,
        disk_used_bytes=disk.used,
        disk_percent=disk.percent,
    )

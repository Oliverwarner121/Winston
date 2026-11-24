import json
import shutil
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import ToolSettings
from app.tools.base import Tool, ToolContext, ToolExecutionError
from app.tools.implementations.process import minimal_environment, run_process
from app.tools.implementations.workspace import resolve_workspace_path
from app.tools.models import ToolCategory
from app.tools.permissions import PermissionLevel


class DockerStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container: str | None = Field(default=None, min_length=1, max_length=128)


class DockerContainerStatus(BaseModel):
    name: str
    image: str
    state: str
    status: str


class DockerStatusOutput(BaseModel):
    containers: list[DockerContainerStatus]


class DockerStatusTool(Tool[DockerStatusInput, DockerStatusOutput]):
    id = "docker_status"
    name = "Docker status"
    description = "List status for explicitly configured Docker containers."
    category = ToolCategory.DOCKER
    permission_level = PermissionLevel.READ
    input_model = DockerStatusInput
    output_model = DockerStatusOutput

    def __init__(self, settings: ToolSettings) -> None:
        self._settings = settings

    async def execute(
        self,
        inputs: DockerStatusInput,
        context: ToolContext,
    ) -> DockerStatusOutput:
        allowed = set(self._settings.docker_allowed_containers)
        if not allowed:
            raise ToolExecutionError("No Docker containers are allowlisted.")
        if inputs.container is not None and inputs.container not in allowed:
            raise ToolExecutionError("Container is not in the configured allowlist.")
        docker = shutil.which("docker")
        if docker is None:
            raise ToolExecutionError("Docker CLI is not installed.")
        workspace = resolve_workspace_path(".", self._settings.workspace_roots, must_exist=True)

        result = await run_process(
            [
                docker,
                "--context",
                "default",
                "ps",
                "--all",
                "--format",
                "{{json .}}",
            ],
            cwd=workspace.path,
            timeout_seconds=self._settings.docker_timeout_seconds,
            output_limit=100_000,
            environment=minimal_environment(),
        )
        if result.timed_out:
            raise ToolExecutionError("Docker status request timed out.")
        if result.return_code != 0:
            raise ToolExecutionError("Unable to retrieve Docker container status.")

        containers: list[DockerContainerStatus] = []
        for line in result.stdout.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ToolExecutionError("Docker returned invalid status data.") from exc
            if not isinstance(record, dict):
                raise ToolExecutionError("Docker returned invalid status data.")
            names = [
                name
                for name in str(record.get("Names", "")).split(",")
                if name in allowed
            ]
            if inputs.container is not None:
                names = [name for name in names if name == inputs.container]
            if not names:
                continue
            containers.append(
                DockerContainerStatus(
                    name=names[0],
                    image=record.get("Image", ""),
                    state=record.get("State", ""),
                    status=record.get("Status", ""),
                )
            )
        return DockerStatusOutput(containers=containers)


class DockerOperation(StrEnum):
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    LOGS = "logs"


class DockerManagementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: DockerOperation
    container: str = Field(min_length=1, max_length=128)


class DockerManagementOutput(BaseModel):
    operation: DockerOperation
    container: str
    output: str


class DockerManagementTool(Tool[DockerManagementInput, DockerManagementOutput]):
    id = "docker_manage"
    name = "Docker management"
    description = "Start, stop, restart, or view logs for an allowlisted container."
    category = ToolCategory.HOMELAB
    permission_level = PermissionLevel.WRITE
    input_model = DockerManagementInput
    output_model = DockerManagementOutput
    audit_safe_input_fields: ClassVar[frozenset[str]] = frozenset(
        {"operation", "container"}
    )

    def __init__(self, settings: ToolSettings) -> None:
        self._settings = settings

    def confirmation_message(self, inputs: DockerManagementInput) -> str:
        return (
            f"Docker {inputs.operation.value} for {inputs.container} "
            "using context default."
        )

    async def execute(
        self,
        inputs: DockerManagementInput,
        context: ToolContext,
    ) -> DockerManagementOutput:
        if inputs.container not in self._settings.docker_allowed_containers:
            raise ToolExecutionError("Container is not in the configured allowlist.")
        docker = shutil.which("docker")
        if docker is None:
            raise ToolExecutionError("Docker CLI is not installed.")
        workspace = resolve_workspace_path(".", self._settings.workspace_roots, must_exist=True)

        arguments = [docker, "--context", "default"]
        if inputs.operation == DockerOperation.LOGS:
            arguments.extend(
                [
                    "logs",
                    "--tail",
                    str(self._settings.docker_logs_tail_lines),
                    inputs.container,
                ]
            )
        else:
            arguments.extend([inputs.operation.value, inputs.container])
        result = await run_process(
            arguments,
            cwd=workspace.path,
            timeout_seconds=self._settings.docker_timeout_seconds,
            output_limit=48_000,
            environment=minimal_environment(),
        )
        if result.timed_out:
            raise ToolExecutionError("Docker operation timed out.")
        if result.return_code != 0:
            raise ToolExecutionError("Docker operation failed.")
        return DockerManagementOutput(
            operation=inputs.operation,
            container=inputs.container,
            output=(result.stdout or result.stderr)[:12_000],
        )

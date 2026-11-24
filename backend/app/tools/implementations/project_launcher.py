import asyncio
import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.projects.registry import ProjectNotFoundError, ProjectRegistry
from app.tools.base import Tool, ToolContext, ToolExecutionError
from app.tools.models import ToolCategory, ToolDefinition
from app.tools.permissions import PermissionLevel


class ProjectLauncherInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = Field(min_length=1, max_length=100)


class ProjectLauncherOutput(BaseModel):
    project_id: str
    project_name: str
    message: str
    vscode_opened: bool
    terminal_opened: bool
    vscode_process_id: int | None = None
    terminal_process_id: int | None = None


class ProjectLauncherTool(Tool[ProjectLauncherInput, ProjectLauncherOutput]):
    id = "project_launcher"
    name = "Project Launcher"
    description = (
        "Open a configured development project in VS Code and a terminal."
    )
    category = ToolCategory.DEVELOPMENT
    permission_level = PermissionLevel.READ
    input_model = ProjectLauncherInput
    output_model = ProjectLauncherOutput
    audit_safe_input_fields: ClassVar[frozenset[str]] = frozenset({"project"})

    def __init__(self, registry: ProjectRegistry) -> None:
        self._registry = registry

    def definition(self) -> ToolDefinition:
        definition = super().definition()
        names = ", ".join(project.name for project in self._registry.list_projects())
        description = (
            f"{definition.description} Configured projects: {names[:4000]}."
        )
        return definition.model_copy(update={"description": description})

    def confirmation_message(self, inputs: ProjectLauncherInput) -> str:
        project = self._registry.get_project(inputs.project)
        actions = []
        if project.launch_vscode:
            actions.append("Open VS Code")
        if project.launch_terminal:
            actions.append("Open Terminal")
        action_list = "\n".join(f"- {action}" for action in actions)
        return (
            f"Open project {json.dumps(project.name)} at "
            f"{json.dumps(str(project.path))}:\n{action_list}"
        )

    def confirmation_required(self, inputs: ProjectLauncherInput) -> bool:
        return False

    def audit_inputs(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        requested = inputs.get("project")
        if not isinstance(requested, str):
            return {"project": "[REDACTED]"}
        try:
            project = self._registry.get_project(requested)
        except ProjectNotFoundError:
            return {"project": requested[:100]}
        actions = []
        if project.launch_vscode:
            actions.append("vscode")
        if project.launch_terminal:
            actions.append("terminal")
        return {
            "project": project.name,
            "project_id": project.id,
            "actions": actions,
        }

    async def execute(
        self,
        inputs: ProjectLauncherInput,
        context: ToolContext,
    ) -> ProjectLauncherOutput:
        try:
            project = self._registry.get_project(inputs.project)
        except ProjectNotFoundError as exc:
            raise ToolExecutionError("Configured project was not found.") from exc

        if not project.path.is_dir():
            raise ToolExecutionError("Configured project directory is unavailable.")

        vscode = shutil.which("code") if project.launch_vscode else None
        terminal = _find_terminal() if project.launch_terminal else None
        if project.launch_vscode and vscode is None:
            raise ToolExecutionError("VS Code command 'code' is not available.")
        if project.launch_terminal and terminal is None:
            raise ToolExecutionError("No supported terminal application is available.")

        process_ids: dict[str, int] = {}
        try:
            if vscode is not None:
                process = await asyncio.to_thread(
                    subprocess.Popen,
                    [vscode, "--reuse-window", str(project.path)],
                    cwd=project.path,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    start_new_session=(os.name == "posix"),
                )
                process_ids["vscode"] = process.pid
            if terminal is not None:
                process = await asyncio.to_thread(
                    subprocess.Popen,
                    _terminal_arguments(terminal, project.path),
                    cwd=project.path,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    start_new_session=(os.name == "posix"),
                )
                process_ids["terminal"] = process.pid
        except OSError as exc:
            if process_ids:
                started = " and ".join(process_ids)
                raise ToolExecutionError(
                    f"Project launch was incomplete; {started} opened but another "
                    "application could not be started."
                ) from exc
            raise ToolExecutionError("Unable to start a project application.") from exc

        return ProjectLauncherOutput(
            project_id=project.id,
            project_name=project.name,
            message=f"{project.name} launched successfully.",
            vscode_opened="vscode" in process_ids,
            terminal_opened="terminal" in process_ids,
            vscode_process_id=process_ids.get("vscode"),
            terminal_process_id=process_ids.get("terminal"),
        )


def _find_terminal() -> tuple[str, str] | None:
    for terminal_name in (
        "gnome-terminal",
        "x-terminal-emulator",
        "konsole",
        "xfce4-terminal",
    ):
        executable = shutil.which(terminal_name)
        if executable is not None:
            return terminal_name, executable
    return None


def _terminal_arguments(terminal: tuple[str, str], project_path: Path) -> list[str]:
    terminal_name, executable = terminal
    path = str(project_path)
    if terminal_name in {"gnome-terminal", "xfce4-terminal"}:
        return [executable, "--working-directory", path]
    if terminal_name == "konsole":
        return [executable, "--workdir", path]
    return [executable]

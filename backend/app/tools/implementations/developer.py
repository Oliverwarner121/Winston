import asyncio
import json
import os
import shlex
import shutil
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import ToolSettings
from app.tools.base import Tool, ToolContext, ToolExecutionError
from app.tools.implementations.process import minimal_environment, run_process
from app.tools.implementations.workspace import (
    WorkspacePathError,
    is_sensitive_path,
    resolve_workspace_path,
)
from app.tools.models import ToolCategory
from app.tools.permissions import PermissionLevel


class OpenVSCodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(default=".", min_length=1, max_length=1000)


class OpenVSCodeOutput(BaseModel):
    opened_path: str
    process_id: int


class OpenVSCodeTool(Tool[OpenVSCodeInput, OpenVSCodeOutput]):
    id = "open_vscode"
    name = "Open VS Code"
    description = "Open a workspace file or folder in VS Code."
    category = ToolCategory.DEVELOPMENT
    permission_level = PermissionLevel.READ
    input_model = OpenVSCodeInput
    output_model = OpenVSCodeOutput
    audit_safe_input_fields: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, settings: ToolSettings) -> None:
        self._settings = settings

    def confirmation_message(self, inputs: OpenVSCodeInput) -> str:
        return f"Open {json.dumps(inputs.path)} in VS Code."

    async def execute(
        self,
        inputs: OpenVSCodeInput,
        context: ToolContext,
    ) -> OpenVSCodeOutput:
        location = resolve_workspace_path(
            inputs.path,
            self._settings.workspace_roots,
            must_exist=True,
        )
        if is_sensitive_path(location.path):
            raise ToolExecutionError("Opening sensitive files is not allowed.")
        if not location.path.is_file() and not location.path.is_dir():
            raise ToolExecutionError("Requested path is not a file or directory.")

        executable = shutil.which("code")
        if executable is None:
            raise ToolExecutionError("VS Code command 'code' is not available.")
        try:
            process = await asyncio.to_thread(
                subprocess.Popen,
                [executable, "--reuse-window", str(location.path)],
                cwd=location.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=(os.name == "posix"),
            )
        except OSError as exc:
            raise ToolExecutionError("Unable to start VS Code.") from exc
        return OpenVSCodeOutput(
            opened_path=location.relative_path,
            process_id=process.pid,
        )


class GitOperation(StrEnum):
    STATUS = "status"
    CREATE_BRANCH = "create_branch"
    COMMIT = "commit"
    PUSH = "push"
    PULL = "pull"
    HISTORY = "history"


class GitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: GitOperation
    repository: str = Field(default=".", min_length=1, max_length=1000)
    branch: str | None = Field(default=None, min_length=1, max_length=200)
    message: str | None = Field(default=None, min_length=1, max_length=500)
    remote: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_operation_arguments(self) -> "GitInput":
        if self.operation in {
            GitOperation.CREATE_BRANCH,
            GitOperation.PUSH,
            GitOperation.PULL,
        } and self.branch is None:
            raise ValueError("A branch is required for this operation.")
        if self.operation == GitOperation.COMMIT and self.message is None:
            raise ValueError("A commit message is required.")
        if self.operation in {GitOperation.PUSH, GitOperation.PULL}:
            if self.remote is None:
                raise ValueError("A configured remote is required.")
        if self.remote is not None and not _is_simple_git_name(self.remote):
            raise ValueError("Remote name contains unsupported characters.")
        if self.branch is not None and not _is_simple_git_name(self.branch):
            raise ValueError("Branch name contains unsupported characters.")
        return self


class GitOutput(BaseModel):
    operation: GitOperation
    repository: str
    return_code: int
    output: str


class GitTool(Tool[GitInput, GitOutput]):
    id = "git"
    name = "Git"
    description = (
        "Inspect status/history or create a branch, commit staged files, "
        "pull fast-forward updates, or push a branch."
    )
    category = ToolCategory.DEVELOPMENT
    permission_level = PermissionLevel.ADMIN
    requires_confirmation = True
    input_model = GitInput
    output_model = GitOutput
    audit_safe_input_fields: ClassVar[frozenset[str]] = frozenset(
        {"operation", "repository", "branch", "remote"}
    )

    def __init__(self, settings: ToolSettings) -> None:
        self._settings = settings

    def minimum_permission_level(self) -> PermissionLevel:
        return PermissionLevel.READ

    def required_permission_level(self, inputs: GitInput) -> PermissionLevel:
        if inputs.operation in {GitOperation.STATUS, GitOperation.HISTORY}:
            return PermissionLevel.READ
        if inputs.operation in {GitOperation.PUSH, GitOperation.PULL}:
            return PermissionLevel.ADMIN
        return PermissionLevel.WRITE

    def confirmation_required(self, inputs: GitInput) -> bool:
        return True

    def confirmation_message(self, inputs: GitInput) -> str:
        if inputs.operation == GitOperation.CREATE_BRANCH:
            return f"Create branch {inputs.branch} in {inputs.repository}."
        if inputs.operation == GitOperation.COMMIT:
            return (
                f"Commit staged changes in {inputs.repository} with message "
                f"{inputs.message!r}; configured Git hooks may run."
            )
        return (
            f"Git {inputs.operation.value} branch {inputs.branch!r} using remote "
            f"{inputs.remote!r} in {inputs.repository!r}; remote writes are "
            "limited to configured remotes."
        )

    async def execute(self, inputs: GitInput, context: ToolContext) -> GitOutput:
        location = resolve_workspace_path(
            inputs.repository,
            self._settings.workspace_roots,
            must_exist=True,
        )
        if not location.path.is_dir():
            raise ToolExecutionError("Repository path is not a directory.")
        git = shutil.which("git")
        if git is None:
            raise ToolExecutionError("Git is not installed.")
        repository_root = await self._find_repository_root(
            git,
            location.path,
            location.root,
        )
        operation_args = self._operation_arguments(inputs)
        result = await run_process(
            [git, *operation_args],
            cwd=repository_root,
            timeout_seconds=30,
            output_limit=48_000,
            environment=minimal_environment(),
        )
        if result.timed_out:
            raise ToolExecutionError("Git operation timed out.")
        if result.return_code != 0:
            raise ToolExecutionError(
                f"Git {inputs.operation.value} failed (exit code {result.return_code})."
            )
        output = result.stdout or result.stderr
        return GitOutput(
            operation=inputs.operation,
            repository=repository_root.relative_to(location.root).as_posix(),
            return_code=result.return_code,
            output=output[:12_000],
        )

    async def _find_repository_root(
        self,
        git: str,
        path: Path,
        workspace_root: Path,
    ) -> Path:
        result = await run_process(
            [git, "rev-parse", "--show-toplevel"],
            cwd=path,
            timeout_seconds=10,
            output_limit=4000,
            environment=minimal_environment(),
        )
        if result.return_code != 0 or result.timed_out:
            raise ToolExecutionError("Requested folder is not a Git repository.")
        try:
            repository_root = Path(result.stdout.strip()).resolve(strict=True)
            repository_root.relative_to(workspace_root)
        except (OSError, ValueError) as exc:
            raise ToolExecutionError(
                "Git repository resolves outside the configured workspace."
            ) from exc
        return repository_root

    def _operation_arguments(self, inputs: GitInput) -> list[str]:
        if inputs.operation == GitOperation.STATUS:
            return ["status", "--short", "--branch"]
        if inputs.operation == GitOperation.HISTORY:
            return ["log", "--oneline", "--decorate", "-n", "10"]
        if inputs.operation == GitOperation.CREATE_BRANCH:
            return ["branch", "--", _required(inputs.branch)]
        if inputs.operation == GitOperation.COMMIT:
            return ["commit", "--message", _required(inputs.message)]
        if inputs.remote not in self._settings.git_allowed_remotes:
            raise ToolExecutionError("Git remote is not in the configured allowlist.")
        if inputs.operation == GitOperation.PUSH:
            branch = _required(inputs.branch)
            return ["push", "--", _required(inputs.remote), f"refs/heads/{branch}"]
        if inputs.operation == GitOperation.PULL:
            return [
                "pull",
                "--ff-only",
                "--",
                _required(inputs.remote),
                _required(inputs.branch),
            ]
        raise ToolExecutionError("Unsupported Git operation.")


class TerminalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program: str = Field(min_length=1, max_length=50)
    arguments: list[str] = Field(default_factory=list, max_length=32)
    working_directory: str = Field(default=".", min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_arguments(self) -> "TerminalInput":
        if Path(self.program).name != self.program or self.program in {".", ".."}:
            raise ValueError("Program must be an allowlisted executable name.")
        if any("\x00" in argument or len(argument) > 500 for argument in self.arguments):
            raise ValueError("Command arguments are invalid or too long.")
        return self


class TerminalOutput(BaseModel):
    program: str
    return_code: int
    stdout: str
    stderr: str
    timed_out: bool


class TerminalTool(Tool[TerminalInput, TerminalOutput]):
    id = "terminal"
    name = "Terminal"
    description = (
        "Run one allowlisted executable without a shell in a configured workspace."
    )
    category = ToolCategory.DEVELOPMENT
    permission_level = PermissionLevel.SYSTEM
    requires_confirmation = True
    input_model = TerminalInput
    output_model = TerminalOutput
    audit_safe_input_fields: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, settings: ToolSettings) -> None:
        self._settings = settings

    def confirmation_message(self, inputs: TerminalInput) -> str:
        command = shlex.join(self._command_arguments(inputs))
        return f"Run {command} in {shlex.quote(inputs.working_directory)}."

    def confirmation_required(self, inputs: TerminalInput) -> bool:
        return inputs.program in self._settings.terminal_allowed_programs

    async def execute(
        self,
        inputs: TerminalInput,
        context: ToolContext,
    ) -> TerminalOutput:
        if inputs.program not in self._settings.terminal_allowed_programs:
            raise ToolExecutionError("Executable is not in the configured allowlist.")
        command_arguments = self._command_arguments(inputs)
        location = resolve_workspace_path(
            inputs.working_directory,
            self._settings.workspace_roots,
            must_exist=True,
        )
        if not location.path.is_dir():
            raise ToolExecutionError("Working directory is not a directory.")
        if is_sensitive_path(location.path):
            raise ToolExecutionError("Sensitive directories cannot be command working directories.")

        result = await run_process(
            command_arguments,
            cwd=location.path,
            timeout_seconds=self._settings.terminal_timeout_seconds,
            output_limit=self._settings.terminal_max_output_chars * 4,
            environment=minimal_environment(),
        )
        return TerminalOutput(
            program=inputs.program,
            return_code=result.return_code,
            stdout=_clip_output(result.stdout, self._settings.terminal_max_output_chars),
            stderr=_clip_output(result.stderr, self._settings.terminal_max_output_chars),
            timed_out=result.timed_out,
        )

    def _command_arguments(self, inputs: TerminalInput) -> list[str]:
        executable = shutil.which(inputs.program)
        if executable is not None:
            return [executable, *inputs.arguments]
        if inputs.program == "pytest":
            return [sys.executable, "-m", "pytest", *inputs.arguments]
        return [inputs.program, *inputs.arguments]


def _is_simple_git_name(value: str) -> bool:
    return bool(value) and all(
        character.isalnum() or character in "._/-" for character in value
    ) and not value.startswith("-") and ".." not in value and "//" not in value


def _required(value: str | None) -> str:
    if value is None:
        raise ToolExecutionError("Required Git argument is missing.")
    return value


def _clip_output(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n[output truncated]"

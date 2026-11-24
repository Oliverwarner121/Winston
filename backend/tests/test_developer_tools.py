import asyncio
import subprocess
from types import SimpleNamespace

from app.core.config import ToolSettings
from app.tools.base import Tool
from app.tools.executor import ToolExecutor
from app.tools.implementations.developer import (
    GitTool,
    OpenVSCodeTool,
    TerminalTool,
)
from app.tools.models import ToolExecutionStatus
from app.tools.permissions import PermissionLevel, PermissionManager
from app.tools.registry import ToolRegistry


def build_executor(tool: Tool, permissions: PermissionManager) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry, permissions)


def test_terminal_requires_confirmation_and_never_uses_a_shell(tmp_path) -> None:
    settings = ToolSettings(
        workspace_roots=[tmp_path],
        terminal_allowed_programs=["echo"],
    )
    permissions = PermissionManager({"alice": [PermissionLevel.SYSTEM]})
    executor = build_executor(TerminalTool(settings), permissions)

    pending = asyncio.run(
        executor.request_execution(
            "terminal",
            {
                "program": "echo",
                "arguments": ["literal; touch should-not-exist"],
                "working_directory": ".",
            },
            actor_id="alice",
            user_request="Print a harmless string",
        )
    )
    assert pending.status == ToolExecutionStatus.CONFIRMATION_REQUIRED
    assert pending.confirmation_message is not None
    assert "literal;" in pending.confirmation_message
    assert not (tmp_path / "should-not-exist").exists()
    assert pending.confirmation_id is not None

    result = asyncio.run(
        executor.confirm_execution(pending.confirmation_id, actor_id="alice")
    )
    assert result.success is True
    assert result.result["return_code"] == 0
    assert result.result["stdout"].strip() == "literal; touch should-not-exist"
    assert not (tmp_path / "should-not-exist").exists()


def test_git_status_and_branch_creation_use_scoped_git_commands(tmp_path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Winston Test"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "winston-test@example.invalid"],
        cwd=repository,
        check=True,
    )
    (repository / "README.txt").write_text("test", encoding="utf-8")
    subprocess.run(["git", "add", "README.txt"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "initial"],
        cwd=repository,
        check=True,
    )
    settings = ToolSettings(workspace_roots=[tmp_path])
    permissions = PermissionManager({"alice": [PermissionLevel.WRITE]})
    executor = build_executor(GitTool(settings), permissions)

    status_pending = asyncio.run(
        executor.request_execution(
            "git",
            {"operation": "status", "repository": "repo"},
            actor_id="alice",
            user_request="Check Git status",
        )
    )
    assert status_pending.status == ToolExecutionStatus.CONFIRMATION_REQUIRED
    assert status_pending.confirmation_id is not None
    status = asyncio.run(
        executor.confirm_execution(status_pending.confirmation_id, actor_id="alice")
    )
    assert status.success is True
    assert status.confirmed is True
    assert "## " in status.result["output"]

    pending = asyncio.run(
        executor.request_execution(
            "git",
            {
                "operation": "create_branch",
                "repository": "repo",
                "branch": "feature/tool-tests",
            },
            actor_id="alice",
            user_request="Create a branch",
        )
    )
    assert pending.status == ToolExecutionStatus.CONFIRMATION_REQUIRED
    assert pending.confirmation_id is not None
    created = asyncio.run(
        executor.confirm_execution(pending.confirmation_id, actor_id="alice")
    )
    assert created.success is True
    assert created.confirmed is True
    branches = subprocess.run(
        ["git", "branch", "--list", "feature/tool-tests"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "feature/tool-tests" in branches.stdout

    (repository / "next.txt").write_text("staged", encoding="utf-8")
    subprocess.run(["git", "add", "next.txt"], cwd=repository, check=True)
    commit_pending = asyncio.run(
        executor.request_execution(
            "git",
            {
                "operation": "commit",
                "repository": "repo",
                "message": "Add staged file",
            },
            actor_id="alice",
            user_request="Commit staged changes",
        )
    )
    assert commit_pending.status == ToolExecutionStatus.CONFIRMATION_REQUIRED
    assert commit_pending.confirmation_id is not None
    committed = asyncio.run(
        executor.confirm_execution(
            commit_pending.confirmation_id,
            actor_id="alice",
        )
    )
    assert committed.success is True
    latest_commit = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    assert latest_commit.stdout.strip() == "Add staged file"


def test_git_remote_operations_require_explicit_allowlist(tmp_path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    settings = ToolSettings(workspace_roots=[tmp_path])
    permissions = PermissionManager({"alice": [PermissionLevel.ADMIN]})
    executor = build_executor(GitTool(settings), permissions)

    pending = asyncio.run(
        executor.request_execution(
            "git",
            {
                "operation": "push",
                "repository": "repo",
                "branch": "main",
                "remote": "origin",
            },
            actor_id="alice",
            user_request="Push branch",
        )
    )
    assert pending.status == ToolExecutionStatus.CONFIRMATION_REQUIRED
    assert pending.confirmation_id is not None
    denied = asyncio.run(
        executor.confirm_execution(pending.confirmation_id, actor_id="alice")
    )
    assert denied.status == ToolExecutionStatus.FAILED
    assert denied.error == "Git remote is not in the configured allowlist."


def test_open_vscode_launches_without_confirmation_and_is_path_scoped(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        "app.tools.implementations.developer.shutil.which",
        lambda command: "/usr/bin/code",
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(arguments: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((arguments, kwargs))
        return SimpleNamespace(pid=1234)

    monkeypatch.setattr(
        "app.tools.implementations.developer.subprocess.Popen",
        fake_popen,
    )
    settings = ToolSettings(workspace_roots=[tmp_path])
    permissions = PermissionManager({"alice": [PermissionLevel.READ]})
    executor = build_executor(OpenVSCodeTool(settings), permissions)

    opened = asyncio.run(
        executor.request_execution(
            "open_vscode",
            {"path": "project"},
            actor_id="alice",
            user_request="Open project",
        )
    )

    assert opened.success is True
    assert opened.confirmed is False
    assert calls[0][0] == ["/usr/bin/code", "--reuse-window", str(project)]

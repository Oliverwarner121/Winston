import asyncio

import pytest
from pydantic import ValidationError

from app.core.config import ToolSettings
from app.tools.base import Tool
from app.tools.executor import ToolExecutor
from app.tools.implementations.files import (
    FileMoveTool,
    FileReadTool,
    FileSearchTool,
    FileWriteMode,
    FileWriteTool,
)
from app.tools.implementations.system import SystemInfoTool
from app.tools.models import ToolExecutionStatus
from app.tools.permissions import PermissionLevel, PermissionManager
from app.tools.registry import ToolRegistry


def build_executor(tool: Tool, permissions: PermissionManager) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry, permissions)


def test_file_search_and_read_are_workspace_scoped(tmp_path) -> None:
    docs = tmp_path / "Documents"
    docs.mkdir()
    cv = docs / "oliver-cv.pdf"
    cv.write_text("A sample text CV.", encoding="utf-8")
    (docs / ".env").write_text("private=hidden", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside workspace", encoding="utf-8")
    settings = ToolSettings(workspace_roots=[tmp_path])
    permissions = PermissionManager({"alice": [PermissionLevel.READ]})
    search_executor = build_executor(FileSearchTool(settings), permissions)
    read_executor = build_executor(FileReadTool(settings), permissions)

    search = asyncio.run(
        search_executor.request_execution(
            "file_search",
            {"query": "cv", "folder": "Documents", "extension": ".pdf"},
            actor_id="alice",
            user_request="Find my CV",
        )
    )
    assert search.success is True
    assert len(search.result["files"]) == 1
    assert search.result["files"][0]["path"] == str(cv)

    read = asyncio.run(
        read_executor.request_execution(
            "file_read",
            {"path": "Documents/oliver-cv.pdf"},
            actor_id="alice",
            user_request="Read my CV",
        )
    )
    assert read.success is True
    assert read.result["content"] == "A sample text CV."

    denied = asyncio.run(
        read_executor.request_execution(
            "file_read",
            {"path": str(outside)},
            actor_id="alice",
            user_request="Read outside workspace",
        )
    )
    assert denied.status == ToolExecutionStatus.FAILED

    sensitive = asyncio.run(
        read_executor.request_execution(
            "file_read",
            {"path": "Documents/.env"},
            actor_id="alice",
            user_request="Read environment file",
        )
    )
    assert sensitive.status == ToolExecutionStatus.FAILED
    assert "private=hidden" not in str(sensitive.model_dump())


def test_file_write_requires_confirmation_and_rejects_overwrite_without_admin(
    tmp_path,
) -> None:
    settings = ToolSettings(workspace_roots=[tmp_path], max_file_size_bytes=100)
    permissions = PermissionManager({"alice": [PermissionLevel.WRITE]})
    executor = build_executor(FileWriteTool(settings), permissions)

    pending = asyncio.run(
        executor.request_execution(
            "file_write",
            {"path": "notes.txt", "content": "first"},
            actor_id="alice",
            user_request="Create a note",
        )
    )
    assert pending.status == ToolExecutionStatus.CONFIRMATION_REQUIRED
    assert not (tmp_path / "notes.txt").exists()
    assert pending.confirmation_id is not None

    created = asyncio.run(
        executor.confirm_execution(pending.confirmation_id, actor_id="alice")
    )
    assert created.success is True
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "first"

    overwrite = asyncio.run(
        executor.request_execution(
            "file_write",
            {"path": "notes.txt", "content": "second", "mode": "overwrite"},
            actor_id="alice",
            user_request="Replace a note",
        )
    )
    assert overwrite.status == ToolExecutionStatus.PERMISSION_DENIED
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "first"

    append = asyncio.run(
        executor.request_execution(
            "file_write",
            {"path": "notes.txt", "content": " second", "mode": "append"},
            actor_id="alice",
            user_request="Append to a note",
        )
    )
    assert append.status == ToolExecutionStatus.CONFIRMATION_REQUIRED
    assert append.confirmation_id is not None
    appended = asyncio.run(
        executor.confirm_execution(append.confirmation_id, actor_id="alice")
    )
    assert appended.success is True
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "first second"


def test_file_tools_reject_symlink_paths_and_move_without_replacing(
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "private.txt").write_text("private", encoding="utf-8")
    (workspace / "escape").symlink_to(external, target_is_directory=True)
    (workspace / "source.txt").write_text("source", encoding="utf-8")
    (workspace / "target.txt").write_text("target", encoding="utf-8")
    settings = ToolSettings(workspace_roots=[workspace])
    permissions = PermissionManager({"alice": [PermissionLevel.READ, PermissionLevel.ADMIN]})
    read_executor = build_executor(FileReadTool(settings), permissions)
    move_executor = build_executor(FileMoveTool(settings), permissions)

    symlink_read = asyncio.run(
        read_executor.request_execution(
            "file_read",
            {"path": "escape/private.txt"},
            actor_id="alice",
            user_request="Read linked file",
        )
    )
    assert symlink_read.status == ToolExecutionStatus.FAILED

    pending = asyncio.run(
        move_executor.request_execution(
            "file_move",
            {"source": "source.txt", "destination": "target.txt"},
            actor_id="alice",
            user_request="Move a file",
        )
    )
    assert pending.status == ToolExecutionStatus.CONFIRMATION_REQUIRED
    assert pending.confirmation_id is not None
    failed_move = asyncio.run(
        move_executor.confirm_execution(pending.confirmation_id, actor_id="alice")
    )
    assert failed_move.status == ToolExecutionStatus.FAILED
    assert (workspace / "source.txt").read_text(encoding="utf-8") == "source"
    assert (workspace / "target.txt").read_text(encoding="utf-8") == "target"


def test_tool_settings_reject_unsafe_browser_host_entries() -> None:
    with pytest.raises(ValidationError):
        ToolSettings(browser_allowed_hosts=["127.0.0.1"])


def test_system_info_reports_host_metrics(tmp_path) -> None:
    settings = ToolSettings(workspace_roots=[tmp_path])
    permissions = PermissionManager({"alice": [PermissionLevel.READ]})
    executor = build_executor(SystemInfoTool(settings), permissions)

    response = asyncio.run(
        executor.request_execution(
            "system_info",
            {},
            actor_id="alice",
            user_request="How is the machine performing?",
        )
    )

    assert response.success is True
    assert response.result["cpu_cores_logical"] > 0
    assert response.result["memory_total_bytes"] > 0
    assert 0 <= response.result["cpu_percent"] <= 100

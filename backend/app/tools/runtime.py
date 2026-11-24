from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.core.config import ToolSettings
from app.projects.registry import ProjectRegistry
from app.tools.audit import AuditLogger
from app.tools.base import Tool
from app.tools.executor import ToolExecutor
from app.tools.implementations.browser import BrowserAutomationTool
from app.tools.implementations.clock import ClockTool
from app.tools.implementations.code_proposal import CodeProposalTool
from app.tools.implementations.developer import GitTool, OpenVSCodeTool, TerminalTool
from app.tools.implementations.docker import DockerManagementTool, DockerStatusTool
from app.tools.implementations.files import (
    FileMoveTool,
    FileReadTool,
    FileSearchTool,
    FileWriteTool,
)
from app.tools.implementations.project_launcher import ProjectLauncherTool
from app.tools.implementations.system import SystemInfoTool
from app.tools.implementations.web_search import WebSearchTool
from app.tools.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from app.services.code_proposals import CodeProposalManager


@dataclass(frozen=True)
class ToolRuntime:
    registry: ToolRegistry
    permission_manager: PermissionManager
    audit_logger: AuditLogger
    executor: ToolExecutor


def create_tool_runtime(
    tools: Iterable[Tool[Any, Any]],
    *,
    confirmation_ttl: timedelta = timedelta(minutes=5),
) -> ToolRuntime:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)

    permission_manager = PermissionManager()
    audit_logger = AuditLogger()
    executor = ToolExecutor(
        registry=registry,
        permission_manager=permission_manager,
        audit_logger=audit_logger,
        confirmation_ttl=confirmation_ttl,
    )
    return ToolRuntime(
        registry=registry,
        permission_manager=permission_manager,
        audit_logger=audit_logger,
        executor=executor,
    )


def create_default_tool_runtime(
    settings: ToolSettings,
    *,
    code_proposal_manager: CodeProposalManager | None = None,
) -> ToolRuntime:
    tools: list[Tool[Any, Any]] = [
        ClockTool(),
        FileSearchTool(settings),
        FileReadTool(settings),
        FileWriteTool(settings),
        FileMoveTool(settings),
        OpenVSCodeTool(settings),
        GitTool(settings),
        TerminalTool(settings),
        SystemInfoTool(settings),
        ProjectLauncherTool(
            ProjectRegistry.load(
                settings.projects_config_path,
                additional_roots=settings.project_roots,
            )
        ),
    ]
    if settings.web_search_enabled:
        tools.append(WebSearchTool(settings))
    if settings.browser_allowed_hosts:
        tools.append(BrowserAutomationTool(settings))
    if settings.docker_allowed_containers:
        tools.extend((DockerStatusTool(settings), DockerManagementTool(settings)))
    if code_proposal_manager is not None:
        tools.append(CodeProposalTool(code_proposal_manager))
    return create_tool_runtime(
        tools,
        confirmation_ttl=timedelta(seconds=settings.confirmation_ttl_seconds),
    )

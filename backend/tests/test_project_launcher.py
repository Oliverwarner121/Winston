import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from app.core.config import ToolSettings
from app.projects.registry import (
    ProjectConfigurationError,
    ProjectNotFoundError,
    ProjectRegistry,
)
from app.tools.audit import AuditLogger
from app.tools.executor import ToolExecutor
from app.tools.implementations.project_launcher import ProjectLauncherTool
from app.tools.models import ToolExecutionStatus
from app.tools.permissions import PermissionLevel, PermissionManager
from app.tools.registry import ToolRegistry


def _write_project_config(
    config_path,
    project_path: str,
    *,
    launch_vscode: bool = True,
    launch_terminal: bool = True,
) -> None:
    config_path.write_text(
        "\n".join(
            [
                "projects:",
                "  test-project:",
                "    name: Test Project",
                f"    path: {project_path}",
                "    description: Test workspace",
                "    launch:",
                f"      vscode: {str(launch_vscode).lower()}",
                f"      terminal: {str(launch_terminal).lower()}",
                "    startup_commands: []",
                "    metadata: {}",
            ]
        ),
        encoding="utf-8",
    )


def _build_executor(tool: ProjectLauncherTool) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(
        registry=registry,
        permission_manager=PermissionManager(
            {"alice": [PermissionLevel.WRITE]}
        ),
        audit_logger=AuditLogger(),
    )


def _audit_records(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        json.loads(record.getMessage().removeprefix("tool_execution "))
        for record in caplog.records
        if record.name == "winston.audit"
    ]


def test_project_registry_loads_paths_and_supports_id_or_name(tmp_path) -> None:
    project_path = tmp_path / "workspace"
    project_path.mkdir()
    config_path = tmp_path / "projects.yaml"
    _write_project_config(config_path, "workspace")

    registry = ProjectRegistry.load(config_path)

    assert registry.get_project("test-project").path == project_path
    assert registry.get_project("TEST PROJECT").id == "test-project"
    assert [project.id for project in registry.list_projects()] == ["test-project"]
    with pytest.raises(ProjectNotFoundError):
        registry.get_project("missing")


def test_project_registry_rejects_missing_project_paths(tmp_path) -> None:
    config_path = tmp_path / "projects.yaml"
    _write_project_config(config_path, "missing")

    with pytest.raises(ProjectConfigurationError, match="does not exist"):
        ProjectRegistry.load(config_path)


def test_project_registry_rejects_config_without_launch_action(tmp_path) -> None:
    project_path = tmp_path / "workspace"
    project_path.mkdir()
    config_path = tmp_path / "projects.yaml"
    _write_project_config(
        config_path,
        "workspace",
        launch_vscode=False,
        launch_terminal=False,
    )

    with pytest.raises(ProjectConfigurationError, match="at least one"):
        ProjectRegistry.load(config_path)


def test_project_registry_discovers_directories_under_additional_roots(
    tmp_path,
) -> None:
    projects_root = tmp_path / "Projects"
    project_path = projects_root / "Intel-Agent"
    project_path.mkdir(parents=True)
    config_path = tmp_path / "projects.yaml"
    _write_project_config(
        config_path,
        "workspace",
        launch_vscode=False,
        launch_terminal=True,
    )
    (tmp_path / "workspace").mkdir()

    registry = ProjectRegistry.load(config_path, additional_roots=[projects_root])

    project = registry.get_project("intel-agent")
    assert project.path == project_path
    assert project.launch_vscode is True
    assert project.launch_terminal is True
    definition = ProjectLauncherTool(registry).definition()
    assert "Intel-Agent" in definition.description


def test_project_launcher_opens_apps_without_confirmation(
    tmp_path,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_path = tmp_path / "workspace"
    project_path.mkdir()
    config_path = tmp_path / "projects.yaml"
    _write_project_config(config_path, "workspace")
    tool = ProjectLauncherTool(ProjectRegistry.load(config_path))
    executor = _build_executor(tool)
    executables = {
        "code": "/usr/bin/code",
        "gnome-terminal": "/usr/bin/gnome-terminal",
    }
    monkeypatch.setattr(
        "app.tools.implementations.project_launcher.shutil.which",
        executables.get,
    )
    launches: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(arguments: list[str], **kwargs: object) -> SimpleNamespace:
        launches.append((arguments, kwargs))
        return SimpleNamespace(pid=1000 + len(launches))

    monkeypatch.setattr(
        "app.tools.implementations.project_launcher.subprocess.Popen",
        fake_popen,
    )
    caplog.set_level(logging.INFO, logger="winston.audit")

    result = asyncio.run(
        executor.request_execution(
            "project_launcher",
            {"project": "test-project"},
            actor_id="alice",
            user_request="Open Test Project",
        )
    )
    assert result.success
    assert result.confirmed is False
    assert result.result["project_name"] == "Test Project"
    assert result.result["vscode_opened"] is True
    assert result.result["terminal_opened"] is True
    assert len(launches) == 2
    assert launches[0][0] == [
        "/usr/bin/code",
        "--reuse-window",
        str(project_path),
    ]
    assert launches[0][1]["cwd"] == project_path
    assert launches[1][0] == [
        "/usr/bin/gnome-terminal",
        "--working-directory",
        str(project_path),
    ]
    assert launches[1][1]["cwd"] == project_path

    audit = _audit_records(caplog)
    assert audit[-1]["status"] == "succeeded"
    assert audit[-1]["confirmed"] is False
    assert audit[-1]["inputs"] == {
        "project": "Test Project",
        "project_id": "test-project",
        "actions": ["vscode", "terminal"],
    }


def test_project_launcher_logs_missing_application_failure(
    tmp_path,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_path = tmp_path / "workspace"
    project_path.mkdir()
    config_path = tmp_path / "projects.yaml"
    _write_project_config(config_path, "workspace")
    tool = ProjectLauncherTool(ProjectRegistry.load(config_path))
    executor = _build_executor(tool)
    monkeypatch.setattr(
        "app.tools.implementations.project_launcher.shutil.which",
        lambda _name: None,
    )
    caplog.set_level(logging.INFO, logger="winston.audit")

    result = asyncio.run(
        executor.request_execution(
            "project_launcher",
            {"project": "test-project"},
            actor_id="alice",
            user_request="Open Test Project",
        )
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert result.error == "VS Code command 'code' is not available."
    assert _audit_records(caplog)[-1]["status"] == "failed"
    assert _audit_records(caplog)[-1]["error"] == result.error


def test_project_launcher_unknown_project_returns_audited_error(
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_path = tmp_path / "workspace"
    project_path.mkdir()
    config_path = tmp_path / "projects.yaml"
    _write_project_config(config_path, "workspace")
    executor = _build_executor(ProjectLauncherTool(ProjectRegistry.load(config_path)))
    caplog.set_level(logging.INFO, logger="winston.audit")

    result = asyncio.run(
        executor.request_execution(
            "project_launcher",
            {"project": "missing-project"},
            actor_id="alice",
            user_request="Open missing project",
        )
    )

    assert result.status == ToolExecutionStatus.FAILED
    assert result.error == "Configured project was not found."
    assert _audit_records(caplog)[-1]["status"] == "failed"


def test_default_runtime_registers_project_launcher() -> None:
    from app.tools.runtime import create_default_tool_runtime

    runtime = create_default_tool_runtime(ToolSettings())

    assert runtime.registry.get("project_launcher").id == "project_launcher"

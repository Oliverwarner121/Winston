import asyncio

from app.core.config import ToolSettings
from app.tools.base import Tool
from app.tools.executor import ToolExecutor
from app.tools.implementations.docker import DockerManagementTool, DockerStatusTool
from app.tools.implementations.process import ProcessResult
from app.tools.models import ToolExecutionStatus
from app.tools.permissions import PermissionLevel, PermissionManager
from app.tools.registry import ToolRegistry


def build_executor(tool: Tool, permissions: PermissionManager) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry, permissions)


def test_docker_status_only_returns_allowlisted_containers(tmp_path, monkeypatch) -> None:
    settings = ToolSettings(
        workspace_roots=[tmp_path],
        docker_allowed_containers=["vaultwarden"],
    )
    permissions = PermissionManager({"alice": [PermissionLevel.READ]})
    executor = build_executor(DockerStatusTool(settings), permissions)
    monkeypatch.setattr(
        "app.tools.implementations.docker.shutil.which",
        lambda command: "/usr/bin/docker",
    )

    async def fake_run_process(*arguments, **kwargs):
        return ProcessResult(
            return_code=0,
            stdout=(
                '{"Names":"vaultwarden","Image":"vaultwarden/server",'
                '"State":"running","Status":"Up 1 hour"}\n'
                '{"Names":"unlisted","Image":"other","State":"running",'
                '"Status":"Up 1 hour"}\n'
            ),
            stderr="",
            timed_out=False,
        )

    monkeypatch.setattr(
        "app.tools.implementations.docker.run_process",
        fake_run_process,
    )
    response = asyncio.run(
        executor.request_execution(
            "docker_status",
            {},
            actor_id="alice",
            user_request="Check containers",
        )
    )

    assert response.success is True
    assert response.result["containers"] == [
        {
            "name": "vaultwarden",
            "image": "vaultwarden/server",
            "state": "running",
            "status": "Up 1 hour",
        }
    ]


def test_docker_restart_does_not_prompt_and_checks_container_allowlist(
    tmp_path,
    monkeypatch,
) -> None:
    settings = ToolSettings(
        workspace_roots=[tmp_path],
        docker_allowed_containers=["vaultwarden"],
    )
    permissions = PermissionManager({"alice": [PermissionLevel.WRITE]})
    executor = build_executor(DockerManagementTool(settings), permissions)
    monkeypatch.setattr(
        "app.tools.implementations.docker.shutil.which",
        lambda command: "/usr/bin/docker",
    )
    calls: list[list[str]] = []

    async def fake_run_process(arguments, **kwargs):
        calls.append(arguments)
        return ProcessResult(
            return_code=0,
            stdout="vaultwarden",
            stderr="",
            timed_out=False,
        )

    monkeypatch.setattr(
        "app.tools.implementations.docker.run_process",
        fake_run_process,
    )
    restarted = asyncio.run(
        executor.request_execution(
            "docker_manage",
            {"operation": "restart", "container": "vaultwarden"},
            actor_id="alice",
            user_request="Restart Vaultwarden",
        )
    )
    assert restarted.success is True
    assert restarted.confirmed is False
    assert calls[0][-2:] == ["restart", "vaultwarden"]

    denied = asyncio.run(
        executor.request_execution(
            "docker_manage",
            {"operation": "stop", "container": "unlisted"},
            actor_id="alice",
            user_request="Stop another container",
        )
    )
    failed = denied
    assert failed.status == ToolExecutionStatus.FAILED
    assert failed.error == "Container is not in the configured allowlist."
    assert calls == [["/usr/bin/docker", "--context", "default", "restart", "vaultwarden"]]

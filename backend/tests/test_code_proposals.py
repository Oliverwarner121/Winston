import asyncio
import json
import subprocess

import pytest

from app.services.code_proposals import CodeProposalError, CodeProposalManager
from app.services.tool_agent import AgentRunStatus, ToolAgent
from app.tools.audit import AuditLogger
from app.tools.executor import ToolExecutor
from app.tools.implementations.code_proposal import CodeProposalTool
from app.tools.permissions import PermissionLevel, PermissionManager
from app.tools.registry import ToolRegistry


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = iter(responses)

    async def generate_response(self, *, system_prompt: str, user_prompt: str) -> str:
        return next(self._responses)


def _create_repository(tmp_path):
    root = tmp_path / "Winston"
    backend_app = root / "backend" / "app"
    backend_tests = root / "backend" / "tests"
    backend_app.mkdir(parents=True)
    backend_tests.mkdir(parents=True)
    (backend_app / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    (backend_tests / "test_example.py").write_text(
        "def test_value():\n    assert True\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def _provider_responses(new_content: str) -> list[str]:
    return [
        json.dumps({"paths": ["backend/app/example.py"]}),
        json.dumps(
            {
                "summary": "Update example value",
                "changes": [
                    {
                        "path": "backend/app/example.py",
                        "content": new_content,
                    }
                ],
            }
        ),
    ]


def test_code_proposal_is_previewed_without_modifying_repository(tmp_path) -> None:
    root = _create_repository(tmp_path)
    manager = CodeProposalManager(
        FakeProvider(_provider_responses("VALUE = 2\n")),
        repository_root=root,
    )

    proposal = asyncio.run(manager.propose("Change VALUE to 2", actor_id="local"))

    assert proposal.summary == "Update example value"
    assert "VALUE = 2" in proposal.diff
    assert proposal.files == ["backend/app/example.py"]
    assert (root / "backend/app/example.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_code_proposal_changes_files_only_after_approval(tmp_path) -> None:
    root = _create_repository(tmp_path)
    manager = CodeProposalManager(
        FakeProvider(_provider_responses("VALUE = 2\n")),
        repository_root=root,
    )
    proposal = asyncio.run(manager.propose("Change VALUE to 2", actor_id="local"))

    applied = asyncio.run(manager.apply(proposal.proposal_id, actor_id="local"))

    assert applied.status == "applied"
    assert (root / "backend/app/example.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    with pytest.raises(CodeProposalError, match="no longer available"):
        asyncio.run(manager.apply(proposal.proposal_id, actor_id="local"))


def test_proposal_rejects_paths_outside_approved_repository_areas(tmp_path) -> None:
    root = _create_repository(tmp_path)
    provider = FakeProvider(
        [
            json.dumps({"paths": ["backend/app/example.py"]}),
            json.dumps(
                {
                    "summary": "Attempt to edit a secret",
                    "changes": [{"path": "../../.env", "content": "SECRET=x\n"}],
                }
            ),
        ]
    )
    manager = CodeProposalManager(provider, repository_root=root)

    with pytest.raises(CodeProposalError, match="remain inside"):
        asyncio.run(manager.propose("Edit a secret", actor_id="local"))


def test_proposal_rejects_stale_file_snapshots(tmp_path) -> None:
    root = _create_repository(tmp_path)
    manager = CodeProposalManager(
        FakeProvider(_provider_responses("VALUE = 2\n")),
        repository_root=root,
    )
    proposal = asyncio.run(manager.propose("Change VALUE to 2", actor_id="local"))
    (root / "backend/app/example.py").write_text("VALUE = 3\n", encoding="utf-8")

    with pytest.raises(CodeProposalError, match="changed after"):
        asyncio.run(manager.apply(proposal.proposal_id, actor_id="local"))
    assert (root / "backend/app/example.py").read_text(encoding="utf-8") == "VALUE = 3\n"
    discarded = manager.discard(proposal.proposal_id, actor_id="local")
    assert discarded.status == "discarded"


def test_agent_returns_code_proposal_for_explicit_review(tmp_path) -> None:
    root = _create_repository(tmp_path)
    provider = FakeProvider(
        [
            json.dumps(
                {
                    "tool_id": "code_proposal",
                    "inputs": {"task": "Change VALUE to 2"},
                    "message": "",
                }
            ),
            json.dumps({"paths": ["backend/app/example.py"]}),
            json.dumps(
                {
                    "summary": "Update example value",
                    "changes": [
                        {
                            "path": "backend/app/example.py",
                            "content": "VALUE = 2\n",
                        }
                    ],
                }
            ),
        ]
    )
    manager = CodeProposalManager(provider, repository_root=root)
    code_tool = CodeProposalTool(manager)
    registry = ToolRegistry()
    registry.register(code_tool)
    permissions = PermissionManager({"local": [PermissionLevel.READ]})
    agent = ToolAgent(
        provider=provider,
        registry=registry,
        executor=ToolExecutor(registry, permissions, AuditLogger()),
        permission_manager=permissions,
    )

    response = asyncio.run(
        agent.run("Change VALUE to 2", actor_id="local")
    )

    assert response.status == AgentRunStatus.COMPLETED
    assert response.steps[0].tool_id == "code_proposal"
    assert response.steps[0].result["summary"] == "Update example value"
    assert "VALUE = 2" in response.steps[0].result["diff"]
    assert (root / "backend/app/example.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_proposal_can_create_a_new_skill_file_after_approval(tmp_path) -> None:
    root = _create_repository(tmp_path)
    provider = FakeProvider(
        [
            json.dumps({"paths": ["backend/app/example.py"]}),
            json.dumps(
                {
                    "summary": "Add a new capability",
                    "changes": [
                        {
                            "path": "backend/app/new_skill.py",
                            "content": "def run() -> str:\n    return 'ready'\n",
                        }
                    ],
                }
            ),
        ]
    )
    manager = CodeProposalManager(provider, repository_root=root)
    proposal = asyncio.run(manager.propose("Add a new skill", actor_id="local"))

    assert not (root / "backend/app/new_skill.py").exists()
    asyncio.run(manager.apply(proposal.proposal_id, actor_id="local"))
    assert (root / "backend/app/new_skill.py").read_text(encoding="utf-8") == (
        "def run() -> str:\n    return 'ready'\n"
    )


def test_backend_tests_run_only_after_explicit_apply_and_report_results(
    tmp_path,
) -> None:
    root = _create_repository(tmp_path)
    manager = CodeProposalManager(
        FakeProvider(_provider_responses("VALUE = 2\n")),
        repository_root=root,
    )
    proposal = asyncio.run(manager.propose("Change VALUE to 2", actor_id="local"))

    with pytest.raises(CodeProposalError, match="Apply the proposal"):
        asyncio.run(manager.run_tests(proposal.proposal_id, actor_id="local"))

    asyncio.run(manager.apply(proposal.proposal_id, actor_id="local"))
    result = asyncio.run(manager.run_tests(proposal.proposal_id, actor_id="local"))

    assert result.tests_passed is True
    assert "1 passed" in result.output

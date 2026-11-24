from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.services.code_proposals import CodeProposalManager
from app.tools.base import Tool, ToolContext
from app.tools.models import ToolCategory
from app.tools.permissions import PermissionLevel


class CodeProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=2000)


class CodeProposalOutput(BaseModel):
    proposal_id: str
    summary: str
    diff: str
    files: list[str]


class CodeProposalTool(Tool[CodeProposalInput, CodeProposalOutput]):
    id = "code_proposal"
    name = "Code proposal"
    description = (
        "Draft a reviewable patch for the Winston repository without applying it."
    )
    category = ToolCategory.DEVELOPMENT
    permission_level = PermissionLevel.READ
    input_model = CodeProposalInput
    output_model = CodeProposalOutput
    end_agent_run_after_success: ClassVar[bool] = True
    audit_safe_input_fields: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, manager: CodeProposalManager) -> None:
        self._manager = manager

    async def execute(
        self,
        inputs: CodeProposalInput,
        context: ToolContext,
    ) -> CodeProposalOutput:
        proposal = await self._manager.propose(
            inputs.task,
            actor_id=context.actor_id,
        )
        return CodeProposalOutput(
            proposal_id=str(proposal.proposal_id),
            summary=proposal.summary,
            diff=proposal.diff,
            files=proposal.files,
        )

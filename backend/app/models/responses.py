from pydantic import BaseModel
from uuid import UUID


class HealthResponse(BaseModel):
    status: str


class RootResponse(BaseModel):
    status: str
    assistant: str
    version: str


class ChatResponse(BaseModel):
    response: str
    confirmation_id: UUID | None = None
    confirmation_message: str | None = None
    proposal_id: UUID | None = None
    proposal_summary: str | None = None
    proposal_diff: str | None = None
    proposal_files: list[str] | None = None
    proposal_status: str | None = None
    test_output: str | None = None
    tests_passed: bool | None = None

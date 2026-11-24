from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.dependencies import get_winston_ai_service
from app.api.local_tool_access import (
    LOCAL_ACTOR_ID,
    get_code_proposal_manager,
    get_tool_agent,
)
from app.models.requests import ChatConfirmationRequest, ChatRequest
from app.models.responses import ChatResponse
from app.services.tool_agent import AgentRunResponse
from app.services.code_proposals import CodeActionResult, CodeProposalError
from app.services.ai_service import (
    AIProviderResponseError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)
from app.services.winston_ai_service import WinstonAIService


router = APIRouter(tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    response_model_exclude_none=True,
)
async def send_message(
    request: Request,
    chat_request: ChatRequest,
    ai_service: Annotated[WinstonAIService, Depends(get_winston_ai_service)],
) -> ChatResponse:
    try:
        agent = getattr(request.app.state, "tool_agent", None)
        if agent is not None:
            agent = get_tool_agent(request)
            agent_response = await agent.run(
                chat_request.message,
                actor_id=LOCAL_ACTOR_ID,
            )
            return _chat_response(agent_response)

        response = await ai_service.respond(chat_request.message)
    except AIProviderUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="The AI model service is unavailable.",
        ) from exc
    except AIProviderTimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="The AI model request timed out.",
        ) from exc
    except AIProviderResponseError as exc:
        raise HTTPException(
            status_code=502,
            detail="The AI model returned an invalid response.",
        ) from exc

    return ChatResponse(response=response)


@router.post(
    "/chat/confirmations/{confirmation_id}",
    response_model=ChatResponse,
    response_model_exclude_none=True,
)
async def respond_to_confirmation(
    confirmation_id: UUID,
    confirmation: ChatConfirmationRequest,
    request: Request,
) -> ChatResponse:
    agent = get_tool_agent(request)
    if confirmation.approved:
        try:
            response = await agent.confirm(
                confirmation_id,
                actor_id=LOCAL_ACTOR_ID,
            )
        except AIProviderUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Confirmation was processed, but the AI service is unavailable. "
                    "Check tool audit logs before retrying."
                ),
            ) from exc
        except AIProviderTimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail=(
                    "Confirmation was processed, but the AI service timed out. "
                    "Check tool audit logs before retrying."
                ),
            ) from exc
        except AIProviderResponseError as exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Confirmation was processed, but the AI response was invalid. "
                    "Check tool audit logs before retrying."
                ),
            ) from exc
    else:
        response = agent.cancel(
            confirmation_id,
            actor_id=LOCAL_ACTOR_ID,
        )
    return _chat_response(response)


@router.post(
    "/chat/code-proposals/{proposal_id}/apply",
    response_model=ChatResponse,
    response_model_exclude_none=True,
)
async def apply_code_proposal(
    proposal_id: UUID,
    request: Request,
) -> ChatResponse:
    manager = get_code_proposal_manager(request)
    try:
        result = await manager.apply(proposal_id, actor_id=LOCAL_ACTOR_ID)
    except CodeProposalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _code_action_response(result)


@router.post(
    "/chat/code-proposals/{proposal_id}/discard",
    response_model=ChatResponse,
    response_model_exclude_none=True,
)
async def discard_code_proposal(
    proposal_id: UUID,
    request: Request,
) -> ChatResponse:
    manager = get_code_proposal_manager(request)
    try:
        result = manager.discard(proposal_id, actor_id=LOCAL_ACTOR_ID)
    except CodeProposalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _code_action_response(result)


@router.post(
    "/chat/code-proposals/{proposal_id}/tests",
    response_model=ChatResponse,
    response_model_exclude_none=True,
)
async def run_code_proposal_tests(
    proposal_id: UUID,
    request: Request,
) -> ChatResponse:
    manager = get_code_proposal_manager(request)
    try:
        result = await manager.run_tests(proposal_id, actor_id=LOCAL_ACTOR_ID)
    except CodeProposalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _code_action_response(result)


def _chat_response(response: AgentRunResponse) -> ChatResponse:
    for step in response.steps:
        if step.tool_id == "code_proposal" and step.result is not None:
            return ChatResponse(
                response=response.message,
                proposal_id=step.result.get("proposal_id"),
                proposal_summary=step.result.get("summary"),
                proposal_diff=step.result.get("diff"),
                proposal_files=step.result.get("files"),
                proposal_status="pending",
            )
    return ChatResponse(
        response=(
            response.confirmation_message
            if response.confirmation_id and response.confirmation_message
            else response.message
        ),
        confirmation_id=response.confirmation_id,
        confirmation_message=response.confirmation_message,
    )


def _code_action_response(result: CodeActionResult) -> ChatResponse:
    return ChatResponse(
        response=result.message,
        proposal_id=result.proposal_id,
        proposal_summary=result.summary,
        proposal_diff=result.diff,
        proposal_files=result.files,
        proposal_status=result.status,
        test_output=result.output,
        tests_passed=result.tests_passed,
    )

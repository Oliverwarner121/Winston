from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_winston_ai_service
from app.models.requests import ChatRequest
from app.models.responses import ChatResponse
from app.services.ai_service import (
    AIProviderResponseError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)
from app.services.winston_ai_service import WinstonAIService


router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def send_message(
    request: ChatRequest,
    ai_service: Annotated[WinstonAIService, Depends(get_winston_ai_service)],
) -> ChatResponse:
    try:
        response = await ai_service.respond(request.message)
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

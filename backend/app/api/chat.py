from fastapi import APIRouter

from app.models.requests import ChatRequest
from app.models.responses import ChatResponse

router = APIRouter(prefix="/api", tags=["chat"])

@router.post("/chat", response_model=ChatResponse)
async def send_message(request: ChatRequest) -> ChatResponse:
    return ChatResponse(
        reply=(
            f"I received: {request.message}\n\n"
            "Winston's AI model is not connected yet."
        )
    )

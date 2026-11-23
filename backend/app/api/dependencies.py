from fastapi import Request

from app.services.winston_ai_service import WinstonAIService


def get_winston_ai_service(request: Request) -> WinstonAIService:
    return request.app.state.winston_ai_service

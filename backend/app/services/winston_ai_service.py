import logging
from time import perf_counter

from app.core.prompts import WINSTON_SYSTEM_PROMPT
from app.services.ai_service import (
    AIProvider,
    AIProviderError,
    AIProviderResponseError,
)


logger = logging.getLogger(__name__)


class WinstonAIService:
    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    async def respond(self, message: str) -> str:
        started_at = perf_counter()
        logger.info("Chat request received (message_chars=%d)", len(message))

        try:
            response = await self._provider.generate_response(
                system_prompt=WINSTON_SYSTEM_PROMPT,
                user_prompt=message,
            )
            response = response.strip()
            if not response:
                raise AIProviderResponseError
        except AIProviderError as exc:
            duration_ms = (perf_counter() - started_at) * 1000
            logger.warning(
                "Chat request failed (error=%s, duration_ms=%.1f)",
                type(exc).__name__,
                duration_ms,
            )
            raise

        duration_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "Chat response generated (response_chars=%d, duration_ms=%.1f)",
            len(response),
            duration_ms,
        )
        return response

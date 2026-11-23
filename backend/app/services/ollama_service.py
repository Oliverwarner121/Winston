import logging

import httpx

from app.services.ai_service import (
    AIProviderResponseError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)


logger = logging.getLogger(__name__)


class OllamaService:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )

    async def generate_response(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        logger.info(
            "Sending chat request to Ollama (model=%s, message_chars=%d)",
            self._model,
            len(user_prompt),
        )
        try:
            response = await self._client.post(
                "/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                },
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.warning("Ollama request timed out (model=%s)", self._model)
            raise AIProviderTimeoutError from exc
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Ollama returned an HTTP error (model=%s, status=%d)",
                self._model,
                exc.response.status_code,
            )
            raise AIProviderResponseError from exc
        except httpx.RequestError as exc:
            logger.warning(
                "Ollama connection failed (model=%s, error=%s)",
                self._model,
                type(exc).__name__,
            )
            raise AIProviderUnavailableError from exc

        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("Ollama returned invalid JSON (model=%s)", self._model)
            raise AIProviderResponseError from exc

        message = payload.get("message") if isinstance(payload, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            logger.warning("Ollama returned an empty response (model=%s)", self._model)
            raise AIProviderResponseError

        result = content.strip()
        logger.info(
            "Ollama response received (model=%s, response_chars=%d)",
            self._model,
            len(result),
        )
        return result

    async def close(self) -> None:
        await self._client.aclose()

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from app.services.ai_service import (
    AIProviderResponseError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)
from app.services.ollama_service import OllamaService


def run_service(
    handler: Callable[[httpx.Request], httpx.Response],
) -> str:
    async def call_service() -> str:
        service = OllamaService(
            base_url="http://ollama.test",
            model="qwen2.5:7b",
            timeout_seconds=1,
            transport=httpx.MockTransport(handler),
        )
        try:
            return await service.generate_response(
                system_prompt="Be Winston.",
                user_prompt="Hello",
            )
        finally:
            await service.close()

    return asyncio.run(call_service())


def test_ollama_service_posts_prompts_and_returns_model_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/api/chat"
        assert payload == {
            "model": "qwen2.5:7b",
            "messages": [
                {"role": "system", "content": "Be Winston."},
                {"role": "user", "content": "Hello"},
            ],
            "stream": False,
        }
        return httpx.Response(
            200,
            json={"message": {"content": "  Hello from Qwen.  "}},
            request=request,
        )

    assert run_service(handler) == "Hello from Qwen."


def test_ollama_service_maps_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(AIProviderUnavailableError):
        run_service(handler)


def test_ollama_service_maps_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("request timed out", request=request)

    with pytest.raises(AIProviderTimeoutError):
        run_service(handler)


def test_ollama_service_rejects_empty_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": "  "}},
            request=request,
        )

    with pytest.raises(AIProviderResponseError):
        run_service(handler)

from typing import Protocol


class AIProviderError(Exception):
    """Base class for errors returned by an AI provider."""


class AIProviderUnavailableError(AIProviderError):
    """The configured AI provider could not be reached."""


class AIProviderTimeoutError(AIProviderError):
    """The AI provider did not respond before the configured timeout."""


class AIProviderResponseError(AIProviderError):
    """The AI provider returned an invalid or empty response."""


class AIProvider(Protocol):
    async def generate_response(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str: ...

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.root import router as root_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.services.ollama_service import OllamaService
from app.services.winston_ai_service import WinstonAIService


configure_logging(settings.log_level)
logger = logging.getLogger("winston")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Initialize and release long-lived application services."""
    ollama_service = OllamaService(
        base_url=settings.ollama_url,
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )
    application.state.winston_ai_service = WinstonAIService(ollama_service)

    logger.info("Winston environment loaded successfully (%s)", settings.environment)
    logger.info(
        "Winston backend started successfully (model=%s)",
        settings.ollama_model,
    )
    try:
        yield
    finally:
        await ollama_service.close()
        logger.info("Winston backend shutting down")


app = FastAPI(
    title=settings.app_name,
    description=settings.app_description,
    version=settings.app_version,
    lifespan=lifespan,
)

app.include_router(root_router)
app.include_router(health_router)
app.include_router(chat_router)

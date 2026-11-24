import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import timedelta

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.root import router as root_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.services.code_proposals import CodeProposalManager
from app.services.ollama_service import OllamaService
from app.services.tool_agent import ToolAgent
from app.services.winston_ai_service import WinstonAIService
from app.tools.permissions import PermissionLevel
from app.tools.runtime import create_default_tool_runtime


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
    code_proposal_manager = (
        CodeProposalManager(ollama_service)
        if settings.features.tools_enabled
        else None
    )
    tool_runtime = create_default_tool_runtime(
        settings.tools,
        code_proposal_manager=code_proposal_manager,
    )
    application.state.tool_runtime = tool_runtime
    application.state.code_proposal_manager = code_proposal_manager
    if settings.features.tools_enabled:
        tool_runtime.permission_manager.grant(
            "winston-local-user",
            PermissionLevel.WRITE,
        )
        application.state.tool_agent = ToolAgent(
            provider=ollama_service,
            registry=tool_runtime.registry,
            executor=tool_runtime.executor,
            permission_manager=tool_runtime.permission_manager,
            max_steps=settings.tools.agent_max_steps,
            confirmation_ttl=timedelta(
                seconds=settings.tools.confirmation_ttl_seconds
            ),
        )
    else:
        application.state.tool_agent = None

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

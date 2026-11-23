from fastapi import APIRouter

from app.core.config import settings
from app.models.responses import RootResponse


router = APIRouter(tags=["application"])


@router.get("/", response_model=RootResponse)
async def read_root() -> RootResponse:
    return RootResponse(
        status="online",
        assistant=settings.app_name,
        version=settings.app_version,
    )

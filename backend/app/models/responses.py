from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class RootResponse(BaseModel):
    status: str
    assistant: str
    version: str


class ChatResponse(BaseModel):
    response: str

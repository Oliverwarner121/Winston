import logging

from app.tools.models import ToolAuditEvent


class AuditLogger:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("winston.audit")

    def record(self, event: ToolAuditEvent) -> None:
        self._logger.info("tool_execution %s", event.model_dump_json())

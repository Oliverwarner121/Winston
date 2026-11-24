import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from threading import RLock
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

from app.tools.audit import AuditLogger
from app.tools.base import Tool, ToolContext, ToolExecutionError
from app.tools.models import (
    ToolAuditEvent,
    ToolExecutionResponse,
    ToolExecutionStatus,
    utc_now,
)
from app.tools.permissions import PermissionManager
from app.tools.registry import ToolNotFoundError, ToolRegistry


_TOOL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


@dataclass(frozen=True)
class _PendingConfirmation:
    tool: Tool[Any, Any]
    inputs: BaseModel
    audit_inputs: dict[str, Any]
    actor_id: str
    user_request: str
    expires_at: datetime


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        permission_manager: PermissionManager,
        audit_logger: AuditLogger | None = None,
        confirmation_ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        if confirmation_ttl < timedelta(0):
            raise ValueError("Confirmation lifetime must not be negative.")

        self._registry = registry
        self._permission_manager = permission_manager
        self._audit_logger = audit_logger or AuditLogger()
        self._confirmation_ttl = confirmation_ttl
        self._pending_confirmations: dict[UUID, _PendingConfirmation] = {}
        self._confirmation_lock = RLock()

    async def request_execution(
        self,
        tool_id: str,
        inputs: Mapping[str, Any],
        *,
        actor_id: str,
        user_request: str,
    ) -> ToolExecutionResponse:
        self._expire_pending_confirmations()
        actor_id = actor_id.strip()
        if not actor_id or len(actor_id) > 200:
            return self._failure(
                tool_id="anonymous",
                actor_id="anonymous",
                user_request=user_request[:4000],
                inputs={key: "[REDACTED]" for key in inputs},
                status=ToolExecutionStatus.PERMISSION_DENIED,
                error="Permission denied.",
            )
        if not _TOOL_ID_PATTERN.fullmatch(tool_id):
            return self._failure(
                tool_id="invalid_tool_id",
                actor_id=actor_id,
                user_request=user_request[:4000],
                inputs={key: "[REDACTED]" for key in inputs},
                status=ToolExecutionStatus.INVALID_INPUT,
                error="Invalid tool ID.",
            )
        user_request = user_request[:4000]

        try:
            tool = self._registry.get(tool_id)
        except ToolNotFoundError:
            return self._failure(
                tool_id=tool_id,
                actor_id=actor_id,
                user_request=user_request,
                inputs={key: "[REDACTED]" for key in inputs},
                status=ToolExecutionStatus.TOOL_NOT_FOUND,
                error="Tool was not found.",
            )

        audit_inputs = tool.audit_inputs(inputs)
        if not self._permission_manager.is_allowed(
            actor_id,
            tool.minimum_permission_level(),
        ):
            return self._failure(
                tool_id=tool.id,
                actor_id=actor_id,
                user_request=user_request,
                inputs=audit_inputs,
                status=ToolExecutionStatus.PERMISSION_DENIED,
                error="Permission denied.",
            )

        try:
            validated_inputs = tool.input_model.model_validate(inputs)
        except ValidationError:
            return self._failure(
                tool_id=tool.id,
                actor_id=actor_id,
                user_request=user_request,
                inputs=audit_inputs,
                status=ToolExecutionStatus.INVALID_INPUT,
                error="Invalid tool inputs.",
            )

        audit_inputs = tool.audit_inputs(validated_inputs.model_dump(mode="json"))
        if not self._permission_manager.is_allowed(
            actor_id,
            tool.required_permission_level(validated_inputs),
        ):
            return self._failure(
                tool_id=tool.id,
                actor_id=actor_id,
                user_request=user_request,
                inputs=audit_inputs,
                status=ToolExecutionStatus.PERMISSION_DENIED,
                error="Permission denied.",
            )

        if tool.confirmation_required(validated_inputs):
            confirmation_id = uuid4()
            confirmation_message = tool.confirmation_message(validated_inputs)
            pending = _PendingConfirmation(
                tool=tool,
                inputs=validated_inputs,
                audit_inputs=audit_inputs,
                actor_id=actor_id,
                user_request=user_request,
                expires_at=utc_now() + self._confirmation_ttl,
            )
            with self._confirmation_lock:
                self._pending_confirmations[confirmation_id] = pending

            self._record_audit(
                tool_id=tool.id,
                actor_id=actor_id,
                user_request=user_request,
                inputs=audit_inputs,
                status=ToolExecutionStatus.CONFIRMATION_REQUIRED,
                execution_time_ms=0,
                confirmed=False,
                confirmation_id=confirmation_id,
            )
            return ToolExecutionResponse(
                success=False,
                tool_id=tool.id,
                status=ToolExecutionStatus.CONFIRMATION_REQUIRED,
                execution_time_ms=0,
                confirmed=False,
                confirmation_id=confirmation_id,
                confirmation_message=confirmation_message,
            )

        return await self._execute_tool(
            tool=tool,
            inputs=validated_inputs,
            audit_inputs=audit_inputs,
            actor_id=actor_id,
            user_request=user_request,
            confirmed=False,
            confirmation_id=None,
        )

    async def confirm_execution(
        self,
        confirmation_id: UUID,
        *,
        actor_id: str,
    ) -> ToolExecutionResponse:
        actor_id = actor_id.strip()
        self._expire_pending_confirmations(except_id=confirmation_id)
        now = utc_now()

        with self._confirmation_lock:
            pending = self._pending_confirmations.get(confirmation_id)
            if pending is None:
                return ToolExecutionResponse(
                    success=False,
                    tool_id="unknown",
                    status=ToolExecutionStatus.TOOL_NOT_FOUND,
                    execution_time_ms=0,
                    error="Confirmation request was not found.",
                )

            if pending.actor_id != actor_id:
                return self._failure(
                    tool_id=pending.tool.id,
                    actor_id=actor_id if len(actor_id) <= 200 else "anonymous",
                    user_request=pending.user_request,
                    inputs=pending.audit_inputs,
                    status=ToolExecutionStatus.PERMISSION_DENIED,
                    error="Permission denied.",
                    confirmation_id=confirmation_id,
                )

            del self._pending_confirmations[confirmation_id]

        if pending.expires_at <= now:
            return self._failure(
                tool_id=pending.tool.id,
                actor_id=actor_id or "anonymous",
                user_request=pending.user_request,
                inputs=pending.audit_inputs,
                status=ToolExecutionStatus.CONFIRMATION_EXPIRED,
                error="Confirmation request expired.",
                confirmation_id=confirmation_id,
            )

        if not self._permission_manager.is_allowed(
            actor_id,
            pending.tool.required_permission_level(pending.inputs),
        ):
            return self._failure(
                tool_id=pending.tool.id,
                actor_id=actor_id if len(actor_id) <= 200 else "anonymous",
                user_request=pending.user_request,
                inputs=pending.audit_inputs,
                status=ToolExecutionStatus.PERMISSION_DENIED,
                error="Permission denied.",
                confirmation_id=confirmation_id,
            )

        return await self._execute_tool(
            tool=pending.tool,
            inputs=pending.inputs,
            audit_inputs=pending.audit_inputs,
            actor_id=actor_id,
            user_request=pending.user_request,
            confirmed=True,
            confirmation_id=confirmation_id,
        )

    def cancel_confirmation(
        self,
        confirmation_id: UUID,
        *,
        actor_id: str,
    ) -> ToolExecutionResponse:
        actor_id = actor_id.strip()
        self._expire_pending_confirmations(except_id=confirmation_id)
        now = utc_now()
        with self._confirmation_lock:
            pending = self._pending_confirmations.get(confirmation_id)
            if pending is None:
                return ToolExecutionResponse(
                    success=False,
                    tool_id="unknown",
                    status=ToolExecutionStatus.TOOL_NOT_FOUND,
                    execution_time_ms=0,
                    error="Confirmation request was not found.",
                )
            if pending.actor_id != actor_id:
                return self._failure(
                    tool_id=pending.tool.id,
                    actor_id=actor_id,
                    user_request=pending.user_request,
                    inputs=pending.audit_inputs,
                    status=ToolExecutionStatus.PERMISSION_DENIED,
                    error="Permission denied.",
                    confirmation_id=confirmation_id,
                )
            del self._pending_confirmations[confirmation_id]

        if pending.expires_at <= now:
            return self._failure(
                tool_id=pending.tool.id,
                actor_id=actor_id,
                user_request=pending.user_request,
                inputs=pending.audit_inputs,
                status=ToolExecutionStatus.CONFIRMATION_EXPIRED,
                error="Confirmation request expired.",
                confirmation_id=confirmation_id,
            )

        return self._failure(
            tool_id=pending.tool.id,
            actor_id=actor_id,
            user_request=pending.user_request,
            inputs=pending.audit_inputs,
            status=ToolExecutionStatus.CONFIRMATION_CANCELLED,
            error="Confirmation cancelled.",
            confirmation_id=confirmation_id,
        )

    async def _execute_tool(
        self,
        *,
        tool: Tool[Any, Any],
        inputs: BaseModel,
        audit_inputs: dict[str, Any],
        actor_id: str,
        user_request: str,
        confirmed: bool,
        confirmation_id: UUID | None,
    ) -> ToolExecutionResponse:
        started_at = perf_counter()
        context = ToolContext(
            actor_id=actor_id,
            user_request=user_request,
            confirmed=confirmed,
            confirmation_id=confirmation_id,
        )

        try:
            output = await tool.execute(inputs, context)
            validated_output = tool.output_model.model_validate(output)
            result = validated_output.model_dump(mode="json")
        except asyncio.CancelledError:
            self._record_audit(
                tool_id=tool.id,
                actor_id=actor_id,
                user_request=user_request,
                inputs=audit_inputs,
                status=ToolExecutionStatus.CANCELLED,
                execution_time_ms=(perf_counter() - started_at) * 1000,
                confirmed=confirmed,
                confirmation_id=confirmation_id,
                error="Tool execution was cancelled.",
            )
            raise
        except ToolExecutionError as exc:
            return self._failure(
                tool_id=tool.id,
                actor_id=actor_id,
                user_request=user_request,
                inputs=audit_inputs,
                status=ToolExecutionStatus.FAILED,
                error=exc.public_message,
                execution_time_ms=(perf_counter() - started_at) * 1000,
                confirmed=confirmed,
                confirmation_id=confirmation_id,
            )
        except Exception as exc:
            return self._failure(
                tool_id=tool.id,
                actor_id=actor_id,
                user_request=user_request,
                inputs=audit_inputs,
                status=ToolExecutionStatus.FAILED,
                error=f"Tool execution failed ({type(exc).__name__}).",
                execution_time_ms=(perf_counter() - started_at) * 1000,
                confirmed=confirmed,
                confirmation_id=confirmation_id,
            )

        execution_time_ms = (perf_counter() - started_at) * 1000
        self._record_audit(
            tool_id=tool.id,
            actor_id=actor_id,
            user_request=user_request,
            inputs=audit_inputs,
            status=ToolExecutionStatus.SUCCEEDED,
            execution_time_ms=execution_time_ms,
            confirmed=confirmed,
            confirmation_id=confirmation_id,
        )
        return ToolExecutionResponse(
            success=True,
            tool_id=tool.id,
            status=ToolExecutionStatus.SUCCEEDED,
            result=result,
            execution_time_ms=execution_time_ms,
            confirmed=confirmed,
        )

    def _expire_pending_confirmations(self, except_id: UUID | None = None) -> None:
        now = utc_now()
        expired: list[tuple[UUID, _PendingConfirmation]] = []
        with self._confirmation_lock:
            for confirmation_id, pending in self._pending_confirmations.items():
                if confirmation_id != except_id and pending.expires_at <= now:
                    expired.append((confirmation_id, pending))
            for confirmation_id, _ in expired:
                del self._pending_confirmations[confirmation_id]

        for confirmation_id, pending in expired:
            self._record_audit(
                tool_id=pending.tool.id,
                actor_id=pending.actor_id,
                user_request=pending.user_request,
                inputs=pending.audit_inputs,
                status=ToolExecutionStatus.CONFIRMATION_EXPIRED,
                execution_time_ms=0,
                confirmed=False,
                confirmation_id=confirmation_id,
                error="Confirmation request expired.",
            )

    def _failure(
        self,
        *,
        tool_id: str,
        actor_id: str,
        user_request: str,
        inputs: dict[str, Any],
        status: ToolExecutionStatus,
        error: str,
        execution_time_ms: float = 0,
        confirmed: bool = False,
        confirmation_id: UUID | None = None,
    ) -> ToolExecutionResponse:
        self._record_audit(
            tool_id=tool_id,
            actor_id=actor_id,
            user_request=user_request,
            inputs=inputs,
            status=status,
            execution_time_ms=execution_time_ms,
            confirmed=confirmed,
            confirmation_id=confirmation_id,
            error=error,
        )
        return ToolExecutionResponse(
            success=False,
            tool_id=tool_id,
            status=status,
            execution_time_ms=execution_time_ms,
            error=error,
            confirmed=confirmed,
            confirmation_id=confirmation_id,
        )

    def _record_audit(
        self,
        *,
        tool_id: str,
        actor_id: str,
        user_request: str,
        inputs: dict[str, Any],
        status: ToolExecutionStatus,
        execution_time_ms: float,
        confirmed: bool,
        confirmation_id: UUID | None = None,
        error: str | None = None,
    ) -> None:
        self._audit_logger.record(
            ToolAuditEvent(
                tool_id=tool_id,
                actor_id=actor_id,
                user_request=user_request,
                inputs=inputs,
                status=status,
                execution_time_ms=execution_time_ms,
                confirmed=confirmed,
                confirmation_id=confirmation_id,
                error=error,
            )
        )

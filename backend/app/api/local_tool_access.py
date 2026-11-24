import ipaddress
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from app.services.tool_agent import ToolAgent
from app.services.code_proposals import CodeProposalManager


LOCAL_ACTOR_ID = "winston-local-user"


def require_local_tool_access(request: Request) -> None:
    client = request.client
    if client is None:
        raise HTTPException(
            status_code=403,
            detail="Tool actions are available only from this machine.",
        )

    address = client.host.partition("%")[0]
    try:
        parsed_address = ipaddress.ip_address(address)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="Tool actions are available only from this machine.",
        ) from None

    if isinstance(parsed_address, ipaddress.IPv6Address):
        parsed_address = parsed_address.ipv4_mapped or parsed_address
    if not parsed_address.is_loopback:
        raise _local_only_error()

    origin = request.headers.get("origin")
    if origin is not None and not _is_loopback_origin(origin):
        raise _local_only_error()


def _is_loopback_origin(origin: str) -> bool:
    try:
        parsed_origin = urlsplit(origin)
        hostname = parsed_origin.hostname
        if parsed_origin.scheme not in {"http", "https"} or hostname is None:
            return False
        if hostname.casefold() == "localhost":
            return True
        address = ipaddress.ip_address(hostname.partition("%")[0])
        if isinstance(address, ipaddress.IPv6Address):
            address = address.ipv4_mapped or address
        return address.is_loopback
    except ValueError:
        return False


def _local_only_error() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail="Tool actions are available only from this machine.",
    )


def get_tool_agent(request: Request) -> ToolAgent:
    agent = getattr(request.app.state, "tool_agent", None)
    if agent is None:
        raise HTTPException(
            status_code=503,
            detail="Tool-assisted chat is disabled.",
        )
    require_local_tool_access(request)
    return agent


def get_code_proposal_manager(request: Request) -> CodeProposalManager:
    manager = getattr(request.app.state, "code_proposal_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="Supervised coding is disabled.",
        )
    require_local_tool_access(request)
    return manager

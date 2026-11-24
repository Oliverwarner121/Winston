import asyncio

import httpx
import pytest

from app.core.config import ToolSettings
from app.tools.base import ToolContext, ToolExecutionError
from app.tools.implementations.browser import BrowserAutomationTool
from app.tools.implementations.web_search import WebSearchInput, WebSearchTool


def test_browser_policy_requires_exact_https_public_host(monkeypatch) -> None:
    settings = ToolSettings(browser_allowed_hosts=["example.com"])
    tool = BrowserAutomationTool(settings)
    monkeypatch.setattr(
        "app.tools.implementations.browser.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ],
    )

    asyncio.run(tool._validate_url("https://example.com/docs"))

    with pytest.raises(ToolExecutionError, match="host policy"):
        asyncio.run(tool._validate_url("https://other.example/"))
    with pytest.raises(ToolExecutionError, match="host policy"):
        asyncio.run(tool._validate_url("http://example.com/"))

    monkeypatch.setattr(
        "app.tools.implementations.browser.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(ToolExecutionError, match="Private and reserved"):
        asyncio.run(tool._validate_url("https://example.com/"))


def test_web_search_uses_opt_in_provider_and_parses_results() -> None:
    requested_urls: list[str] = []
    html = """
    <a class="result__a" href="https://example.com/releases">FastAPI release</a>
    <a class="result__snippet">A public release note.</a>
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, text=html)

    settings = ToolSettings(web_search_enabled=True)
    transport = httpx.MockTransport(handler)
    tool = WebSearchTool(settings, transport=transport)

    response = asyncio.run(
        tool.execute(
            WebSearchInput(query="latest FastAPI release"),
            context=ToolContext("alice", "search", False),
        )
    )

    assert response.provider == "DuckDuckGo"
    assert len(response.results) == 1
    assert response.results[0].title == "FastAPI release"
    assert response.results[0].url == "https://example.com/releases"
    assert response.results[0].snippet == "A public release note."
    assert "latest+FastAPI+release" in requested_urls[0]


def test_web_search_is_disabled_by_default() -> None:
    tool = WebSearchTool(ToolSettings())

    with pytest.raises(ToolExecutionError, match="disabled"):
        asyncio.run(
            tool.execute(
                WebSearchInput(query="latest FastAPI release"),
                context=ToolContext("alice", "search", False),
            )
        )

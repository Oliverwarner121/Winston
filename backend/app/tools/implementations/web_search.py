from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import ToolSettings
from app.tools.base import Tool, ToolContext, ToolExecutionError
from app.tools.models import ToolCategory
from app.tools.permissions import PermissionLevel


class WebSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=300)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, query: str) -> str:
        normalized = query.strip()
        if len(normalized) < 2:
            raise ValueError("Search query must contain at least two characters.")
        return normalized


class WebSearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class WebSearchOutput(BaseModel):
    provider: str
    results: list[WebSearchResult]


class WebSearchTool(Tool[WebSearchInput, WebSearchOutput]):
    id = "web_search"
    name = "Web search"
    description = "Search DuckDuckGo for public web pages and snippets."
    category = ToolCategory.BROWSER
    permission_level = PermissionLevel.READ
    input_model = WebSearchInput
    output_model = WebSearchOutput

    def __init__(
        self,
        settings: ToolSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def execute(
        self,
        inputs: WebSearchInput,
        context: ToolContext,
    ) -> WebSearchOutput:
        if not self._settings.web_search_enabled:
            raise ToolExecutionError("Web search is disabled in configuration.")
        timeout = httpx.Timeout(10, connect=5)
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
                headers={"User-Agent": "Winston/0.1 (local assistant)"},
            ) as client:
                async with client.stream(
                    "GET",
                    "https://html.duckduckgo.com/html/",
                    params={"q": inputs.query},
                ) as response:
                    if response.status_code != 200:
                        raise ToolExecutionError("Web search provider returned an error.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 2_000_000:
                            raise ToolExecutionError("Web search response exceeded its size limit.")
        except httpx.TimeoutException as exc:
            raise ToolExecutionError("Web search request timed out.") from exc
        except httpx.RequestError as exc:
            raise ToolExecutionError("Web search provider is unavailable.") from exc

        parser = _DuckDuckGoParser()
        parser.feed(body.decode("utf-8", errors="replace"))
        results = [
            WebSearchResult(
                title=title[:500],
                url=url,
                snippet=(
                    parser.snippets[index] if index < len(parser.snippets) else ""
                )[:1200],
            )
            for index, (title, url) in enumerate(parser.results[:10])
            if _is_public_web_url(url)
        ]
        return WebSearchOutput(provider="DuckDuckGo", results=results)


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[tuple[str, str]] = []
        self.snippets: list[str] = []
        self._title_active = False
        self._snippet_active = False
        self._title: list[str] = []
        self._snippet: list[str] = []
        self._url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            self._title_active = True
            self._title = []
            self._url = _unwrap_search_url(attributes.get("href") or "")
        if "result__snippet" in classes:
            self._snippet_active = True
            self._snippet = []

    def handle_data(self, data: str) -> None:
        if self._title_active:
            self._title.append(data)
        if self._snippet_active:
            self._snippet.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._title_active:
            title = " ".join("".join(self._title).split())
            if title and self._url:
                self.results.append((title, self._url))
            self._title_active = False
        if self._snippet_active and tag in {"a", "div", "span"}:
            snippet = " ".join("".join(self._snippet).split())
            if snippet:
                self.snippets.append(snippet)
            self._snippet_active = False


def _unwrap_search_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname and (
        parsed.hostname == "duckduckgo.com"
        or parsed.hostname.endswith(".duckduckgo.com")
    ):
        target = parse_qs(parsed.query).get("uddg", [])
        if target:
            return target[0]
    return url if url.startswith("https://") else ""


def _is_public_web_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.hostname)

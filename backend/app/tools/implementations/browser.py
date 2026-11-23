import asyncio
import ipaddress
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from playwright.async_api import Route

from app.core.config import ToolSettings
from app.tools.base import Tool, ToolContext, ToolExecutionError
from app.tools.models import ToolCategory
from app.tools.permissions import PermissionLevel


class BrowserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)


class BrowserOutput(BaseModel):
    url: str
    title: str
    text: str
    truncated: bool


class BrowserAutomationTool(Tool[BrowserInput, BrowserOutput]):
    id = "browser_read"
    name = "Read web page"
    description = (
        "Open an allowlisted HTTPS page and extract its title and visible text."
    )
    category = ToolCategory.BROWSER
    permission_level = PermissionLevel.READ
    input_model = BrowserInput
    output_model = BrowserOutput

    def __init__(self, settings: ToolSettings) -> None:
        self._settings = settings

    async def execute(
        self,
        inputs: BrowserInput,
        context: ToolContext,
    ) -> BrowserOutput:
        await self._validate_url(inputs.url)
        try:
            from playwright.async_api import Error as PlaywrightError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise ToolExecutionError(
                "Playwright is not installed; install the backend dependencies."
            ) from exc

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    browser_context = await browser.new_context(
                        accept_downloads=False,
                        java_script_enabled=False,
                        service_workers="block",
                    )
                    await browser_context.route("**/*", self._route_guard)
                    page = await browser_context.new_page()
                    page.set_default_navigation_timeout(
                        self._settings.browser_timeout_seconds * 1000
                    )
                    await page.goto(inputs.url, wait_until="domcontentloaded")
                    title = await page.title()
                    text = await page.locator("body").inner_text()
                finally:
                    await browser.close()
        except ToolExecutionError:
            raise
        except PlaywrightError as exc:
            raise ToolExecutionError("Unable to load the requested web page.") from exc

        limit = self._settings.browser_max_text_chars
        return BrowserOutput(
            url=inputs.url,
            title=title[:500],
            text=text[:limit],
            truncated=len(text) > limit,
        )

    async def _route_guard(self, route: "Route") -> None:
        try:
            await self._validate_url(route.request.url)
        except ToolExecutionError:
            await route.abort()
            return
        await route.continue_()

    async def _validate_url(self, url: str) -> None:
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").rstrip(".").casefold()
            port = parsed.port
        except ValueError as exc:
            raise ToolExecutionError("Invalid browser URL.") from exc
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or host
            not in {
                item.casefold().rstrip(".")
                for item in self._settings.browser_allowed_hosts
            }
        ):
            raise ToolExecutionError("URL is not allowed by the browser host policy.")

        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None:
            if not ip.is_global:
                raise ToolExecutionError("Private and reserved browser addresses are blocked.")
            addresses = {str(ip)}
        else:
            try:
                records = await asyncio.to_thread(
                    socket.getaddrinfo,
                    host,
                    443,
                    0,
                    socket.SOCK_STREAM,
                )
                addresses = {record[4][0] for record in records}
            except OSError as exc:
                raise ToolExecutionError("Browser host could not be resolved.") from exc
        try:
            if not addresses or any(
                not ipaddress.ip_address(address).is_global for address in addresses
            ):
                raise ToolExecutionError(
                    "Private and reserved browser addresses are blocked."
                )
        except ValueError as exc:
            raise ToolExecutionError("Browser host resolved to an invalid address.") from exc

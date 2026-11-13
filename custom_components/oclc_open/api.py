"""Async client for OCLC OPEN library accounts."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import aiohttp
from bs4 import BeautifulSoup

from . import parser
from .const import ACCOUNT_PATH

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=30)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HomeAssistant-oclc_open)",
    "Accept-Language": "de-DE,de;q=0.9",
}


class OpenError(Exception):
    """Base error."""


class CannotConnect(OpenError):
    """The OPAC could not be reached."""


class InvalidAuth(OpenError):
    """Card number or password rejected."""


class UnexpectedPage(OpenError):
    """The OPAC returned a page we cannot interpret."""


class RenewError(OpenError):
    """Renewal was refused or failed."""


class RenewalFeeError(RenewError):
    """Renewal would cost money and fees are not allowed."""

    def __init__(self, fee: Decimal) -> None:
        """Store the fee."""
        super().__init__(f"Renewal would cost {fee} EUR")
        self.fee = fee


@dataclass(slots=True)
class RenewOutcome:
    """Result of one renewal request."""

    message: str | None
    preview: list[dict[str, str]]


def create_session() -> aiohttp.ClientSession:
    """Session with its own cookie jar for one library account.

    OPEN's COPAUTH login cookie is base64. aiohttp quotes such values by
    default, the server then rejects the cookie and logs the session off.
    """
    return aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(quote_cookie=False))


class OpenClient:
    """Logs in to an OPEN web OPAC and reads/renews the account."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        username: str,
        password: str,
    ) -> None:
        """Initialize."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._lock = asyncio.Lock()

    @property
    def account_url(self) -> str:
        """URL of the account page."""
        return self._base_url + ACCOUNT_PATH

    def _abs(self, url: str) -> str:
        # Pages contain http:// links although the site enforces https.
        if self._base_url.startswith("https://") and url.startswith("http://"):
            return "https://" + url[len("http://") :]
        return url

    async def _request(self, method: str, url: str, **kwargs: Any) -> tuple[str, str]:
        try:
            async with self._session.request(
                method, self._abs(url), headers=HEADERS, timeout=TIMEOUT, **kwargs
            ) as resp:
                resp.raise_for_status()
                return await resp.text(), str(resp.url)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f"{method} {url} failed: {err}") from err

    async def _get(self, url: str) -> tuple[BeautifulSoup, str]:
        html, final_url = await self._request("GET", url)
        return parser.soup_of(html), final_url

    async def _post_form(
        self, url: str, fields: list[tuple[str, str]]
    ) -> tuple[BeautifulSoup, str]:
        html, final_url = await self._request(
            "POST", url, data=aiohttp.FormData(fields, charset="utf-8")
        )
        return parser.soup_of(html), final_url

    async def _account_page(self) -> tuple[BeautifulSoup, str]:
        """Return the account page, logging in when the session expired."""
        soup, url = await self._get(self.account_url)
        if parser.is_account_page(soup):
            return soup, url

        fields = parser.login_fields(soup, self._username, self._password)
        if fields is None:
            raise UnexpectedPage("Login form not found")
        action = parser.form_action(parser.main_form(soup), url)
        soup, url = await self._post_form(action, fields)

        if error := parser.login_error(soup):
            raise InvalidAuth(error)
        if "loggedoff" in url.lower():
            raise UnexpectedPage("The library ended the session right after login")
        if parser.is_account_page(soup):
            return soup, url
        # Some installations redirect to the start page after login.
        soup, url = await self._get(self.account_url)
        if parser.is_account_page(soup):
            return soup, url
        # No error message was shown, so the password was accepted; do not
        # report InvalidAuth (that would trigger a reauth flow).
        raise UnexpectedPage("Account page not found after login")

    async def async_validate(self) -> str | None:
        """Log in once and return the portal name; raises on failure."""
        async with self._lock:
            soup, _ = await self._account_page()
            return parser.site_name(soup)

    async def async_fetch(self) -> parser.AccountData:
        """Read loans, reservations and fees."""
        async with self._lock:
            soup, url = await self._account_page()
            try:
                data = parser.parse_account(soup)
            except parser.ParseError as err:
                raise UnexpectedPage(str(err)) from err
            await self._check_renewable(soup, url, data.loans)
            return data

    async def _check_renewable(
        self, soup: BeautifulSoup, url: str, loans: list[parser.Loan]
    ) -> None:
        copy_ids = [loan.copy_id for loan in loans if loan.copy_id and loan.renewable is None]
        if not copy_ids:
            return
        check = parser.extension_check(soup, url, self._username)
        payloads = (
            [parser.extension_payload(check, copy_ids)]
            if check.batch
            else [parser.single_extension_payload(check, cid) for cid in copy_ids]
        )
        for payload in payloads:
            try:
                async with self._session.post(
                    self._abs(check.url),
                    json=payload,
                    headers=HEADERS,
                    timeout=TIMEOUT,
                ) as resp:
                    resp.raise_for_status()
                    result = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                _LOGGER.debug("Renewability check failed: %s", err)
                return
            parser.apply_extension_results(loans, result)

    async def async_renew(
        self, copy_ids: list[str], allow_fees: bool = False
    ) -> RenewOutcome:
        """Renew the given copies. Verify the result by re-reading the account."""
        async with self._lock:
            soup, url = await self._account_page()
            try:
                fields = parser.renew_fields(soup, copy_ids)
            except parser.ParseError as err:
                raise RenewError(str(err)) from err
            action = parser.form_action(parser.main_form(soup), url)
            soup, url = await self._post_form(action, fields)
            preview = parser.parse_renew_preview(soup)
            _LOGGER.debug("Renewal preview: %s", preview)

            if parser.renew_succeeded(preview.message):
                return RenewOutcome(preview.message, preview.items)
            if not preview.items or preview.confirm_fields is None:
                raise RenewError(preview.message or "The library offered no renewal")
            if preview.fee_total and preview.fee_total > 0 and not allow_fees:
                raise RenewalFeeError(preview.fee_total)

            action = parser.form_action(parser.main_form(soup), url)
            soup, _ = await self._post_form(action, preview.confirm_fields)
            message = parser.renew_message(soup)
            if message and not parser.renew_succeeded(message):
                raise RenewError(message)
            return RenewOutcome(message, preview.items)

    async def async_logout(self) -> None:
        """Best-effort logout."""
        try:
            soup, url = await self._get(self.account_url)
            if link := parser.logoff_url(soup, url):
                await self._get(link)
        except OpenError:
            pass

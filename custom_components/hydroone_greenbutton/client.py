"""Authenticated requests to the same endpoints used by Hydro One's portal."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
import json
from urllib.parse import urljoin, urlsplit

import aiohttp

from .parser import FeedError, parse_feed

BASE = "https://www.hydroone.com"
LOGIN = "https://www.myaccount.hydroone.com/pkmslogin.form"
API = BASE + "/_vti_bin/DCEPServices/DCEPServices.svc/"
LOGIN_SETTINGS = (
    BASE + "/_api/lists/getbytitle('Settings')/items"
    "?$select=Key,Value&$filter=Key%20eq%20%27TivoliSignInUrl%27"
)
FEDERATION = (
    "https://www.myaccount.hydroone.com/isam/sps/wsfed/wsf"
    "?wa=wsignin1.0&wtrealm=urn%3aecustomer%3aprod"
    "&wctx=https%3a%2f%2fwww.hydroone.com%2fMyAccount_%2fSecure%2f_layouts%2f15%2f"
    "Authenticate.aspx%3fSource%3d%252Fmyaccount%252Fsecure"
)
ALLOWED_HOSTS = {"www.hydroone.com", "hydroone.com", "www.myaccount.hydroone.com"}


class PortalError(Exception):
    """The portal could not be reached or its response changed."""


class AuthenticationError(PortalError):
    """The Hydro One credentials or login session were rejected."""


class NoDataError(PortalError):
    """The requested date range has no available hourly consumption."""


@dataclass(frozen=True)
class Contract:
    account: str
    contract: str
    meter: str


class _Forms(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag.lower() == "form":
            self.current = {"action": values.get("action", ""), "fields": {}}
            self.forms.append(self.current)
        elif tag.lower() == "input" and self.current is not None:
            if values.get("type", "").lower() == "hidden" and values.get("name"):
                self.current["fields"][values["name"]] = values.get("value", "")

    def handle_endtag(self, tag):
        if tag.lower() == "form":
            self.current = None


def _allowed(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname in ALLOWED_HOSTS
                and parts.port in (None, 443) and parts.username is None and parts.password is None)
    except ValueError:
        return False


def _decode_json(body: str):
    try:
        value = json.loads(body)
        if isinstance(value, dict) and "d" in value:
            value = value["d"]
        if isinstance(value, str):
            value = json.loads(value)
        return value
    except (ValueError, TypeError) as err:
        raise PortalError("The Hydro One API returned an unexpected response") from err


class HydroOneClient:
    """A short-lived portal session; no browser cookies are copied or stored."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=90),
            headers={"User-Agent": "HomeAssistant-HydroOneGreenButton/0.1", "Accept": "application/json,text/html"},
        )
        return self

    async def __aexit__(self, *args):
        await self.session.close()

    async def _request(self, method: str, url: str, **kwargs):
        for _ in range(10):
            if not _allowed(url):
                raise PortalError("The portal redirected outside the allowed Hydro One HTTPS hosts")
            async with self.session.request(method, url, allow_redirects=False, **kwargs) as response:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise PortalError("The portal returned a redirect without a destination")
                    url = urljoin(str(response.url), location)
                    if response.status == 303 or (response.status in (301, 302) and method == "POST"):
                        method = "GET"
                        kwargs.pop("data", None)
                        kwargs.pop("json", None)
                    continue
                if response.status in (401, 403):
                    raise AuthenticationError("Hydro One did not accept the login session")
                if response.status >= 400:
                    raise PortalError(f"Hydro One returned HTTP {response.status}")
                data = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    data.extend(chunk)
                    if len(data) > 20 * 1024 * 1024:
                        raise PortalError("The Hydro One response exceeds 20 MiB")
                try:
                    body = data.decode(response.charset or "utf-8", errors="strict")
                except (UnicodeError, LookupError) as err:
                    raise PortalError("The portal returned invalid text encoding") from err
                return str(response.url), body
        raise PortalError("Too many Hydro One redirects")

    async def _submit_federation(self, url: str, body: str):
        for _ in range(4):
            forms = _Forms()
            forms.feed(body)
            form = next((f for f in forms.forms if "wresult" in f["fields"]), None)
            if form is None:
                return
            url, body = await self._request("POST", urljoin(url, form["action"]), data=form["fields"])
        raise PortalError("Too many federation forms")

    async def _customer(self):
        url, body = await self._request("GET", API + "Customer", headers={"MockedUser": ""})
        if "/login" in url.lower() or body.lstrip().startswith("<"):
            raise AuthenticationError("Hydro One redirected the API request to login")
        value = _decode_json(body)
        if not isinstance(value, dict) or not isinstance(value.get("Accounts"), list):
            raise AuthenticationError("Hydro One did not return authenticated customer accounts")
        return value

    async def login(self):
        await self._request("GET", BASE + "/login")
        login_url = LOGIN
        _, settings_body = await self._request("GET", LOGIN_SETTINGS, headers={
            "Accept": "application/json;odata=nometadata",
        })
        settings = _decode_json(settings_body)
        values = settings.get("value", []) if isinstance(settings, dict) else []
        if not isinstance(values, list):
            raise PortalError("The public login configuration has an unexpected format")
        for setting in values:
            if isinstance(setting, dict) and setting.get("Key") == "TivoliSignInUrl" and setting.get("Value"):
                login_url = urljoin(BASE, setting["Value"])
                break
        url, body = await self._request("POST", login_url, data={
            "username": self.username,
            "password": self.password,
            "UserId": "",
            "login-form-type": "pwd",
        })
        if "EC=0x" in url:
            raise AuthenticationError("Hydro One rejected the credentials")
        await self._submit_federation(url, body)
        try:
            return await self._customer()
        except AuthenticationError:
            # Some WebSEAL responses need the documented WS-Federation handoff.
            url, body = await self._request("GET", FEDERATION)
            await self._submit_federation(url, body)
            return await self._customer()

    @staticmethod
    def contracts(customer: dict) -> list[Contract]:
        result = []
        for account in customer.get("Accounts", []):
            container = account.get("Contracts", {})
            contracts = container.get("list", []) if isinstance(container, dict) else container
            for contract in contracts or []:
                if str(contract.get("BillingClass", "")).upper() == "UNM":
                    continue
                account_id = str(account.get("AccountId", ""))
                contract_id = str(contract.get("ContractId", ""))
                if account_id and contract_id:
                    meter = str(contract.get("KeyMeterSerialNo") or contract.get("PODId") or "Electricity")
                    result.append(Contract(account_id, contract_id, meter.lstrip("0")))
        return result

    async def download(self, account: str, contract: str, start: date, end: date):
        _, body = await self._request("POST", API + "GetGreenButtonDownloadMyData", json={
            "AccountNumber": account,
            "ContractId": contract,
            "FromDate": start.strftime("%Y%m%d"),
            "ToDate": end.strftime("%Y%m%d"),
        }, headers={"MockedUser": ""})
        documents = _decode_json(body)
        if not isinstance(documents, list):
            raise PortalError("The Green Button export response is not a document list")
        readings = {}
        for document in documents:
            if not isinstance(document, dict) or document.get("ResponseStatus") != 200:
                continue
            content = document.get("Content", "")
            if not isinstance(content, str):
                raise PortalError("The Green Button export is not XML text")
            if "IntervalReading" not in content:
                continue  # The accompanying customer-data XML is not energy.
            parsed = await asyncio.to_thread(parse_feed, content)
            for timestamp, value in parsed.items():
                if timestamp in readings and readings[timestamp] != value:
                    raise FeedError("Conflicting readings in the downloaded documents")
                readings[timestamp] = value
        if not readings:
            raise NoDataError("No hourly electricity readings are available for this date range")
        return readings

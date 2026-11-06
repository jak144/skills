"""Log in to a myAudi account and keep the access token fresh.

myAudi accounts live in the VW Group identity service (identity.vwgroup.io).
There is no public API: this module drives the same OAuth2 authorization-code
flow with PKCE that the myAudi smartphone app uses, by filling in the e-mail
and password forms of the login page, and then trades the code for tokens at
the CARIAD token endpoint.

The flow mirrors the open-source CarConnectivity Audi connector
(carconnectivity-connector-audi 0.3.2), which tracks Audi's changes to it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx2 as httpx

from .errors import AudiError, AuthenticationError, LoginFlowError

CLIENT_ID = "09b6cbec-cd19-4589-82fd-363dfa8c24da@apps_vw-dilab_com"
REDIRECT_URI = "myaudi:///"
SCOPE = "openid profile badge cars dealers vin"
IDENTITY_BASE = "https://identity.vwgroup.io"
AUTHORIZE_URL = f"{IDENTITY_BASE}/oidc/v1/authorize"
TOKEN_URL = "https://emea.bff.cariad.digital/auth/v1/idk/oidc/token"

LOGIN_HEADERS = {
    "user-agent": "myAudi-Android/4.14.1 (Android 11; SDK 30)",
    "accept": "application/json, text/plain, */*",
    "accept-language": "de-DE, de; q=0.9",
    "x-requested-with": "de.audi.myaudi",
    "x-app-version": "4.14.1",
    "x-app-name": "myAudi",
    "x-client-id": "59edf286-a9ca-4d34-9421-68da00f72dc8",
    "upgrade-insecure-requests": "1",
}

MAX_REDIRECTS = 20
_REDIRECT_CODES = {301, 302, 303, 307, 308}

_LOGIN_ERRORS = {
    "login.errors.password_invalid": "The myAudi password is wrong.",
    "login.error.throttled": (
        "Too many failed logins: the myAudi account is temporarily locked. Wait a few minutes before trying again."
    ),
}


@dataclass
class Token:
    access_token: str
    refresh_token: str | None
    expires_at: float

    def is_expired(self, leeway: float = 60.0) -> bool:
        return time.time() >= self.expires_at - leeway


class TokenStore:
    """Caches the token on disk so a restart of the server does not need a full login.

    The file holds tokens for the myAudi account, so it is written with mode 0600.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def default(cls) -> TokenStore:
        override = os.environ.get("MYAUDI_TOKEN_CACHE")
        if override:
            return cls(Path(override).expanduser())
        cache_home = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        return cls(cache_home / "myaudi-mcp" / "token.json")

    @staticmethod
    def _account_key(username: str) -> str:
        return hashlib.sha256(username.strip().lower().encode()).hexdigest()

    def load(self, username: str) -> Token | None:
        try:
            stored = json.loads(self.path.read_text())
            if stored.get("account") != self._account_key(username):
                return None
            return Token(**stored["token"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, username: str, token: Token) -> None:
        payload = json.dumps({"account": self._account_key(username), "token": asdict(token)})
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
        except OSError:
            # A cache that cannot be written only costs an extra login next time.
            pass

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


class _EmailFormParser(HTMLParser):
    """Collects the action and the input fields of <form id="emailPasswordForm">."""

    def __init__(self) -> None:
        super().__init__()
        self._inside = False
        self.action: str | None = None
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form" and attributes.get("id") == "emailPasswordForm":
            self._inside = True
            self.action = attributes.get("action")
        elif tag == "input" and self._inside and attributes.get("name"):
            self.fields[attributes["name"] or ""] = attributes.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._inside = False


_TEMPLATE_MODEL = re.compile(r"templateModel:\s*(.*?),\s*\n")
_CSRF_TOKEN = re.compile(r"csrf_token:\s*'([^']*)'")


def parse_email_form(html: str) -> tuple[str, dict[str, str]]:
    parser = _EmailFormParser()
    parser.feed(html)
    required = {"_csrf", "relayState", "hmac", "email"}
    if not parser.action or not required.issubset(parser.fields):
        raise LoginFlowError("The myAudi login page no longer has the expected e-mail form.")
    return parser.action, parser.fields


def parse_password_page(html: str) -> tuple[str, dict[str, str]]:
    """The password page keeps its form data in a `templateModel: {...}` JavaScript object."""
    match = _TEMPLATE_MODEL.search(html)
    if not match:
        raise LoginFlowError("The myAudi password page no longer has the expected form data.")
    try:
        model: dict[str, Any] = json.loads(match.group(1))
    except ValueError as exc:
        raise LoginFlowError("The myAudi password page has form data that is not valid JSON.") from exc

    if model.get("error"):
        if model["error"] == "validator.email.invalid":
            raise AuthenticationError("myAudi rejected the e-mail address as invalid.")
        raise AuthenticationError(f"myAudi login failed: {model['error']}")
    if model.get("errorCode"):
        raise AuthenticationError("myAudi login failed. Check that MYAUDI_USERNAME is the account's e-mail address.")
    if model.get("registerCredentialsPath") == "register":
        raise AuthenticationError("There is no myAudi account for this e-mail address.")

    csrf = _CSRF_TOKEN.search(html)
    fields = {key: str(model[key]) for key in ("relayState", "hmac") if model.get(key)}
    if csrf:
        fields["_csrf"] = csrf.group(1)
    post_action = model.get("postAction")
    if not post_action or set(fields) != {"relayState", "hmac", "_csrf"}:
        raise LoginFlowError("The myAudi password page is missing fields the login needs.")
    return str(post_action), fields


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
    return verifier, challenge


def _callback_params(url: str) -> dict[str, str]:
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query))
    params.update(parse_qsl(parts.fragment))
    return params


class AudiAuth:
    """Hands out a valid access token, logging in or refreshing when needed."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        store: TokenStore | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not username or not password:
            raise AuthenticationError(
                "MYAUDI_USERNAME and MYAUDI_PASSWORD are not set. Add them to the MCP server's environment."
            )
        self._username = username
        self._password = password
        self._store = store
        self._transport = transport
        self._timeout = timeout
        self._token: Token | None = store.load(username) if store else None
        self._lock = asyncio.Lock()

    async def access_token(self, *, force_refresh: bool = False) -> str:
        async with self._lock:
            token = self._token
            if token and not force_refresh and not token.is_expired():
                return token.access_token
            try:
                if token and token.refresh_token:
                    try:
                        return self._remember(await self._refresh(token.refresh_token)).access_token
                    except AuthenticationError:
                        pass  # The refresh token expired or was revoked: log in again.
                return self._remember(await self._login()).access_token
            except httpx.TimeoutException as exc:
                raise AudiError("The myAudi login service did not answer in time. Try again in a moment.") from exc
            except httpx.HTTPError as exc:
                raise AudiError(f"Could not reach the myAudi login service: {exc}") from exc

    def _remember(self, token: Token) -> Token:
        self._token = token
        if self._store:
            self._store.save(self._username, token)
        return token

    def _client(self) -> httpx.AsyncClient:
        # A new client per login gives every login a clean cookie jar.
        return httpx.AsyncClient(headers=LOGIN_HEADERS, timeout=self._timeout, transport=self._transport)

    async def _refresh(self, refresh_token: str) -> Token:
        async with self._client() as client:
            return await self._token_request(
                client,
                {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLIENT_ID},
                previous_refresh_token=refresh_token,
            )

    async def _login(self) -> Token:
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)
        params = {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "nonce": secrets.token_urlsafe(16),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        async with self._client() as client:
            response, url = await self._follow(client, "GET", str(httpx.URL(AUTHORIZE_URL, params=params)))
            if response is not None:
                action, fields = parse_email_form(response.text)
                fields["email"] = self._username
                response, url = await self._follow(client, "POST", urljoin(IDENTITY_BASE, action), data=fields)
                if response is None:
                    raise LoginFlowError("The myAudi login skipped the password page.")
                post_action, fields = parse_password_page(response.text)
                fields.update(email=self._username, password=self._password)
                url = await self._submit_password(client, post_action, fields)
                response, url = await self._follow(client, "GET", url)
                if response is not None:
                    raise AuthenticationError(
                        "The myAudi login stopped on a page that needs your input (for example new terms or a "
                        "consent). Log in once in the myAudi app or at my.audi.com, confirm it there, then try again."
                    )

            callback = _callback_params(url)
            if callback.get("error"):
                raise AuthenticationError(f"myAudi login failed: {callback['error']}")
            if callback.get("state") and callback["state"] != state:
                raise LoginFlowError("The myAudi login returned a different state than it was sent.")
            if not callback.get("code"):
                raise LoginFlowError("The myAudi login finished without an authorization code.")

            return await self._token_request(
                client,
                {
                    "grant_type": "authorization_code",
                    "code": callback["code"],
                    "redirect_uri": REDIRECT_URI,
                    "client_id": CLIENT_ID,
                    "code_verifier": verifier,
                },
            )

    async def _follow(
        self, client: httpx.AsyncClient, method: str, url: str, data: dict[str, str] | None = None
    ) -> tuple[httpx.Response | None, str]:
        """Follow redirects by hand, because the last one goes to the app's own myaudi:// scheme.

        Returns (None, callback_url) when the flow reached the app's redirect URI, otherwise the
        page it landed on.
        """
        for _ in range(MAX_REDIRECTS):
            if url.startswith(REDIRECT_URI):
                return None, url
            if "terms-and-conditions" in url:
                raise AuthenticationError(
                    "Audi asks you to accept updated terms and conditions. Log in once in the myAudi app or at "
                    "my.audi.com, accept them there, then try again."
                )
            response = await client.request(method, url, data=data)
            if response.status_code >= 500:
                raise AudiError(f"The myAudi login service has a temporary problem (HTTP {response.status_code}).")
            if response.status_code not in _REDIRECT_CODES:
                if response.status_code != 200:
                    raise LoginFlowError(f"The myAudi login page answered with HTTP {response.status_code}.")
                return response, url
            location = response.headers.get("location")
            if not location:
                raise LoginFlowError("The myAudi login redirected without a target.")
            url = urljoin(str(response.url), location)
            if response.status_code in (301, 302, 303):
                method, data = "GET", None
        raise LoginFlowError("The myAudi login redirected too many times.")

    async def _submit_password(self, client: httpx.AsyncClient, post_action: str, fields: dict[str, str]) -> str:
        url = f"{IDENTITY_BASE}/signin-service/v1/{CLIENT_ID}/{post_action}"
        response = await client.post(url, data=fields)
        if response.status_code >= 500:
            raise AudiError(f"The myAudi login service has a temporary problem (HTTP {response.status_code}).")
        location = response.headers.get("location")
        if response.status_code not in _REDIRECT_CODES or not location:
            raise LoginFlowError(f"The myAudi password form answered with HTTP {response.status_code}.")
        params = dict(parse_qsl(urlsplit(location).query))
        if params.get("error"):
            raise AuthenticationError(_LOGIN_ERRORS.get(params["error"], f"myAudi login failed: {params['error']}"))
        if not params.get("userId"):
            if params.get("updated") == "dataprivacy":
                raise AuthenticationError(
                    "Audi asks you to accept the updated privacy policy. Log in once at my.audi.com, accept it, "
                    "then try again."
                )
            raise LoginFlowError("The myAudi login did not return a user ID.")
        return urljoin(str(response.url), location)

    async def _token_request(
        self, client: httpx.AsyncClient, form: dict[str, str], previous_refresh_token: str | None = None
    ) -> Token:
        response = await client.post(TOKEN_URL, data=form, headers={"accept": "application/json"})
        if response.status_code in (400, 401, 403):
            raise AuthenticationError(f"The myAudi token service refused the request (HTTP {response.status_code}).")
        if response.status_code != 200:
            raise AudiError(f"The myAudi token service answered with HTTP {response.status_code}.")
        try:
            payload = response.json()
            access_token = payload["access_token"]
        except (ValueError, KeyError) as exc:
            raise LoginFlowError("The myAudi token service returned no access token.") from exc
        return Token(
            access_token=access_token,
            refresh_token=payload.get("refresh_token") or previous_refresh_token,
            expires_at=time.time() + int(payload.get("expires_in", 3600)),
        )

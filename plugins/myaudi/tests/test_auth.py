import json
import stat
import time

import httpx2 as httpx
import pytest

from fake_audi import PASSWORD, USERNAME, FakeAudi
from myaudi_mcp.auth import AudiAuth, Token, TokenStore, parse_password_page
from myaudi_mcp.errors import AudiError, AuthenticationError, LoginFlowError


async def test_login_walks_the_forms_and_exchanges_the_code_with_pkce():
    fake = FakeAudi()
    auth = AudiAuth(USERNAME, PASSWORD, transport=fake.transport())

    assert await auth.access_token() == "access-1"
    assert fake.logins == 1
    # A valid token is reused, not fetched again.
    assert await auth.access_token() == "access-1"
    assert fake.logins == 1


async def test_wrong_password_is_reported_plainly():
    fake = FakeAudi()
    auth = AudiAuth(USERNAME, "wrong", transport=fake.transport())

    with pytest.raises(AuthenticationError, match="password is wrong"):
        await auth.access_token()


async def test_missing_credentials_fail_before_any_request():
    with pytest.raises(AuthenticationError, match="MYAUDI_USERNAME"):
        AudiAuth("", "")


async def test_forced_refresh_uses_the_refresh_token():
    fake = FakeAudi()
    auth = AudiAuth(USERNAME, PASSWORD, transport=fake.transport())
    await auth.access_token()

    assert await auth.access_token(force_refresh=True) == "access-2"
    assert (fake.logins, fake.refreshes) == (1, 1)


async def test_rejected_refresh_falls_back_to_a_new_login():
    fake = FakeAudi()
    auth = AudiAuth(USERNAME, PASSWORD, transport=fake.transport())
    await auth.access_token()
    fake.refresh_works = False

    assert await auth.access_token(force_refresh=True) == "access-2"
    assert (fake.logins, fake.refreshes) == (2, 0)


async def test_token_cache_survives_a_restart_and_is_private(tmp_path):
    fake = FakeAudi()
    store = TokenStore(tmp_path / "cache" / "token.json")
    await AudiAuth(USERNAME, PASSWORD, store=store, transport=fake.transport()).access_token()

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert USERNAME not in store.path.read_text()

    restarted = AudiAuth(USERNAME, PASSWORD, store=store, transport=fake.transport())
    assert await restarted.access_token() == "access-1"
    assert fake.logins == 1
    # The cached token belongs to one account only.
    assert store.load("someone-else@example.com") is None


async def test_expired_cached_token_is_refreshed(tmp_path):
    fake = FakeAudi()
    store = TokenStore(tmp_path / "token.json")
    store.save(USERNAME, Token("old", "refresh-old", time.time() - 10))
    fake.access_token = "unused"

    auth = AudiAuth(USERNAME, PASSWORD, store=store, transport=fake.transport())
    assert await auth.access_token() == "access-1"
    assert (fake.logins, fake.refreshes) == (0, 1)
    assert json.loads(store.path.read_text())["token"]["refresh_token"] == "refresh-1"


async def test_terms_and_conditions_page_asks_the_user_to_act():
    fake = FakeAudi()
    original = fake._identity

    def identity(request):
        if request.url.path == "/oidc/v1/oauth/sso":
            from fake_audi import redirect

            return redirect("/signin-service/v1/terms-and-conditions?relayState=relay-1")
        return original(request)

    fake._identity = identity
    auth = AudiAuth(USERNAME, PASSWORD, transport=fake.transport())
    with pytest.raises(AuthenticationError, match="terms and conditions"):
        await auth.access_token()


def test_password_page_without_form_data_means_audi_changed_the_login():
    with pytest.raises(LoginFlowError):
        parse_password_page("<html><body>Neue Login-Seite</body></html>")


def test_password_page_reports_unknown_account():
    page = 'templateModel: {"registerCredentialsPath":"register","hmac":"h","relayState":"r"},\n'
    with pytest.raises(AuthenticationError, match="no myAudi account"):
        parse_password_page(page)


async def test_network_failure_is_a_readable_error():
    def unreachable(request):
        raise httpx.ConnectError("connection refused", request=request)

    auth = AudiAuth(USERNAME, PASSWORD, transport=httpx.MockTransport(unreachable))
    with pytest.raises(AudiError, match="Could not reach the myAudi login service"):
        await auth.access_token()

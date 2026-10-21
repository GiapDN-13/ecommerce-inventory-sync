import httpx
import pytest

from inventory_sync.http import ApiClient, ApiError, RetryPolicy, TokenProvider


def make_client(handler, *, with_auth=False, max_attempts=4):
    http = httpx.Client(base_url="https://api.example", transport=httpx.MockTransport(handler))
    sleeps: list[float] = []
    auth = TokenProvider(http, "oauth/token", "id", "secret") if with_auth else None
    api = ApiClient(http, auth=auth, retry=RetryPolicy(max_attempts=max_attempts), sleep=sleeps.append)
    return api, sleeps


def test_retries_429_and_honours_retry_after():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"ok": True})

    api, sleeps = make_client(handler)
    assert api.get("v1/thing").json() == {"ok": True}
    assert sleeps == [2.0, 2.0]
    assert api.retries == 2


def test_gives_up_after_max_attempts():
    api, sleeps = make_client(lambda r: httpx.Response(503), max_attempts=3)
    with pytest.raises(ApiError, match="HTTP 503"):
        api.get("v1/thing")
    assert len(sleeps) == 2


def test_client_errors_are_not_retried():
    api, sleeps = make_client(lambda r: httpx.Response(404, text="nope"))
    with pytest.raises(ApiError, match="404"):
        api.get("v1/thing")
    assert sleeps == []


def test_network_errors_are_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={})

    api, sleeps = make_client(handler)
    api.get("v1/thing")
    assert len(sleeps) == 1


def test_expired_token_is_refreshed_once_and_request_replayed():
    issued = []

    def handler(request):
        if request.url.path == "/oauth/token":
            issued.append(f"t{len(issued) + 1}")
            return httpx.Response(200, json={"access_token": issued[-1], "expires_in": 3600})
        # server considers the first token revoked
        if request.headers["Authorization"] == "Bearer t1":
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    api, _ = make_client(handler, with_auth=True)
    assert api.get("v1/stock").json() == {"ok": True}
    assert issued == ["t1", "t2"]


def test_persistent_401_raises_instead_of_looping():
    def handler(request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(401)

    api, _ = make_client(handler, with_auth=True)
    with pytest.raises(ApiError, match="401"):
        api.get("v1/stock")

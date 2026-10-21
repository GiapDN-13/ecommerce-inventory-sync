"""Resilient HTTP client: OAuth2 client-credentials tokens, retries with backoff, Retry-After support."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    """Raised when a request fails permanently (non-retryable status or retries exhausted)."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 0.5
    max_delay: float = 30.0

    def delay(self, attempt: int, retry_after: str | None = None) -> float:
        """Seconds to wait before the next attempt. Honors Retry-After, else exponential backoff + jitter."""
        if retry_after:
            try:
                return min(float(retry_after), self.max_delay)
            except ValueError:
                pass
        backoff = min(self.max_delay, self.base_delay * 2 ** (attempt - 1))
        return backoff * (0.5 + random.random() / 2)


class TokenProvider:
    """Caches an OAuth2 client-credentials token and refreshes it shortly before it expires."""

    def __init__(
        self,
        client: httpx.Client,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        refresh_margin: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_margin = refresh_margin
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self.fetch_count = 0

    def token(self) -> str:
        if self._token is None or self._clock() >= self._expires_at - self._refresh_margin:
            self._fetch()
        assert self._token is not None
        return self._token

    def invalidate(self) -> None:
        self._token = None

    def _fetch(self) -> None:
        resp = self._client.post(
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        if resp.is_error:
            raise ApiError(f"token request failed: HTTP {resp.status_code}")
        body = resp.json()
        self._token = body["access_token"]
        self._expires_at = self._clock() + float(body.get("expires_in", 300))
        self.fetch_count += 1
        log.debug("fetched new access token (expires in %ss)", body.get("expires_in"))


class ApiClient:
    """Thin wrapper over httpx.Client that adds auth, retries and clear errors."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        auth: TokenProvider | None = None,
        headers: dict[str, str] | None = None,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.auth = auth
        self.headers = headers or {}
        self.retry = retry or RetryPolicy()
        self._sleep = sleep
        self.retries = 0

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        extra_headers = kwargs.pop("headers", {})
        refreshed_token = False
        attempt = 0
        while True:
            attempt += 1
            headers = {**self.headers, **extra_headers}
            if self.auth:
                headers["Authorization"] = f"Bearer {self.auth.token()}"
            try:
                resp = self.client.request(method, path, headers=headers, **kwargs)
            except httpx.TransportError as exc:
                if attempt >= self.retry.max_attempts:
                    raise ApiError(f"{method} {path}: network error after {attempt} attempts: {exc}") from exc
                self._wait(attempt, None, f"network error: {exc}")
                continue

            if resp.status_code == 401 and self.auth and not refreshed_token:
                # Token was revoked or expired early: refresh once and replay the request.
                self.auth.invalidate()
                refreshed_token = True
                attempt -= 1
                continue
            if resp.status_code in RETRYABLE_STATUS and attempt < self.retry.max_attempts:
                self._wait(attempt, resp.headers.get("Retry-After"), f"HTTP {resp.status_code}")
                continue
            if resp.is_error:
                raise ApiError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
            return resp

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", path, **kwargs)

    def _wait(self, attempt: int, retry_after: str | None, reason: str) -> None:
        delay = self.retry.delay(attempt, retry_after)
        self.retries += 1
        log.warning("%s — retrying in %.1fs (attempt %d/%d)", reason, delay, attempt, self.retry.max_attempts)
        self._sleep(delay)

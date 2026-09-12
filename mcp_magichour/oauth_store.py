from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import asdict, dataclass
from threading import Lock
from time import monotonic, time
from urllib.parse import urlsplit

import httpx


CODE_TTL_SECONDS = 300
MAX_PENDING_CODES = 1_000
MAX_CODES_PER_API_KEY = 3


class OAuthCapacityError(Exception):
    pass


@dataclass(frozen=True)
class AuthorizationCode:
    api_key: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    resource: str | None
    expires_at: float


class AuthorizationCodeStore:
    """Small process-local store for short-lived, single-use codes."""

    def __init__(self, ttl_seconds: int = CODE_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._codes: dict[str, AuthorizationCode] = {}
        self._lock = Lock()

    async def issue(
        self,
        *,
        api_key: str,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        resource: str | None,
    ) -> str:
        code = secrets.token_urlsafe(32)
        now = monotonic()
        authorization_code = AuthorizationCode(
            api_key=api_key,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            resource=resource,
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._remove_expired(now)
            if sum(value.api_key == api_key for value in self._codes.values()) >= MAX_CODES_PER_API_KEY:
                raise OAuthCapacityError
            if len(self._codes) >= MAX_PENDING_CODES:
                raise OAuthCapacityError
            self._codes[code] = authorization_code
        return code

    async def consume(
        self, code: str, expected: AuthorizationCode | None = None
    ) -> AuthorizationCode | None:
        now = monotonic()
        with self._lock:
            self._remove_expired(now)
            if expected is not None and self._codes.get(code) != expected:
                return None
            authorization_code = self._codes.pop(code, None)
        if authorization_code is None or authorization_code.expires_at <= now:
            return None
        return authorization_code

    async def get(self, code: str) -> AuthorizationCode | None:
        now = monotonic()
        with self._lock:
            self._remove_expired(now)
            return self._codes.get(code)

    async def has_capacity(self, api_key: str) -> bool:
        now = monotonic()
        with self._lock:
            self._remove_expired(now)
            return (
                len(self._codes) < MAX_PENDING_CODES
                and sum(value.api_key == api_key for value in self._codes.values()) < MAX_CODES_PER_API_KEY
            )

    def _remove_expired(self, now: float) -> None:
        for code, value in list(self._codes.items()):
            if value.expires_at <= now:
                del self._codes[code]


class OAuthStoreUnavailable(Exception):
    """Storage failure that must never fall back to process-local codes."""


# All keys share a Redis hash tag. Redis time governs quota expiration across workers.
_CAPACITY_SCRIPT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) or
   redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[2]) then
    return 0
end
"""
_ISSUE_SCRIPT = _CAPACITY_SCRIPT + """
local ttl = tonumber(ARGV[3])
if not redis.call('SET', KEYS[3], ARGV[5], 'EX', ttl, 'NX') then
    return 0
end
redis.call('ZADD', KEYS[1], now + ttl, ARGV[4])
redis.call('ZADD', KEYS[2], now + ttl, ARGV[4])
redis.call('EXPIRE', KEYS[1], ttl)
redis.call('EXPIRE', KEYS[2], ttl)
return 1
"""
_CONSUME_SCRIPT = """
if redis.call('GET', KEYS[3]) ~= ARGV[2] then
    return 0
end
redis.call('DEL', KEYS[3])
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
return 1
"""


class RedisAuthorizationCodeStore:
    """Shared single-use codes through the Upstash-compatible Redis REST API."""

    def __init__(self, url: str, token: str, prefix: str, ttl_seconds: int = CODE_TTL_SECONDS):
        self.url = url
        self.token = token
        self.prefix = "{" + prefix + "}:"
        self.ttl_seconds = ttl_seconds

    def _keys(self, api_key: str, code: str | None = None) -> list[str]:
        keys = [self.prefix + "pending", self.prefix + "key:" + self._digest(api_key)]
        if code is not None:
            keys.append(self.prefix + "code:" + self._digest(code))
        return keys

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _encode(authorization: AuthorizationCode) -> str:
        return json.dumps(asdict(authorization), separators=(",", ":"), sort_keys=True)

    async def _command(self, *command: str | int) -> object:
        try:
            # One short-lived client avoids event-loop/lifespan coupling in serverless runtimes.
            # Never retry mutations: a timed-out consume may already have succeeded.
            async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                response = await client.post(
                    self.url, headers={"Authorization": "Bearer " + self.token}, json=command
                )
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload, dict) or "error" in payload or "result" not in payload:
                raise ValueError("Invalid Redis response")
            return payload["result"]
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            # Provider errors and request bodies may contain credentials; don't expose them.
            raise OAuthStoreUnavailable("Authorization storage is unavailable. Try again.") from None

    async def has_capacity(self, api_key: str) -> bool:
        return await self._command(
            "EVAL", _CAPACITY_SCRIPT + "return 1", 2, *self._keys(api_key),
            MAX_PENDING_CODES, MAX_CODES_PER_API_KEY,
        ) == 1

    async def issue(
        self, *, api_key: str, client_id: str, redirect_uri: str,
        code_challenge: str, resource: str | None,
    ) -> str:
        code = secrets.token_urlsafe(32)
        authorization = AuthorizationCode(
            api_key, client_id, redirect_uri, code_challenge, resource, time() + self.ttl_seconds
        )
        issued = await self._command(
            "EVAL", _ISSUE_SCRIPT, 3, *self._keys(api_key, code),
            MAX_PENDING_CODES, MAX_CODES_PER_API_KEY, self.ttl_seconds,
            self._digest(code), self._encode(authorization),
        )
        if issued != 1:
            raise OAuthCapacityError
        return code

    async def get(self, code: str) -> AuthorizationCode | None:
        # EVAL routes to the primary; a plain GET can miss a just-issued code on a replica.
        value = await self._command(
            "EVAL", "return redis.call('GET', KEYS[1])", 1,
            self.prefix + "code:" + self._digest(code),
        )
        if value is None:
            return None
        try:
            return AuthorizationCode(**json.loads(value))
        except (TypeError, ValueError):
            raise OAuthStoreUnavailable("Authorization storage returned an invalid code.") from None

    async def consume(self, code: str, expected: AuthorizationCode) -> AuthorizationCode | None:
        consumed = await self._command(
            "EVAL", _CONSUME_SCRIPT, 3, *self._keys(expected.api_key, code),
            self._digest(code), self._encode(expected),
        )
        return expected if consumed == 1 else None


def configured_code_store() -> AuthorizationCodeStore | RedisAuthorizationCodeStore:
    """Select explicit shared configuration; never silently fall back on Vercel."""
    url = os.getenv("UPSTASH_REDIS_REST_URL")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
    if not url and not token:
        url, token = os.getenv("KV_REST_API_URL"), os.getenv("KV_REST_API_TOKEN")
    if not url and not token and not os.getenv("VERCEL"):
        return AuthorizationCodeStore()
    if not url or not token:
        raise OAuthStoreUnavailable(
            "OAuth requires UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN "
            "(or KV_REST_API_URL and KV_REST_API_TOKEN) on Vercel."
        )
    try:
        parsed = urlsplit(url)
        parsed.port  # Validate a supplied port before constructing the HTTP client.
        valid_url = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                     and not parsed.password and not parsed.query and not parsed.fragment
                     and parsed.path in ("", "/"))
    except ValueError:
        valid_url = False
    if not valid_url or any(character.isspace() for character in token):
        raise OAuthStoreUnavailable("OAuth Redis configuration must use an HTTPS REST URL and token.")
    # Preview deployments need their own namespace; never inherit production's quota/code keys.
    prefix = os.getenv("MCP_OAUTH_REDIS_PREFIX")
    if not prefix:
        raise OAuthStoreUnavailable(
            "OAuth shared storage requires MCP_OAUTH_REDIS_PREFIX unique to this app/environment."
        )
    if len(prefix) > 128 or any(character in prefix for character in "{}"):
        raise OAuthStoreUnavailable("MCP_OAUTH_REDIS_PREFIX must be at most 128 characters without braces.")
    return RedisAuthorizationCodeStore(url, token, prefix)

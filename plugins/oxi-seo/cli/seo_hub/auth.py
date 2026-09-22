from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import jwt

from seo_hub.errors import SeoHubError
from seo_hub.registry import Registry


class AuthError(SeoHubError, PermissionError):
    code = "AUTH_FAILED"


@dataclass(frozen=True)
class Principal:
    kind: str
    identity: str
    email: str | None
    scopes: tuple[str, ...]

    def can(self, scope: str) -> bool:
        return scope in self.scopes


OWNER_SCOPES = ("read", "read-only-run", "ui")
SERVICE_SCOPES = ("read", "read-only-run")


@lru_cache(maxsize=16)
def _jwks_client(issuer: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        issuer + "/cdn-cgi/access/certs",
        timeout=5,
        cache_jwk_set=True,
        lifespan=300,
        cache_keys=False,
    )


def principal_from_cloudflare_access_jwt(
    token: str, *, registry: Registry, audience: str
) -> Principal:
    """Verify an HTTP Access assertion using only the configured team's JWKS.

    The caller selects the required application audience, never from token claims.
    """
    server = registry.server
    if server is None:
        raise AuthError("registry has no [server] section; Access verification is not configured.")
    domain = server.access_team_domain or ""
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com", domain):
        raise AuthError("Access team domain is invalid.")
    if not audience or audience not in (server.human_audience, server.service_audience):
        raise AuthError("Access JWT audience is invalid.")
    issuer = "https://" + domain
    try:
        # The untrusted header selects a key only; claims remain untrusted until decode.
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
            raise AuthError("Access JWT requires RS256 and a signing key ID.")
        signing_key = _jwks_client(issuer).get_signing_key(header["kid"])
    except (jwt.PyJWTError, OSError, ValueError, TypeError, AttributeError):
        # PyJWT can raise AttributeError for non-object entries in a malformed JWKS.
        raise AuthError("Access JWT signing key could not be verified.") from None
    pem = signing_key.key
    return principal_from_access_jwt(
        token, registry=registry, audience=audience, issuer=issuer,
        verification_key=pem,
    )


def local_noauth_principal(registry: Registry) -> Principal:
    server = registry.server
    bind = server.bind if server is not None else "127.0.0.1:0"
    owner_email = server.owner_email if server is not None else "local-owner@localhost"
    _assert_loopback_bind(bind)
    return Principal(kind="owner", identity="local-noauth", email=owner_email, scopes=OWNER_SCOPES)


def principal_from_access_jwt(
    token: str,
    *,
    registry: Registry,
    audience: str,
    issuer: str,
    verification_key: Any,
) -> Principal:
    """Verify RS256 with an explicitly trusted key (offline callers/tests)."""
    if not issuer or not audience:
        raise AuthError("Access JWT issuer and audience are required.")
    try:
        claims = jwt.decode(
            token, verification_key, algorithms=["RS256"], issuer=issuer, audience=audience,
            options={"require": ["iss", "aud", "exp"]},
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("Access JWT is expired.") from None
    except jwt.ImmatureSignatureError:
        raise AuthError("Access JWT is not yet valid.") from None
    except jwt.InvalidAudienceError:
        raise AuthError("Access JWT audience is invalid.") from None
    except jwt.InvalidIssuerError:
        raise AuthError("Access JWT issuer is invalid.") from None
    except jwt.MissingRequiredClaimError:
        raise AuthError("Access JWT issuer, audience and expiration are required.") from None
    except (jwt.PyJWTError, ValueError, TypeError, OverflowError):
        raise AuthError("Access JWT signature or claims are invalid.") from None
    if claims.get("iss") != issuer:
        raise AuthError("Access JWT issuer is invalid.")
    _validate_time_claims(claims)
    server = registry.server
    email = claims.get("email")
    sub = claims.get("sub")
    service_name = claims.get("common_name")
    if (
        server is not None
        and audience == server.human_audience
        and isinstance(email, str) and email and email == server.owner_email
        and isinstance(sub, str) and sub
    ):
        return Principal(kind="owner", identity=sub, email=email, scopes=OWNER_SCOPES)
    if (
        server is not None
        and audience == server.service_audience
        and isinstance(service_name, str) and service_name
        and service_name == server.service_common_name
    ):
        return Principal(kind="service", identity=service_name, email=email if isinstance(email, str) else None, scopes=SERVICE_SCOPES)
    raise AuthError("Access identity is not allowlisted for this hub.")


def require_scope(principal: Principal, scope: str) -> None:
    if not principal.can(scope):
        raise AuthError(f"principal does not have required scope: {scope}", scope=scope, principal_kind=principal.kind)


def _assert_loopback_bind(bind: str) -> None:
    host = _bind_host(bind)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise AuthError("local_noauth may only bind to loopback.", bind=bind)


def _bind_host(bind: str) -> str:
    if bind.startswith("["):
        closing = bind.find("]")
        if closing != -1:
            return bind[1:closing]
        return bind
    if bind.count(":") > 1:
        return bind
    return bind.rsplit(":", 1)[0]


def _validate_time_claims(claims: dict[str, Any]) -> None:
    now = datetime.now(UTC).timestamp()
    exp = _numeric_date_claim(claims.get("exp"), "expiration")
    if exp <= now:
        raise AuthError("Access JWT is expired.")
    for name in ("nbf", "iat"):
        if name in claims and _numeric_date_claim(claims[name], name) > now:
            raise AuthError("Access JWT is not yet valid.")


def _numeric_date_claim(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AuthError(f"Access JWT {label} claim is invalid.")
    try:
        numeric = float(value)
    except OverflowError:
        raise AuthError(f"Access JWT {label} claim is invalid.") from None
    if not math.isfinite(numeric):
        raise AuthError(f"Access JWT {label} claim is invalid.")
    return numeric

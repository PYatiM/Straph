"""Extract subject and resource context from an incoming request.

JWT claims are decoded here WITHOUT verifying the signature. Signature validation is the
responsibility of the API under observation; Straph only records what the caller presented.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

ANONYMOUS = "anonymous"
UNKNOWN = "unknown"
UNKNOWN_RESOURCE = "unknown"

_VERSION_SEGMENT = re.compile(r"^(api|v[0-9]+)$", re.IGNORECASE)
_TEMPLATE_SEGMENT = re.compile(r"^\{[^{}]+\}$|^:[A-Za-z_][A-Za-z0-9_]*$")
_NON_ID_SEGMENTS = frozenset({"webhook", "health", "ready", "metrics", "export"})


@dataclass(frozen=True)
class SubjectContext:
    """Caller identity as presented in the bearer token or gateway identity headers."""

    issuer: str
    sub: str
    role: str
    token_id: str = ""

    @property
    def is_anonymous(self) -> bool:
        """True when no usable bearer token or identity header was presented."""
        return self.sub == ANONYMOUS and self.issuer == ANONYMOUS


ANONYMOUS_SUBJECT = SubjectContext(issuer=ANONYMOUS, sub=ANONYMOUS, role=ANONYMOUS)


def _decode_claims(token: str) -> dict[str, Any] | None:
    """Return the JWT payload claims, or None if the token is malformed."""
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        return None
    segment = parts[1]
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        claims = json.loads(raw)
    except (binascii.Error, ValueError):
        return None
    return claims if isinstance(claims, dict) else None


def _string_claim(claims: Mapping[str, Any], name: str) -> str | None:
    value = claims.get(name)
    if isinstance(value, str) and value:
        return value
    return None


def _role_claim(claims: Mapping[str, Any]) -> str | None:
    value = claims.get("role")
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, str) and v), None)
    return value if isinstance(value, str) and value else None


def extract_subject_from_headers(headers: Mapping[str, str]) -> SubjectContext:
    """Parse `Authorization: Bearer <jwt>` or gateway `X-User-*` headers into a SubjectContext.

    Falls back to `X-User-Sub` and `X-User-Roles` headers if no Bearer token is provided.
    Falls back to the anonymous subject when neither is present or usable.
    """
    auth = next((v for k, v in headers.items() if k.lower() == "authorization"), None)
    if isinstance(auth, str):
        scheme, _, token = auth.strip().partition(" ")
        token = token.strip()
        if scheme.lower() == "bearer" and token:
            claims = _decode_claims(token)
            if claims is not None:
                sub = _string_claim(claims, "sub")
                if sub is not None:
                    return SubjectContext(
                        issuer=_string_claim(claims, "iss") or UNKNOWN,
                        sub=sub,
                        role=_role_claim(claims) or UNKNOWN,
                        token_id=_string_claim(claims, "jti") or "",
                    )

    # Check X-User-Sub / X-User-Roles fallback
    user_sub = next((v for k, v in headers.items() if k.lower() == "x-user-sub"), None)
    if isinstance(user_sub, str) and user_sub.strip():
        user_roles = next((v for k, v in headers.items() if k.lower() == "x-user-roles"), "")
        role = user_roles.split(",")[0].strip() if user_roles else UNKNOWN
        issuer = next((v for k, v in headers.items() if k.lower() == "x-user-iss"), "gateway")
        return SubjectContext(
            issuer=issuer,
            sub=user_sub.strip(),
            role=role or UNKNOWN,
            token_id="",
        )

    return ANONYMOUS_SUBJECT


def extract_resource_context(path: str, method: str) -> tuple[str, str | None]:
    """Return (resource_type, resource_id) for the top-level resource in a path.

    `/orders/1042/payment` -> ("orders", "1042"); `/orders` -> ("orders", None).
    Query strings, a leading `/api` or `/vN` prefix, and trailing slashes are ignored.
    A template placeholder such as `{id}` or `:id` is not a concrete id and yields None.
    `method` is accepted for interface symmetry: the resource is identified from the path
    alone, so a POST to a collection simply has no id until the response supplies one.
    """
    clean = path.split("?", 1)[0].split("#", 1)[0]
    segments = [s for s in clean.split("/") if s]
    while segments and _VERSION_SEGMENT.match(segments[0]):
        segments.pop(0)
    if not segments:
        return UNKNOWN_RESOURCE, None
    if clean.rstrip("/").endswith("/payments/webhook"):
        return "orders", None
    resource_type = unquote(segments[0])
    if len(segments) < 2:
        return resource_type, None
    candidate = segments[1]
    if _TEMPLATE_SEGMENT.match(candidate) or candidate.lower() in _NON_ID_SEGMENTS:
        return resource_type, None
    return resource_type, unquote(candidate)

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


@dataclass(frozen=True)
class SubjectContext:
    """Caller identity as presented in the bearer token."""

    issuer: str
    sub: str
    role: str
    token_id: str = ""

    @property
    def is_anonymous(self) -> bool:
        """True when no usable bearer token was presented."""
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
    """Parse `Authorization: Bearer <jwt>` into a SubjectContext.

    Falls back to the anonymous subject when the header is absent, is not a Bearer
    credential, or the token or its `sub` claim is unusable. A missing `iss` or `role`
    becomes "unknown". Header names are matched case-insensitively.
    """
    auth = next((v for k, v in headers.items() if k.lower() == "authorization"), None)
    if not isinstance(auth, str):
        return ANONYMOUS_SUBJECT
    scheme, _, token = auth.strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return ANONYMOUS_SUBJECT
    claims = _decode_claims(token)
    if claims is None:
        return ANONYMOUS_SUBJECT
    sub = _string_claim(claims, "sub")
    if sub is None:
        return ANONYMOUS_SUBJECT
    return SubjectContext(
        issuer=_string_claim(claims, "iss") or UNKNOWN,
        sub=sub,
        role=_role_claim(claims) or UNKNOWN,
        token_id=_string_claim(claims, "jti") or "",
    )


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
    resource_type = unquote(segments[0])
    if len(segments) < 2:
        return resource_type, None
    candidate = segments[1]
    if _TEMPLATE_SEGMENT.match(candidate):
        return resource_type, None
    return resource_type, unquote(candidate)

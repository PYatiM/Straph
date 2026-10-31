"""Tests for services/gateway/extractor.py."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from services.gateway.extractor import (
    ANONYMOUS_SUBJECT,
    SubjectContext,
    extract_resource_context,
    extract_subject_from_headers,
)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwt(claims: Any) -> str:
    header = b64(json.dumps({"alg": "none"}).encode())
    return f"{header}.{b64(json.dumps(claims).encode())}.sig"


def bearer(claims: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt(claims)}"}


# ---- JWT claims ----


def test_full_claims_parsed() -> None:
    s = extract_subject_from_headers(
        bearer({"sub": "user-A", "iss": "https://idp", "role": "customer", "jti": "tok-1"})
    )
    assert s == SubjectContext("https://idp", "user-A", "customer", "tok-1")
    assert not s.is_anonymous


def test_header_name_and_scheme_case_insensitive() -> None:
    headers = {"authorization": f"bearer {jwt({'sub': 'u', 'iss': 'i', 'role': 'r'})}"}
    assert extract_subject_from_headers(headers).sub == "u"


def test_missing_iss_role_jti_default() -> None:
    s = extract_subject_from_headers(bearer({"sub": "u"}))
    assert (s.issuer, s.role, s.token_id) == ("unknown", "unknown", "")


def test_role_list_uses_first_string() -> None:
    s = extract_subject_from_headers(bearer({"sub": "u", "role": ["admin", "x"]}))
    assert s.role == "admin"


def test_non_string_claims_ignored() -> None:
    s = extract_subject_from_headers(bearer({"sub": "u", "iss": 5, "role": 7}))
    assert (s.issuer, s.role) == ("unknown", "unknown")


def test_padding_free_base64_with_unicode() -> None:
    assert extract_subject_from_headers(bearer({"sub": "usér-é"})).sub == "usér-é"


# ---- malformed / absent ----


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Bearer a.b"},
        {"Authorization": "Bearer a..c"},
        {"Authorization": "Bearer a.!!!.c"},
        {"Authorization": f"Bearer a.{b64(b'not json')}.c"},
        {"Authorization": f"Bearer {jwt([1, 2])}"},
        {"Authorization": f"Bearer {jwt({'iss': 'i', 'role': 'r'})}"},
        {"Authorization": f"Bearer {jwt({'sub': ''})}"},
    ],
)
def test_malformed_falls_back_to_anonymous(headers: dict[str, str]) -> None:
    s = extract_subject_from_headers(headers)
    assert s == ANONYMOUS_SUBJECT
    assert s.is_anonymous


def test_anonymous_values() -> None:
    assert (ANONYMOUS_SUBJECT.sub, ANONYMOUS_SUBJECT.issuer) == ("anonymous", "anonymous")


# ---- resource context ----


@pytest.mark.parametrize(
    ("path", "method", "expected"),
    [
        ("/orders/1042", "GET", ("orders", "1042")),
        ("/orders/1042/payment", "POST", ("orders", "1042")),
        ("/orders", "POST", ("orders", None)),
        ("/orders/", "GET", ("orders", None)),
        ("/orders/{id}", "GET", ("orders", None)),
        ("/orders/{order_id}/payment", "POST", ("orders", None)),
        ("/orders/:id/cancel", "POST", ("orders", None)),
        ("/orders/1042?expand=items", "GET", ("orders", "1042")),
        ("/orders/1042/items/5", "DELETE", ("orders", "1042")),
        ("/api/v1/orders/7", "GET", ("orders", "7")),
        ("/v2/invoices/abc-9", "PUT", ("invoices", "abc-9")),
        ("/orders/a%2Fb", "GET", ("orders", "a/b")),
        ("/", "GET", ("unknown", None)),
        ("", "GET", ("unknown", None)),
    ],
)
def test_resource_context(path: str, method: str, expected: tuple[str, str | None]) -> None:
    assert extract_resource_context(path, method) == expected

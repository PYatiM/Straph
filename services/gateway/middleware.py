"""ASGI middleware that emits one APIEventPayload per completed HTTP call.

Privacy: request bodies are never read. A response body is read only for successful POST
creations whose id is not in the path, solely to pick the configured id field out of a JSON
object; the body is replayed unchanged to the client and is never logged or emitted. No header
values are logged. Only metadata (method, route template, ids, status, timestamps) is emitted.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from services.gateway.config import GatewaySettings
from services.gateway.extractor import extract_resource_context, extract_subject_from_headers
from services.gateway.models import APIEventPayload
from services.gateway.producer import EventProducerProtocol, event_key

log = logging.getLogger("straph.gateway.middleware")

SESSION_HEADERS = ("X-Session-Id", "session_id")
SERVER_ERROR = 500
CREATED_STATUSES = frozenset({200, 201})


def format_timestamp(moment: datetime) -> str:
    """Format as RFC 3339 UTC with microseconds, e.g. 2026-10-13T09:00:00.120000Z."""
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond:06d}Z"


def _route_template(request: Request) -> str:
    """Route template (e.g. /orders/{id}) when matched, else the concrete path."""
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template.startswith("/"):
        return template
    path = request.url.path
    return path if path.startswith("/") else f"/{path}"


def _first_header(request: Request, names: tuple[str, ...]) -> str:
    for name in names:
        value = request.headers.get(name)
        if value:
            return value
    return ""


def _created_resource_id(response: Response | None, resource_type: str) -> str | None:
    """Resource id from a Location header (never from the body), if it matches the type."""
    if response is None:
        return None
    location = response.headers.get("location")
    if not location:
        return None
    loc_type, loc_id = extract_resource_context(location, "GET")
    return loc_id if loc_type == resource_type else None


def _id_fields(mapping: Mapping[str, Sequence[str]], resource_type: str) -> Sequence[str]:
    return mapping.get(resource_type) or mapping.get("default", ("id",))


def _pick_id(document: Any, fields: Sequence[str]) -> str | None:
    """First configured field holding a non-empty string or integer id."""
    if not isinstance(document, dict):
        return None
    for name in fields:
        value = document.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str) and value:
            return value
    return None


def _is_json(response: Response) -> bool:
    content_type = response.headers.get("content-type", "")
    return content_type.split(";", 1)[0].strip().lower() == "application/json"


async def _peek_json_id(response: Response, fields: Sequence[str], max_bytes: int) -> str | None:
    """Extract an id from a JSON response body while keeping the stream intact for the client.

    Chunks are buffered up to `max_bytes`; the response iterator is then replaced by one that
    replays the buffered chunks followed by the rest of the original stream (and re-raises any
    error hit while peeking), so the caller always receives the complete, unmodified body.
    """
    body_iterator = getattr(response, "body_iterator", None)
    if body_iterator is None or not _is_json(response):
        return None
    source = body_iterator.__aiter__()
    buffered: list[bytes | str] = []
    size = 0
    finished = False
    failure: BaseException | None = None
    try:
        async for chunk in source:
            buffered.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                break
        else:
            finished = True
    except Exception as exc:  # noqa: BLE001 - replayed to the client below, not swallowed
        failure = exc

    async def replay() -> AsyncIterator[bytes | str]:
        for chunk in buffered:
            yield chunk
        if failure is not None:
            raise failure
        if not finished:
            async for chunk in source:
                yield chunk

    response.body_iterator = replay()
    if not finished:
        return None
    raw = b"".join(c if isinstance(c, bytes) else c.encode("utf-8") for c in buffered)
    try:
        return _pick_id(json.loads(raw), fields)
    except ValueError:
        return None


class EventInterceptorMiddleware(BaseHTTPMiddleware):
    """Emit an API event to the producer after every handled request."""

    def __init__(
        self,
        app: ASGIApp,
        producer: EventProducerProtocol,
        settings: GatewaySettings | None = None,
    ) -> None:
        super().__init__(app)
        self._producer = producer
        self._settings = settings or GatewaySettings()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self._settings.gateway_enabled:
            return await call_next(request)

        ts_received = datetime.now(UTC)
        session_id = _first_header(request, SESSION_HEADERS)
        idempotency_key = request.headers.get(self._settings.idempotency_header) or None

        response: Response | None = None
        status_code = SERVER_ERROR
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            ts_completed = datetime.now(UTC)
            await self._emit(
                request,
                response,
                status_code,
                ts_received,
                ts_completed,
                session_id,
                idempotency_key,
            )

    async def _emit(
        self,
        request: Request,
        response: Response | None,
        status_code: int,
        ts_received: datetime,
        ts_completed: datetime,
        session_id: str,
        idempotency_key: str | None,
    ) -> None:
        """Build and dispatch the event; never raises so the client response is unaffected."""
        try:
            subject = extract_subject_from_headers(request.headers)
            resource_type, resource_id = extract_resource_context(request.url.path, request.method)
            if resource_id is None:
                resource_id = _created_resource_id(response, resource_type)
            if (
                resource_id is None
                and response is not None
                and request.method.upper() == "POST"
                and status_code in CREATED_STATUSES
            ):
                resource_id = await _peek_json_id(
                    response,
                    _id_fields(self._settings.resource_id_fields, resource_type),
                    self._settings.resource_id_max_body_bytes,
                )
            data: dict[str, Any] = {
                "event_id": str(uuid.uuid4()),
                "ts_received": format_timestamp(ts_received),
                "ts_completed": format_timestamp(ts_completed),
                "subject": {"issuer": subject.issuer, "sub": subject.sub, "role": subject.role},
                "session_id": session_id,
                "token_id": subject.token_id,
                "operation": {
                    "method": request.method.upper(),
                    "path_template": _route_template(request),
                },
                "resource_type": resource_type,
                "resource_id": resource_id,
                "status_code": status_code,
                "idempotency_key": idempotency_key,
            }
            event = APIEventPayload.model_validate(data)
            await self._producer.produce_event(event_key(event), event)
        except Exception:
            log.exception("failed to emit API event method=%s", request.method)

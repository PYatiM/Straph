"""Pydantic v2 models mirroring contracts/event.schema.json (contract v1)."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

_TIMESTAMP_PATTERN = (
    r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
    r"T([01][0-9]|2[0-3]):[0-5][0-9]:([0-5][0-9]|60)(\.[0-9]+)?"
    r"(Z|[+-]([01][0-9]|2[0-3]):[0-5][0-9])$"
)
_UUID4_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)

Timestamp = Annotated[str, StringConstraints(pattern=_TIMESTAMP_PATTERN)]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]
HttpMethod = Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

_STRICT = ConfigDict(extra="forbid", populate_by_name=True, strict=True)


class Subject(BaseModel):
    """Authenticated caller."""

    model_config = _STRICT

    issuer: NonEmpty
    sub: NonEmpty
    role: NonEmpty


class Operation(BaseModel):
    """HTTP operation, identified by method and path template."""

    model_config = _STRICT

    method: HttpMethod
    path_template: str = Field(alias="path_template")

    @field_validator("path_template")
    @classmethod
    def _starts_with_slash(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("path_template must start with '/'")
        return value


class APIEventPayload(BaseModel):
    """One completed API call. Field order matches the contract."""

    model_config = _STRICT

    event_id: Annotated[str, StringConstraints(pattern=_UUID4_PATTERN)] = Field(alias="event_id")
    ts_received: Timestamp = Field(alias="ts_received")
    ts_completed: Timestamp = Field(alias="ts_completed")
    subject: Subject
    session_id: str = Field(alias="session_id")
    token_id: str = Field(alias="token_id")
    operation: Operation
    resource_type: str = Field(alias="resource_type")
    resource_id: str | None = Field(alias="resource_id")
    status_code: Annotated[int, Field(ge=100, le=599)] = Field(alias="status_code")
    idempotency_key: str | None = Field(default=None, alias="idempotency_key")

    def to_contract_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible dict using contract field names."""
        return self.model_dump(mode="json", by_alias=True)

    def to_contract_json(self) -> str:
        """Serialize to JSON text with contract field names and order."""
        return json.dumps(self.to_contract_dict())

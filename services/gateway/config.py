"""Gateway runtime configuration, loaded from the environment."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    """Environment-driven gateway settings (no secrets live here)."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    kafka_bootstrap_servers: str = Field(default="localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    events_topic: str = Field(default="api.events", alias="EVENTS_TOPIC")
    gateway_enabled: bool = Field(default=True, alias="GATEWAY_ENABLED")
    idempotency_header: str = Field(default="X-Idempotency-Key", alias="IDEMPOTENCY_HEADER")
    resource_id_fields: dict[str, tuple[str, ...]] = Field(
        default_factory=lambda: {"default": ("id",), "orders": ("order_id", "id")},
        alias="RESOURCE_ID_FIELDS",
        description=(
            "JSON map of resource_type -> candidate response fields holding the server-assigned "
            "id, tried in order. The 'default' key applies to unlisted resource types."
        ),
    )
    resource_id_max_body_bytes: int = Field(
        default=1_048_576, ge=0, alias="RESOURCE_ID_MAX_BODY_BYTES"
    )


def get_settings() -> GatewaySettings:
    """Load settings from the current environment."""
    return GatewaySettings()

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


def get_settings() -> GatewaySettings:
    """Load settings from the current environment."""
    return GatewaySettings()

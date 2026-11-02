"""Liveness route used by the container healthcheck."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

SERVICE_NAME = "straph-target-app"

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    service: str


@router.get("/health", response_model=HealthResponse, include_in_schema=False)
def health() -> HealthResponse:
    """Report that the app is up. Left out of the OpenAPI spec: it is not business API."""
    return HealthResponse(status="healthy", service=SERVICE_NAME)

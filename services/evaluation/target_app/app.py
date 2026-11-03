"""FastAPI application factory for the Straph evaluation target app."""

from __future__ import annotations

from fastapi import FastAPI

from services.evaluation.target_app.flaws import Flaws
from services.evaluation.target_app.openapi_export import install_openapi
from services.evaluation.target_app.repository import CartStore, OrderRepository
from services.evaluation.target_app.routes.health import router as health_router
from services.evaluation.target_app.routes.orders import router as orders_router


def create_app(*, strict: bool = True, flaws: Flaws | None = None) -> FastAPI:
    """Build the app with fresh in-memory state.

    Args:
        strict: Passed to ``OrderRepository``. False makes the app accept illegal
            state transitions, which is useful for attack simulations.
        flaws: Which planted flaws are active. When omitted they are read from the
            ``ALLOW_*`` environment variables (see ``flaws.py``), all off by default.
    """
    app = FastAPI(title="Straph target app")
    app.state.repository = OrderRepository(strict=strict)
    app.state.carts = CartStore()
    app.state.flaws = flaws if flaws is not None else Flaws.from_env()
    app.include_router(health_router)
    app.include_router(orders_router)
    install_openapi(app)
    return app


app = create_app()

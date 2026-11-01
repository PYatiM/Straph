"""FastAPI application factory for the Straph evaluation target app."""

from __future__ import annotations

from fastapi import FastAPI

from services.evaluation.target_app.repository import CartStore, OrderRepository
from services.evaluation.target_app.routes.orders import router as orders_router


def create_app(*, strict: bool = True) -> FastAPI:
    """Build the app with fresh in-memory state.

    Args:
        strict: Passed to ``OrderRepository``. False makes the app accept illegal
            state transitions, which is useful for attack simulations.
    """
    app = FastAPI(title="Straph target app")
    app.state.repository = OrderRepository(strict=strict)
    app.state.carts = CartStore()
    app.include_router(orders_router)
    return app


app = create_app()

"""Planted business-logic flaws for the Straph evaluation target app.

The target app is a deliberately simple shop used to measure how well Straph detects
workflow abuse. By default it behaves correctly. Each flaw below is OFF unless its
environment variable is set to a true value, so an evaluation run can switch on exactly
the weaknesses it wants to attack.

Environment variables (true values: ``true``, ``1``, ``yes``, ``on``; case-insensitive):

* ``ALLOW_SKIP_PAYMENT``: ``POST /orders/{id}/fulfill`` also works from ``ADDRESS_SET``.
* ``ALLOW_POST_SHIP_ADDRESS_EDIT``: ``PUT /orders/{id}/address`` also works when ``SHIPPED``.
* ``ALLOW_DOUBLE_REFUND``: ``POST /orders/{id}/refund`` can be repeated on a refunded order.
* ``ALLOW_BOLA_CANCEL``: ``POST /orders/{id}/cancel`` skips the owner check.

Never enable these flags on anything but a local test run.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

ENV_SKIP_PAYMENT = "ALLOW_SKIP_PAYMENT"
ENV_POST_SHIP_ADDRESS_EDIT = "ALLOW_POST_SHIP_ADDRESS_EDIT"
ENV_DOUBLE_REFUND = "ALLOW_DOUBLE_REFUND"
ENV_BOLA_CANCEL = "ALLOW_BOLA_CANCEL"

_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})


def _is_enabled(environ: Mapping[str, str], name: str) -> bool:
    return environ.get(name, "").strip().lower() in _TRUE_VALUES


@dataclass(frozen=True, slots=True)
class Flaws:
    """Which planted flaws are active. All default to off."""

    skip_payment: bool = False
    post_ship_address_edit: bool = False
    double_refund: bool = False
    bola_cancel: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Flaws:
        """Read the flags from ``environ`` (defaults to the real process environment)."""
        env = os.environ if environ is None else environ
        return cls(
            skip_payment=_is_enabled(env, ENV_SKIP_PAYMENT),
            post_ship_address_edit=_is_enabled(env, ENV_POST_SHIP_ADDRESS_EDIT),
            double_refund=_is_enabled(env, ENV_DOUBLE_REFUND),
            bola_cancel=_is_enabled(env, ENV_BOLA_CANCEL),
        )

    @property
    def enabled(self) -> tuple[str, ...]:
        """Environment-variable names of the flaws that are on (handy for logging)."""
        pairs = (
            (ENV_SKIP_PAYMENT, self.skip_payment),
            (ENV_POST_SHIP_ADDRESS_EDIT, self.post_ship_address_edit),
            (ENV_DOUBLE_REFUND, self.double_refund),
            (ENV_BOLA_CANCEL, self.bola_cancel),
        )
        return tuple(name for name, on in pairs if on)

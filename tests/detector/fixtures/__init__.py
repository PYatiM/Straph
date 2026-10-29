"""Detector test fixtures: TC1-TC10 events and expected alerts."""

from collections.abc import Callable
from typing import Any

from tests.detector.fixtures.tc1_legal_path import (
    get_tc1_events,
    get_tc1_expected_alerts,
)
from tests.detector.fixtures.tc2_skipped_payment import (
    get_tc2_events,
    get_tc2_expected_alerts,
)
from tests.detector.fixtures.tc3_address_after_shipping import (
    get_tc3_events,
    get_tc3_expected_alerts,
)
from tests.detector.fixtures.tc4_double_refund import (
    get_tc4_events,
    get_tc4_expected_alerts,
)
from tests.detector.fixtures.tc5_other_user_order import (
    get_tc5_events,
    get_tc5_expected_alerts,
)
from tests.detector.fixtures.tc6_wrong_role import (
    get_tc6_events,
    get_tc6_expected_alerts,
)
from tests.detector.fixtures.tc7_split_token_skip import (
    get_tc7_events,
    get_tc7_expected_alerts,
)
from tests.detector.fixtures.tc8_legal_retry import (
    get_tc8_events,
    get_tc8_expected_alerts,
)
from tests.detector.fixtures.tc9_legal_back_edit import (
    get_tc9_events,
    get_tc9_expected_alerts,
)
from tests.detector.fixtures.tc10_duplicate_delivery import (
    get_tc10_events,
    get_tc10_expected_alerts,
)

FixtureLoader = Callable[[], list[dict[str, Any]]]

ALL_FIXTURES: dict[str, tuple[FixtureLoader, FixtureLoader]] = {
    "tc1": (get_tc1_events, get_tc1_expected_alerts),
    "tc2": (get_tc2_events, get_tc2_expected_alerts),
    "tc3": (get_tc3_events, get_tc3_expected_alerts),
    "tc4": (get_tc4_events, get_tc4_expected_alerts),
    "tc5": (get_tc5_events, get_tc5_expected_alerts),
    "tc6": (get_tc6_events, get_tc6_expected_alerts),
    "tc7": (get_tc7_events, get_tc7_expected_alerts),
    "tc8": (get_tc8_events, get_tc8_expected_alerts),
    "tc9": (get_tc9_events, get_tc9_expected_alerts),
    "tc10": (get_tc10_events, get_tc10_expected_alerts),
}
"""Mapping of test-case id to ``(get_events, get_expected_alerts)`` loaders."""

__all__ = [
    "ALL_FIXTURES",
    "FixtureLoader",
    "get_tc1_events",
    "get_tc1_expected_alerts",
    "get_tc2_events",
    "get_tc2_expected_alerts",
    "get_tc3_events",
    "get_tc3_expected_alerts",
    "get_tc4_events",
    "get_tc4_expected_alerts",
    "get_tc5_events",
    "get_tc5_expected_alerts",
    "get_tc6_events",
    "get_tc6_expected_alerts",
    "get_tc7_events",
    "get_tc7_expected_alerts",
    "get_tc8_events",
    "get_tc8_expected_alerts",
    "get_tc9_events",
    "get_tc9_expected_alerts",
    "get_tc10_events",
    "get_tc10_expected_alerts",
]

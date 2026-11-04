"""Alert feature extraction module with zero ground-truth label leakage.

Extracts normalized numeric features strictly from alert dictionaries and instance history
records while strictly rejecting any ground-truth evaluation labels.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "LEAKAGE_FIELDS",
    "RULE_SEVERITY_MAP",
    "extract_features",
]

RULE_SEVERITY_MAP: dict[str, float] = {
    "R0": 4.0,
    "R1": 3.0,
    "R2": 2.0,
    "R3": 1.0,
    "R4": 0.5,
}

LEAKAGE_FIELDS: frozenset[str] = frozenset(
    {
        "is_violation",
        "expected_label",
        "expected_rule",
    }
)


def extract_features(
    alert: dict[str, Any],
    instance_history: list[dict[str, Any]],
    clean_stats: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Extract numeric features strictly from alert payload and instance history.

    Features extracted:
    1. rule_severity_code: float mapping (R0=4.0, R1=3.0, R2=2.0, R3=1.0, R4=0.5).
    2. is_accepted: 1.0 if severity == 'accepted' else 0.0.
    3. has_role_mismatch: 1.0 if label == 'IDENTITY_MISMATCH' else 0.0.
    4. missing_steps_count: float(len(alert.get('missing', []))).
    5. history_length: float(len(instance_history)).
    6. transition_rarity: float lookup from clean_stats (default 0.0).

    Raises:
        ValueError: If any ground-truth field ('is_violation', 'expected_label') is passed.
    """
    for forbidden in LEAKAGE_FIELDS:
        if forbidden in alert:
            raise ValueError(
                f"Ground-truth label leakage detected: '{forbidden}' is forbidden in alert payload."
            )
        if clean_stats and forbidden in clean_stats:
            raise ValueError(
                f"Ground-truth label leakage detected: '{forbidden}' is forbidden in clean_stats."
            )

    rule = str(alert.get("rule", ""))
    rule_severity_code = float(RULE_SEVERITY_MAP.get(rule, 0.0))

    is_accepted = 1.0 if alert.get("severity") == "accepted" else 0.0
    has_role_mismatch = 1.0 if alert.get("label") == "IDENTITY_MISMATCH" else 0.0

    missing = alert.get("missing", [])
    missing_steps_count = float(len(missing)) if isinstance(missing, list) else 0.0

    history_length = float(len(instance_history)) if isinstance(instance_history, list) else 0.0

    transition_rarity = 0.0
    if clean_stats:
        observed = alert.get("observed", {})
        op = str(observed.get("operation", "")) if isinstance(observed, dict) else ""
        state = str(observed.get("state", "")) if isinstance(observed, dict) else ""
        transition_key = f"{state}->{op}" if state else op

        if transition_key in clean_stats:
            transition_rarity = float(clean_stats[transition_key])
        elif op in clean_stats:
            transition_rarity = float(clean_stats[op])
        elif "transition_rarity" in clean_stats:
            transition_rarity = float(clean_stats["transition_rarity"])

    return {
        "rule_severity_code": rule_severity_code,
        "is_accepted": is_accepted,
        "has_role_mismatch": has_role_mismatch,
        "missing_steps_count": missing_steps_count,
        "history_length": history_length,
        "transition_rarity": transition_rarity,
    }

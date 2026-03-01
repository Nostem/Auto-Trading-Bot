"""
Parameter guardrails — single source of truth for tunable parameters with
min/max bounds. Imported by the reflection engine (to build LLM prompts and
validate output) and API routes (to validate on approve).
"""

TUNABLE_PARAMS: dict[str, dict] = {
    "bond_stop_loss_cents": {
        "description": "Bond stop-loss threshold in price units (e.g. 0.06 = 6¢ drop triggers exit)",
        "default": 0.06,
        "min": 0.02,
        "max": 0.10,
        "type": "float",
    },
    "stop_loss_threshold": {
        "description": "Percentage-based stop-loss for MM and BTC strategies (e.g. 0.50 = exit at 50% loss of entry value)",
        "default": 0.50,
        "min": 0.20,
        "max": 0.70,
        "type": "float",
    },
    "btc_take_profit_pct": {
        "description": "BTC take-profit percentage (e.g. 0.30 = exit at 30% gain)",
        "default": 0.30,
        "min": 0.10,
        "max": 0.60,
        "type": "float",
    },
    "mm_max_hold_hours": {
        "description": "Maximum hours to hold a market-making position before forced exit",
        "default": 4,
        "min": 1,
        "max": 12,
        "type": "int",
    },
    "bond_pre_expiry_sec": {
        "description": "Seconds before market close to exit bond positions",
        "default": 300,
        "min": 60,
        "max": 900,
        "type": "int",
    },
    "mm_pre_expiry_sec": {
        "description": "Seconds before market close to exit market-making positions",
        "default": 600,
        "min": 120,
        "max": 1800,
        "type": "int",
    },
    "btc_pre_expiry_sec": {
        "description": "Seconds before market close to exit BTC 15-min positions",
        "default": 60,
        "min": 15,
        "max": 300,
        "type": "int",
    },
    "max_position_pct": {
        "description": "Maximum single position size as fraction of bankroll (e.g. 0.15 = 15%)",
        "default": 0.15,
        "min": 0.05,
        "max": 0.25,
        "type": "float",
    },
    "daily_loss_limit_pct": {
        "description": "Daily loss limit as fraction of bankroll (e.g. 0.03 = 3%)",
        "default": 0.03,
        "min": 0.01,
        "max": 0.10,
        "type": "float",
    },
}


def validate_proposed_value(key: str, value) -> tuple[bool, str]:
    """Validate a proposed parameter value against guardrails.

    Returns (is_valid, error_message). error_message is empty on success.
    """
    if key not in TUNABLE_PARAMS:
        return False, f"Unknown parameter: {key}"

    spec = TUNABLE_PARAMS[key]

    # Coerce to the correct type
    try:
        if spec["type"] == "int":
            value = int(value)
        else:
            value = float(value)
    except (ValueError, TypeError):
        return False, f"Invalid value for {key}: cannot convert {value!r} to {spec['type']}"

    if value < spec["min"]:
        return False, f"{key} value {value} is below minimum {spec['min']}"
    if value > spec["max"]:
        return False, f"{key} value {value} is above maximum {spec['max']}"

    return True, ""

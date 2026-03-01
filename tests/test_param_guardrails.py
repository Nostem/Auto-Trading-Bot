"""Tests for bot.intelligence.param_guardrails."""
from bot.intelligence.param_guardrails import TUNABLE_PARAMS, validate_proposed_value


class TestTunableParams:
    def test_all_params_have_required_keys(self):
        required = {"description", "default", "min", "max", "type"}
        for key, spec in TUNABLE_PARAMS.items():
            assert required.issubset(spec.keys()), f"{key} missing keys: {required - spec.keys()}"

    def test_defaults_within_bounds(self):
        for key, spec in TUNABLE_PARAMS.items():
            assert spec["min"] <= spec["default"] <= spec["max"], (
                f"{key}: default {spec['default']} not in [{spec['min']}, {spec['max']}]"
            )

    def test_type_is_valid(self):
        for key, spec in TUNABLE_PARAMS.items():
            assert spec["type"] in ("float", "int"), f"{key}: unknown type {spec['type']}"


class TestValidateProposedValue:
    def test_valid_float(self):
        ok, err = validate_proposed_value("bond_stop_loss_cents", 0.05)
        assert ok
        assert err == ""

    def test_valid_int(self):
        ok, err = validate_proposed_value("mm_max_hold_hours", 6)
        assert ok
        assert err == ""

    def test_string_coercion(self):
        ok, err = validate_proposed_value("bond_stop_loss_cents", "0.05")
        assert ok

    def test_below_min(self):
        ok, err = validate_proposed_value("bond_stop_loss_cents", 0.01)
        assert not ok
        assert "below minimum" in err

    def test_above_max(self):
        ok, err = validate_proposed_value("bond_stop_loss_cents", 0.20)
        assert not ok
        assert "above maximum" in err

    def test_unknown_key(self):
        ok, err = validate_proposed_value("nonexistent_param", 1.0)
        assert not ok
        assert "Unknown parameter" in err

    def test_invalid_type(self):
        ok, err = validate_proposed_value("bond_stop_loss_cents", "not_a_number")
        assert not ok
        assert "cannot convert" in err

    def test_boundary_min(self):
        ok, _ = validate_proposed_value("bond_stop_loss_cents", 0.02)
        assert ok

    def test_boundary_max(self):
        ok, _ = validate_proposed_value("bond_stop_loss_cents", 0.10)
        assert ok

    def test_int_param_rejects_out_of_range(self):
        ok, err = validate_proposed_value("mm_max_hold_hours", 0)
        assert not ok
        assert "below minimum" in err

    def test_all_defaults_validate(self):
        for key, spec in TUNABLE_PARAMS.items():
            ok, err = validate_proposed_value(key, spec["default"])
            assert ok, f"{key} default {spec['default']} failed: {err}"

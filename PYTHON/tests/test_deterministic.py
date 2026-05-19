"""Tests for maatproof.layers.deterministic — deterministic gate layer."""

import pytest

from maatproof.layers.deterministic import (
    DeterministicGate,
    DeterministicLayer,
    GateResult,
    GateStatus,
)


# ---------------------------------------------------------------------------
# DeterministicGate
# ---------------------------------------------------------------------------


class TestDeterministicGate:
    def _passing_gate(self, name: str = "lint") -> DeterministicGate:
        return DeterministicGate(name=name, check_fn=lambda **kw: (True, "ok"))

    def _failing_gate(self, name: str = "compile") -> DeterministicGate:
        return DeterministicGate(name=name, check_fn=lambda **kw: (False, "error"))

    def _raising_gate(self, name: str = "bad") -> DeterministicGate:
        def _raise(**kw):
            raise RuntimeError("something exploded")

        return DeterministicGate(name=name, check_fn=_raise)

    def test_passing_gate_status(self):
        result = self._passing_gate().run()
        assert result.status == GateStatus.PASSED

    def test_failing_gate_status(self):
        result = self._failing_gate().run()
        assert result.status == GateStatus.FAILED

    def test_raising_gate_is_captured_as_failure(self):
        result = self._raising_gate().run()
        assert result.status == GateStatus.FAILED
        assert "exception" in result.details.lower()

    def test_result_has_artifact_hash(self):
        result = self._passing_gate().run()
        assert len(result.artifact_hash) == 64
        int(result.artifact_hash, 16)  # valid hex

    def test_artifact_hash_is_deterministic(self):
        gate = DeterministicGate(
            name="lint",
            check_fn=lambda **kw: (True, "fixed-details"),
        )
        r1 = gate.run()
        r2 = gate.run()
        # Details are identical so hashes must match.
        assert r1.artifact_hash == r2.artifact_hash

    def test_gate_name_in_result(self):
        result = self._passing_gate(name="my-gate").run()
        assert result.gate_name == "my-gate"

    def test_duration_ms_is_non_negative(self):
        result = self._passing_gate().run()
        assert result.duration_ms >= 0

    def test_to_dict_contains_expected_keys(self):
        result = self._passing_gate().run()
        d = result.to_dict()
        assert set(d.keys()) == {
            "gate_name",
            "status",
            "duration_ms",
            "details",
            "artifact_hash",
            "timestamp",
        }


# ---------------------------------------------------------------------------
# DeterministicLayer
# ---------------------------------------------------------------------------


class TestDeterministicLayer:
    def _layer_with_all_passing(self) -> DeterministicLayer:
        layer = DeterministicLayer()
        for name in ("lint", "compile", "security"):
            layer.register(
                DeterministicGate(name=name, check_fn=lambda **kw: (True, "ok"))
            )
        return layer

    def _layer_with_one_failure(self) -> DeterministicLayer:
        layer = DeterministicLayer()
        layer.register(
            DeterministicGate(name="lint", check_fn=lambda **kw: (True, "ok"))
        )
        layer.register(
            DeterministicGate(name="compile", check_fn=lambda **kw: (False, "error"))
        )
        layer.register(
            DeterministicGate(name="security", check_fn=lambda **kw: (True, "clean"))
        )
        return layer

    def test_all_passed_when_all_pass(self):
        layer = self._layer_with_all_passing()
        results = layer.run_all()
        assert layer.all_passed(results) is True

    def test_not_all_passed_when_one_fails(self):
        layer = self._layer_with_one_failure()
        results = layer.run_all()
        assert layer.all_passed(results) is False

    def test_failed_gates_returns_names(self):
        layer = self._layer_with_one_failure()
        results = layer.run_all()
        assert layer.failed_gates(results) == ["compile"]

    def test_all_gates_run_even_if_one_fails(self):
        layer = self._layer_with_one_failure()
        results = layer.run_all()
        assert len(results) == 3

    def test_register_returns_self(self):
        layer = DeterministicLayer()
        gate = DeterministicGate(name="x", check_fn=lambda **kw: (True, "ok"))
        returned = layer.register(gate)
        assert returned is layer

    def test_gate_names_property(self):
        layer = self._layer_with_all_passing()
        assert layer.gate_names == ["lint", "compile", "security"]

    def test_empty_layer_all_passed(self):
        layer = DeterministicLayer()
        assert layer.all_passed([]) is True

    def test_kwargs_forwarded_to_check_fn(self):
        received: dict = {}

        def check_fn(**kw):
            received.update(kw)
            return True, "ok"

        layer = DeterministicLayer()
        layer.register(DeterministicGate(name="g", check_fn=check_fn))
        layer.run_all(foo="bar", baz=42)
        assert received.get("foo") == "bar"
        assert received.get("baz") == 42

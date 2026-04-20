"""Tests for maatproof.layers.agent — agent gate layer."""

import pytest

from maatproof.chain import ReasoningChain
from maatproof.layers.agent import AgentDecision, AgentGate, AgentLayer, AgentResult
from maatproof.proof import ProofBuilder, ProofVerifier

SECRET = b"agent-test-secret"


def _builder() -> ProofBuilder:
    return ProofBuilder(secret_key=SECRET, model_id="agent-test-v1")


def _approve_fn(context, chain: ReasoningChain, **kw):
    chain.step(context=context, reasoning="Looks fine.", conclusion="Approve.")
    return AgentDecision.APPROVE, "All good."


def _reject_fn(context, chain: ReasoningChain, **kw):
    chain.step(context=context, reasoning="Risky change.", conclusion="Reject.")
    return AgentDecision.REJECT, "Too risky."


def _fix_fn(context, chain: ReasoningChain, **kw):
    chain.step(context=context, reasoning="Can auto-fix.", conclusion="Retry.")
    return AgentDecision.FIX_AND_RETRY, "Applied fix."


# ---------------------------------------------------------------------------
# AgentGate
# ---------------------------------------------------------------------------


class TestAgentGate:
    def test_approve_decision(self):
        gate = AgentGate(name="reviewer", reasoning_fn=_approve_fn, proof_builder=_builder())
        result = gate.run(context="PR #1")
        assert result.decision == AgentDecision.APPROVE
        assert result.summary == "All good."

    def test_reject_decision(self):
        gate = AgentGate(name="reviewer", reasoning_fn=_reject_fn, proof_builder=_builder())
        result = gate.run(context="PR #99")
        assert result.decision == AgentDecision.REJECT

    def test_fix_and_retry_decision(self):
        gate = AgentGate(name="test_fixer", reasoning_fn=_fix_fn, proof_builder=_builder())
        result = gate.run(context="test_auth.py:15 failed")
        assert result.decision == AgentDecision.FIX_AND_RETRY

    def test_proof_is_present(self):
        gate = AgentGate(name="reviewer", reasoning_fn=_approve_fn, proof_builder=_builder())
        result = gate.run(context="ctx")
        assert result.proof is not None
        assert len(result.proof.steps) >= 1

    def test_proof_is_verifiable(self):
        gate = AgentGate(name="reviewer", reasoning_fn=_approve_fn, proof_builder=_builder())
        result = gate.run(context="ctx")
        verifier = ProofVerifier(secret_key=SECRET)
        assert verifier.verify(result.proof) is True

    def test_agent_name_in_result(self):
        gate = AgentGate(name="my-agent", reasoning_fn=_approve_fn, proof_builder=_builder())
        result = gate.run(context="ctx")
        assert result.agent_name == "my-agent"

    def test_to_dict_contains_proof(self):
        gate = AgentGate(name="reviewer", reasoning_fn=_approve_fn, proof_builder=_builder())
        result = gate.run(context="ctx")
        d = result.to_dict()
        assert "proof" in d
        assert "steps" in d["proof"]


# ---------------------------------------------------------------------------
# AgentLayer
# ---------------------------------------------------------------------------


class TestAgentLayer:
    def _layer(self) -> AgentLayer:
        layer = AgentLayer()
        layer.register(
            AgentGate(name="reviewer", reasoning_fn=_approve_fn, proof_builder=_builder())
        )
        layer.register(
            AgentGate(name="test_fixer", reasoning_fn=_fix_fn, proof_builder=_builder())
        )
        return layer

    def test_gate_names(self):
        layer = self._layer()
        assert "reviewer" in layer.gate_names
        assert "test_fixer" in layer.gate_names

    def test_run_gate_by_name(self):
        layer = self._layer()
        result = layer.run_gate("test_fixer", context="failing test")
        assert result is not None
        assert result.decision == AgentDecision.FIX_AND_RETRY

    def test_run_gate_unknown_name_returns_none(self):
        layer = self._layer()
        result = layer.run_gate("nonexistent", context="ctx")
        assert result is None

    def test_run_all(self):
        layer = self._layer()
        results = layer.run_all(context="ctx")
        assert len(results) == 2

    def test_register_returns_self(self):
        layer = AgentLayer()
        gate = AgentGate(name="g", reasoning_fn=_approve_fn, proof_builder=_builder())
        returned = layer.register(gate)
        assert returned is layer

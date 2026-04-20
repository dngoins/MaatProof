"""Tests for maatproof.orchestrator — OrchestratingAgent."""

import pytest

from maatproof.chain import ReasoningChain
from maatproof.layers.agent import AgentDecision, AgentGate, AgentLayer
from maatproof.layers.deterministic import DeterministicGate, DeterministicLayer
from maatproof.orchestrator import OrchestratingAgent, PipelineEvent
from maatproof.proof import ProofBuilder

SECRET = b"orch-test-secret"


def _builder() -> ProofBuilder:
    return ProofBuilder(secret_key=SECRET, model_id="orch-test")


def _all_pass_layer() -> DeterministicLayer:
    layer = DeterministicLayer()
    for name in ("lint", "compile"):
        layer.register(
            DeterministicGate(name=name, check_fn=lambda **kw: (True, "ok"))
        )
    return layer


def _one_fail_layer() -> DeterministicLayer:
    layer = DeterministicLayer()
    layer.register(
        DeterministicGate(name="lint", check_fn=lambda **kw: (True, "ok"))
    )
    layer.register(
        DeterministicGate(name="compile", check_fn=lambda **kw: (False, "err"))
    )
    return layer


def _fix_fn(context, chain: ReasoningChain, **kw):
    chain.step(context=context, reasoning="Applied fix.", conclusion="Retry.")
    return AgentDecision.FIX_AND_RETRY, "Applied fix."


def _agent_layer_with_fixer() -> AgentLayer:
    layer = AgentLayer()
    layer.register(
        AgentGate(name="test_fixer", reasoning_fn=_fix_fn, proof_builder=_builder())
    )
    return layer


def _orchestrator(
    det_layer=None,
    agent_layer=None,
    max_fix_retries: int = 3,
) -> OrchestratingAgent:
    return OrchestratingAgent(
        deterministic_layer=det_layer or _all_pass_layer(),
        agent_layer=agent_layer or _agent_layer_with_fixer(),
        proof_builder=_builder(),
        max_fix_retries=max_fix_retries,
    )


class TestOrchestratingAgent:
    def test_code_pushed_all_pass(self):
        orch = _orchestrator(det_layer=_all_pass_layer())
        result = orch.emit(PipelineEvent.CODE_PUSHED, context="PR #1")
        assert result == "deterministic_gates_passed"

    def test_code_pushed_one_fail(self):
        orch = _orchestrator(det_layer=_one_fail_layer())
        result = orch.emit(PipelineEvent.CODE_PUSHED, context="PR #2")
        assert result.startswith("deterministic_gates_failed")
        assert "compile" in result

    def test_test_failed_fix_and_retry(self):
        orch = _orchestrator(agent_layer=_agent_layer_with_fixer())
        result = orch.emit(
            PipelineEvent.TEST_FAILED, context="test_auth.py", retry_count=0
        )
        assert "fix_applied_retrying" in result
        assert "attempt_1" in result

    def test_test_failed_max_retries_exceeded(self):
        orch = _orchestrator(max_fix_retries=2)
        result = orch.emit(
            PipelineEvent.TEST_FAILED, context="ctx", retry_count=2
        )
        assert "max_retries_exceeded" in result

    def test_test_failed_no_fixer_registered(self):
        orch = _orchestrator(agent_layer=AgentLayer())
        result = orch.emit(PipelineEvent.TEST_FAILED, context="ctx", retry_count=0)
        assert "no_test_fixer_agent_registered" in result

    def test_no_handler_returns_none(self):
        orch = _orchestrator()
        result = orch.emit(PipelineEvent.STAGING_HEALTHY, context="ctx")
        assert result is None

    def test_audit_log_populated_after_events(self):
        orch = _orchestrator()
        orch.emit(PipelineEvent.CODE_PUSHED, context="PR #1")
        orch.emit(PipelineEvent.TEST_FAILED, context="ctx", retry_count=0)
        log = orch.get_audit_log()
        assert len(log) == 2

    def test_audit_log_entry_has_expected_keys(self):
        orch = _orchestrator()
        orch.emit(PipelineEvent.CODE_PUSHED, context="ctx")
        entry = orch.get_audit_log()[0]
        assert "event" in entry
        assert "timestamp" in entry
        assert "result" in entry

    def test_custom_event_handler_registered(self):
        orch = _orchestrator()
        orch.on(PipelineEvent.STAGING_HEALTHY, lambda **kw: "staging_ok")
        result = orch.emit(PipelineEvent.STAGING_HEALTHY)
        assert result == "staging_ok"

    def test_prod_error_spike_no_rollback_agent(self):
        orch = _orchestrator(agent_layer=AgentLayer())
        result = orch.emit(PipelineEvent.PROD_ERROR_SPIKE, context="500 spike")
        assert "no_rollback_agent_registered" in result

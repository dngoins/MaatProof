"""Tests for maatproof.pipeline — ACIPipeline and ACDPipeline."""

import pytest

from maatproof.chain import ReasoningChain
from maatproof.layers.agent import AgentDecision, AgentGate, AgentLayer
from maatproof.layers.deterministic import DeterministicGate, DeterministicLayer
from maatproof.orchestrator import PipelineEvent
from maatproof.pipeline import ACDPipeline, ACIPipeline, PipelineConfig
from maatproof.proof import ProofBuilder, ProofVerifier

SECRET = b"pipeline-test-secret"


def _config(**kwargs) -> PipelineConfig:
    defaults = dict(name="test-pipeline", secret_key=SECRET, model_id="test-v1")
    defaults.update(kwargs)
    return PipelineConfig(**defaults)


def _approve_fn(context, chain: ReasoningChain, **kw):
    chain.step(context=context, reasoning="LGTM.", conclusion="Approve.")
    return AgentDecision.APPROVE, "LGTM."


class TestACIPipeline:
    def test_run_code_pushed_no_gates_passes(self):
        pipeline = ACIPipeline(config=_config())
        result = pipeline.run(PipelineEvent.CODE_PUSHED, context="PR #1")
        assert result == "deterministic_gates_passed"

    def test_run_with_passing_gate(self):
        pipeline = ACIPipeline(config=_config())
        pipeline.deterministic_layer.register(
            DeterministicGate(name="lint", check_fn=lambda **kw: (True, "ok"))
        )
        result = pipeline.run(PipelineEvent.CODE_PUSHED, context="PR #2")
        assert result == "deterministic_gates_passed"

    def test_run_with_failing_gate(self):
        pipeline = ACIPipeline(config=_config())
        pipeline.deterministic_layer.register(
            DeterministicGate(name="lint", check_fn=lambda **kw: (False, "style errors"))
        )
        result = pipeline.run(PipelineEvent.CODE_PUSHED, context="PR #3")
        assert "lint" in result
        assert "failed" in result

    def test_verify_proof_valid(self):
        pipeline = ACIPipeline(config=_config())
        proof = pipeline.proof_builder.build(
            steps=[
                __import__("maatproof.proof", fromlist=["ReasoningStep"]).ReasoningStep(
                    step_id=0,
                    context="ctx",
                    reasoning="rsn",
                    conclusion="ok",
                    timestamp=1_700_000_000.0,
                )
            ]
        )
        assert pipeline.verify_proof(proof) is True

    def test_verify_proof_wrong_key_fails(self):
        pipeline = ACIPipeline(config=_config())
        bad_pipeline = ACIPipeline(config=_config(secret_key=b"other-key"))
        proof = bad_pipeline.proof_builder.build(
            steps=[
                __import__("maatproof.proof", fromlist=["ReasoningStep"]).ReasoningStep(
                    step_id=0,
                    context="ctx",
                    reasoning="rsn",
                    conclusion="ok",
                    timestamp=1_700_000_000.0,
                )
            ]
        )
        assert pipeline.verify_proof(proof) is False

    def test_get_audit_log_after_run(self):
        pipeline = ACIPipeline(config=_config())
        pipeline.run(PipelineEvent.CODE_PUSHED, context="ctx")
        log = pipeline.get_audit_log()
        assert len(log) == 1


class TestACDPipeline:
    def test_request_staging_deployment_approved(self):
        pipeline = ACDPipeline(config=_config())
        result = pipeline.request_deployment(context="deploy v1.2", environment="staging")
        assert result["approved"] is True
        assert result["requires_human_approval"] is False

    def test_request_production_deployment_requires_approval(self):
        pipeline = ACDPipeline(config=_config(require_human_approval=True))
        result = pipeline.request_deployment(
            context="deploy v1.2", environment="production"
        )
        assert result["approved"] is False
        assert result["requires_human_approval"] is True

    def test_request_production_deployment_without_approval_check(self):
        pipeline = ACDPipeline(config=_config(require_human_approval=False))
        result = pipeline.request_deployment(
            context="deploy v1.2", environment="production"
        )
        assert result["approved"] is True
        assert result["requires_human_approval"] is False

    def test_deployment_proof_is_verifiable(self):
        pipeline = ACDPipeline(config=_config())
        result = pipeline.request_deployment(context="deploy v1.2", environment="staging")
        proof = result["proof"]
        verifier = ProofVerifier(secret_key=SECRET)
        assert verifier.verify(proof) is True

    def test_deployment_proofs_accumulate(self):
        pipeline = ACDPipeline(config=_config())
        pipeline.request_deployment(context="deploy v1.0", environment="staging")
        pipeline.request_deployment(context="deploy v1.1", environment="staging")
        assert len(pipeline.get_deployment_proofs()) == 2

    def test_deployment_message_references_constitution(self):
        pipeline = ACDPipeline(config=_config(require_human_approval=True))
        result = pipeline.request_deployment(
            context="deploy v1.2", environment="production"
        )
        assert "CONSTITUTION" in result["message"]

    def test_run_inherits_from_aci_pipeline(self):
        pipeline = ACDPipeline(config=_config())
        result = pipeline.run(PipelineEvent.CODE_PUSHED, context="PR #1")
        assert result == "deterministic_gates_passed"

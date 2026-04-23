"""Tests for the ACI/ACD engine core data model (Issue #14).

Covers the round-trip, validation, and identity contracts required by the
acceptance criteria:

- All dataclasses expose ``to_dict`` / ``from_dict`` round-trips without loss.
- ``ReasoningStep.compute_hash`` is deterministic.
- ``AuditEntry`` entries carry a unique ``entry_id``.
- ``PipelineConfig`` validates required fields (``secret_key`` not empty).
"""

from __future__ import annotations

import base64
import time
from typing import Set

import pytest

from maatproof.layers.agent import (
    AgentDecision,
    AgentGate,
    AgentLayer,
    AgentResult,
)
from maatproof.layers.deterministic import (
    DeterministicGate,
    DeterministicLayer,
    GateResult,
    GateStatus,
)
from maatproof.orchestrator import AuditEntry, OrchestratingAgent, PipelineEvent
from maatproof.pipeline import ACIPipeline, PipelineConfig
from maatproof.proof import (
    ProofBuilder,
    ProofVerifier,
    ReasoningProof,
    ReasoningStep,
)

SECRET = b"data-model-test-secret"


# ---------------------------------------------------------------------------
# ReasoningStep — serialization & determinism
# ---------------------------------------------------------------------------


def _step(step_id: int = 0) -> ReasoningStep:
    return ReasoningStep(
        step_id=step_id,
        context="ctx",
        reasoning="rsn",
        conclusion="ok",
        timestamp=1_700_000_000.0,
    )


class TestReasoningStepDataModel:
    def test_to_dict_contains_all_fields(self):
        step = _step()
        d = step.to_dict()
        for key in (
            "step_id",
            "context",
            "reasoning",
            "conclusion",
            "timestamp",
            "step_hash",
        ):
            assert key in d

    def test_roundtrip_preserves_fields(self):
        step = _step()
        restored = ReasoningStep.from_dict(step.to_dict())
        assert restored == step
        assert restored.step_hash == step.step_hash

    def test_roundtrip_preserves_populated_hash(self):
        step = _step()
        step.step_hash = "a" * 64
        restored = ReasoningStep.from_dict(step.to_dict())
        assert restored.step_hash == "a" * 64

    def test_from_dict_defaults_missing_step_hash(self):
        d = _step().to_dict()
        del d["step_hash"]
        restored = ReasoningStep.from_dict(d)
        assert restored.step_hash == ""

    def test_compute_hash_is_deterministic_same_input(self):
        step = _step()
        assert step.compute_hash("abc") == step.compute_hash("abc")

    def test_compute_hash_differs_with_different_previous_hash(self):
        step = _step()
        assert step.compute_hash("abc") != step.compute_hash("def")


# ---------------------------------------------------------------------------
# ReasoningProof — serialization round-trip
# ---------------------------------------------------------------------------


class TestReasoningProofDataModel:
    def test_roundtrip_is_verifiable(self):
        builder = ProofBuilder(secret_key=SECRET, model_id="m")
        proof = builder.build(
            steps=[_step(0), _step(1)],
            chain_id="rt-1",
            metadata={"k": "v"},
        )
        restored = ReasoningProof.from_dict(proof.to_dict())
        assert ProofVerifier(secret_key=SECRET).verify(restored) is True

    def test_roundtrip_preserves_all_top_level_fields(self):
        builder = ProofBuilder(secret_key=SECRET, model_id="m")
        proof = builder.build(steps=[_step()], chain_id="rt-2")
        restored = ReasoningProof.from_dict(proof.to_dict())
        assert restored.proof_id == proof.proof_id
        assert restored.model_id == proof.model_id
        assert restored.chain_id == proof.chain_id
        assert restored.root_hash == proof.root_hash
        assert restored.signature == proof.signature
        assert restored.created_at == proof.created_at

    def test_roundtrip_metadata_is_independent_copy(self):
        builder = ProofBuilder(secret_key=SECRET)
        proof = builder.build(steps=[_step()], metadata={"env": "staging"})
        restored = ReasoningProof.from_dict(proof.to_dict())
        restored.metadata["env"] = "prod"
        assert proof.metadata["env"] == "staging"


# ---------------------------------------------------------------------------
# AuditEntry — append-only + unique entry IDs
# ---------------------------------------------------------------------------


class TestAuditEntryDataModel:
    def test_new_entry_id_is_unique(self):
        ids: Set[str] = {AuditEntry.new_entry_id() for _ in range(100)}
        assert len(ids) == 100

    def test_orchestrator_audit_entries_have_unique_ids(self):
        layer_det = DeterministicLayer()
        layer_agent = AgentLayer()
        builder = ProofBuilder(secret_key=SECRET, model_id="orch")
        orch = OrchestratingAgent(
            deterministic_layer=layer_det,
            agent_layer=layer_agent,
            proof_builder=builder,
        )
        orch.on(PipelineEvent.STAGING_HEALTHY, lambda **kw: "ok")
        for i in range(5):
            orch.emit(PipelineEvent.STAGING_HEALTHY, ctx=str(i))
        entries = orch.get_audit_log()
        ids = [e["entry_id"] for e in entries]
        assert len(ids) == 5
        assert len(set(ids)) == 5

    def test_roundtrip_preserves_all_fields(self):
        entry = AuditEntry(
            entry_id=AuditEntry.new_entry_id(),
            event="code_pushed",
            timestamp=time.time(),
            result="deterministic_gates_passed",
            metadata={"pr": "42"},
        )
        restored = AuditEntry.from_dict(entry.to_dict())
        assert restored == entry

    def test_to_dict_returns_independent_metadata(self):
        entry = AuditEntry(
            entry_id="id-1",
            event="evt",
            timestamp=0.0,
            result="r",
            metadata={"k": "v"},
        )
        d = entry.to_dict()
        d["metadata"]["k"] = "mutated"
        assert entry.metadata["k"] == "v"

    def test_from_dict_tolerates_missing_metadata(self):
        restored = AuditEntry.from_dict(
            {
                "entry_id": "id-1",
                "event": "evt",
                "timestamp": 0.0,
                "result": "r",
            }
        )
        assert restored.metadata == {}


# ---------------------------------------------------------------------------
# PipelineConfig — validation + serialization
# ---------------------------------------------------------------------------


class TestPipelineConfigDataModel:
    def test_empty_secret_key_rejected(self):
        with pytest.raises(ValueError, match="secret_key"):
            PipelineConfig(name="p", secret_key=b"")

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name"):
            PipelineConfig(name="", secret_key=SECRET)

    def test_negative_retries_rejected(self):
        with pytest.raises(ValueError, match="max_fix_retries"):
            PipelineConfig(name="p", secret_key=SECRET, max_fix_retries=-1)

    def test_non_bytes_secret_key_rejected(self):
        with pytest.raises(TypeError, match="secret_key"):
            PipelineConfig(name="p", secret_key="not-bytes")  # type: ignore[arg-type]

    def test_bytearray_secret_key_is_normalized(self):
        cfg = PipelineConfig(name="p", secret_key=bytearray(SECRET))
        assert isinstance(cfg.secret_key, bytes)
        assert cfg.secret_key == SECRET

    def test_valid_config_accepts_defaults(self):
        cfg = PipelineConfig(name="p", secret_key=SECRET)
        assert cfg.require_human_approval is True
        assert cfg.max_fix_retries == 3
        assert cfg.model_id == "maatproof-v1"

    def test_roundtrip_preserves_fields(self):
        cfg = PipelineConfig(
            name="p",
            secret_key=SECRET,
            model_id="custom",
            require_human_approval=False,
            max_fix_retries=5,
        )
        restored = PipelineConfig.from_dict(cfg.to_dict())
        assert restored == cfg

    def test_to_dict_uses_base64_for_secret(self):
        cfg = PipelineConfig(name="p", secret_key=SECRET)
        d = cfg.to_dict()
        assert "secret_key_b64" in d
        assert "secret_key" not in d  # raw bytes never leak into the dict
        assert base64.b64decode(d["secret_key_b64"]) == SECRET

    def test_from_dict_accepts_raw_secret_key(self):
        # Backwards-compat path: caller can supply raw bytes directly.
        cfg = PipelineConfig.from_dict(
            {"name": "p", "secret_key": SECRET}
        )
        assert cfg.secret_key == SECRET

    def test_from_dict_rejects_bad_base64(self):
        with pytest.raises(ValueError, match="base64"):
            PipelineConfig.from_dict({"name": "p", "secret_key_b64": "not base64!!"})

    def test_from_dict_missing_secret_raises(self):
        with pytest.raises(KeyError):
            PipelineConfig.from_dict({"name": "p"})


# ---------------------------------------------------------------------------
# GateResult — serialization round-trip
# ---------------------------------------------------------------------------


class TestGateResultDataModel:
    def test_roundtrip_preserves_fields(self):
        gr = GateResult(
            gate_name="lint",
            status=GateStatus.PASSED,
            duration_ms=4.2,
            details="ok",
            artifact_hash="deadbeef",
            timestamp=1_700_000_000.0,
        )
        restored = GateResult.from_dict(gr.to_dict())
        assert restored == gr

    def test_from_dict_rejects_invalid_status(self):
        with pytest.raises(ValueError):
            GateResult.from_dict(
                {
                    "gate_name": "lint",
                    "status": "not-a-status",
                    "duration_ms": 0.0,
                    "details": "",
                    "artifact_hash": "",
                    "timestamp": 0.0,
                }
            )

    def test_from_dict_defaults_timestamp(self):
        before = time.time()
        restored = GateResult.from_dict(
            {
                "gate_name": "lint",
                "status": "passed",
                "duration_ms": 1.0,
                "details": "ok",
                "artifact_hash": "aa",
            }
        )
        assert restored.timestamp >= before


# ---------------------------------------------------------------------------
# AgentResult — serialization round-trip
# ---------------------------------------------------------------------------


class TestAgentResultDataModel:
    def _result(self) -> AgentResult:
        builder = ProofBuilder(secret_key=SECRET, model_id="m")
        proof = builder.build(steps=[_step()])
        return AgentResult(
            agent_name="reviewer",
            decision=AgentDecision.APPROVE,
            summary="LGTM",
            proof=proof,
            metadata={"note": "n"},
        )

    def test_roundtrip_preserves_decision_and_summary(self):
        ar = self._result()
        restored = AgentResult.from_dict(ar.to_dict())
        assert restored.agent_name == ar.agent_name
        assert restored.decision == AgentDecision.APPROVE
        assert restored.summary == ar.summary
        assert restored.metadata == ar.metadata

    def test_roundtrip_embedded_proof_verifies(self):
        ar = self._result()
        restored = AgentResult.from_dict(ar.to_dict())
        assert ProofVerifier(secret_key=SECRET).verify(restored.proof) is True

    def test_from_dict_rejects_invalid_decision(self):
        ar = self._result()
        d = ar.to_dict()
        d["decision"] = "not-a-decision"
        with pytest.raises(ValueError):
            AgentResult.from_dict(d)


# ---------------------------------------------------------------------------
# ACIPipeline still constructs with the validated config
# ---------------------------------------------------------------------------


class TestPipelineConfigIntegration:
    def test_aci_pipeline_constructs_with_validated_config(self):
        cfg = PipelineConfig(name="p", secret_key=SECRET)
        pipeline = ACIPipeline(config=cfg)
        # Smoke test the orchestrator still runs.
        result = pipeline.run(PipelineEvent.CODE_PUSHED, context="ctx")
        assert result == "deterministic_gates_passed"

    def test_aci_pipeline_rejects_empty_secret_via_config(self):
        with pytest.raises(ValueError, match="secret_key"):
            PipelineConfig(name="p", secret_key=b"")

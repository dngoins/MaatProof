"""Tests for maatproof.proof — cryptographic proof primitives."""

import hashlib
import hmac
import json
import time

import pytest

from maatproof.proof import ProofBuilder, ProofVerifier, ReasoningProof, ReasoningStep


SECRET = b"test-secret-key-do-not-use-in-prod"
MODEL = "test-model-v1"


# ---------------------------------------------------------------------------
# ReasoningStep
# ---------------------------------------------------------------------------


class TestReasoningStep:
    def _make_step(self, step_id: int = 0) -> ReasoningStep:
        return ReasoningStep(
            step_id=step_id,
            context="Should we deploy?",
            reasoning="All gates passed.",
            conclusion="Yes.",
            timestamp=1_700_000_000.0,
        )

    def test_compute_hash_is_deterministic(self):
        step = self._make_step()
        h1 = step.compute_hash()
        h2 = step.compute_hash()
        assert h1 == h2

    def test_compute_hash_depends_on_previous_hash(self):
        step = self._make_step()
        h_no_prev = step.compute_hash(previous_hash="")
        h_with_prev = step.compute_hash(previous_hash="abc123")
        assert h_no_prev != h_with_prev

    def test_compute_hash_changes_when_content_changes(self):
        step1 = self._make_step()
        step2 = ReasoningStep(
            step_id=0,
            context="Different context",
            reasoning="All gates passed.",
            conclusion="Yes.",
            timestamp=1_700_000_000.0,
        )
        assert step1.compute_hash() != step2.compute_hash()

    def test_compute_hash_is_sha256_hex(self):
        step = self._make_step()
        h = step.compute_hash()
        assert len(h) == 64
        int(h, 16)  # must be valid hex


# ---------------------------------------------------------------------------
# ProofBuilder
# ---------------------------------------------------------------------------


def _single_step(ts: float = 1_700_000_000.0) -> ReasoningStep:
    return ReasoningStep(
        step_id=0,
        context="ctx",
        reasoning="rsn",
        conclusion="ok",
        timestamp=ts,
    )


class TestProofBuilder:
    def test_build_single_step(self):
        builder = ProofBuilder(secret_key=SECRET, model_id=MODEL)
        proof = builder.build(steps=[_single_step()])
        assert len(proof.steps) == 1
        assert proof.root_hash == proof.steps[0].step_hash
        assert len(proof.signature) == 64

    def test_build_multi_step_chains_hashes(self):
        steps = [
            ReasoningStep(
                step_id=i,
                context=f"ctx {i}",
                reasoning=f"rsn {i}",
                conclusion=f"ok {i}",
                timestamp=1_700_000_000.0 + i,
            )
            for i in range(3)
        ]
        builder = ProofBuilder(secret_key=SECRET)
        proof = builder.build(steps=steps)
        # Verify chain linkage
        previous_hash = ""
        for step in proof.steps:
            expected = step.compute_hash(previous_hash)
            assert step.step_hash == expected
            previous_hash = step.step_hash
        assert proof.root_hash == previous_hash

    def test_build_raises_on_empty_steps(self):
        builder = ProofBuilder(secret_key=SECRET)
        with pytest.raises(ValueError, match="at least one"):
            builder.build(steps=[])

    def test_build_raises_on_empty_secret(self):
        with pytest.raises(ValueError):
            ProofBuilder(secret_key=b"")

    def test_build_preserves_chain_id(self):
        builder = ProofBuilder(secret_key=SECRET)
        proof = builder.build(steps=[_single_step()], chain_id="my-chain")
        assert proof.chain_id == "my-chain"

    def test_build_generates_chain_id_when_absent(self):
        builder = ProofBuilder(secret_key=SECRET)
        proof = builder.build(steps=[_single_step()])
        assert proof.chain_id  # non-empty UUID

    def test_build_attaches_metadata(self):
        builder = ProofBuilder(secret_key=SECRET)
        proof = builder.build(
            steps=[_single_step()],
            metadata={"pr": 42, "env": "staging"},
        )
        assert proof.metadata == {"pr": 42, "env": "staging"}

    def test_build_model_id(self):
        builder = ProofBuilder(secret_key=SECRET, model_id="gpt-det-v1")
        proof = builder.build(steps=[_single_step()])
        assert proof.model_id == "gpt-det-v1"

    def test_two_builds_with_same_inputs_produce_same_hashes(self):
        """Determinism: same inputs → same hashes (different UUIDs but same content digests)."""
        step = _single_step()
        builder = ProofBuilder(secret_key=SECRET, model_id=MODEL)
        proof1 = builder.build(steps=[step], chain_id="fixed-chain")
        proof2 = builder.build(steps=[step], chain_id="fixed-chain")
        assert proof1.root_hash == proof2.root_hash
        assert proof1.signature == proof2.signature


# ---------------------------------------------------------------------------
# ProofVerifier
# ---------------------------------------------------------------------------


class TestProofVerifier:
    def _build_proof(self, chain_id: str = "chain-1") -> ReasoningProof:
        builder = ProofBuilder(secret_key=SECRET, model_id=MODEL)
        return builder.build(steps=[_single_step()], chain_id=chain_id)

    def test_valid_proof_passes(self):
        proof = self._build_proof()
        verifier = ProofVerifier(secret_key=SECRET)
        assert verifier.verify(proof) is True

    def test_wrong_key_fails(self):
        proof = self._build_proof()
        verifier = ProofVerifier(secret_key=b"wrong-key")
        assert verifier.verify(proof) is False

    def test_tampered_conclusion_fails(self):
        proof = self._build_proof()
        # Directly mutate a step's conclusion without recomputing hashes.
        proof.steps[0].conclusion = "EVIL CONCLUSION"
        verifier = ProofVerifier(secret_key=SECRET)
        assert verifier.verify(proof) is False

    def test_tampered_step_hash_fails(self):
        proof = self._build_proof()
        proof.steps[0].step_hash = "a" * 64
        verifier = ProofVerifier(secret_key=SECRET)
        assert verifier.verify(proof) is False

    def test_tampered_root_hash_fails(self):
        proof = self._build_proof()
        proof.root_hash = "b" * 64
        verifier = ProofVerifier(secret_key=SECRET)
        assert verifier.verify(proof) is False

    def test_tampered_signature_fails(self):
        proof = self._build_proof()
        proof.signature = "c" * 64
        verifier = ProofVerifier(secret_key=SECRET)
        assert verifier.verify(proof) is False

    def test_empty_steps_fails(self):
        proof = self._build_proof()
        proof.steps = []
        verifier = ProofVerifier(secret_key=SECRET)
        assert verifier.verify(proof) is False

    def test_multi_step_proof_passes(self):
        steps = [
            ReasoningStep(
                step_id=i,
                context=f"c{i}",
                reasoning=f"r{i}",
                conclusion=f"ok{i}",
                timestamp=float(i),
            )
            for i in range(5)
        ]
        builder = ProofBuilder(secret_key=SECRET)
        proof = builder.build(steps=steps, chain_id="multi")
        verifier = ProofVerifier(secret_key=SECRET)
        assert verifier.verify(proof) is True

    def test_multi_step_tamper_middle_fails(self):
        steps = [
            ReasoningStep(
                step_id=i,
                context=f"c{i}",
                reasoning=f"r{i}",
                conclusion=f"ok{i}",
                timestamp=float(i),
            )
            for i in range(5)
        ]
        builder = ProofBuilder(secret_key=SECRET)
        proof = builder.build(steps=steps, chain_id="multi")
        # Tamper with the middle step.
        proof.steps[2].reasoning = "TAMPERED"
        verifier = ProofVerifier(secret_key=SECRET)
        assert verifier.verify(proof) is False


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_to_dict_from_dict_roundtrip(self):
        builder = ProofBuilder(secret_key=SECRET, model_id=MODEL)
        original = builder.build(steps=[_single_step()], chain_id="rt-chain")
        restored = ReasoningProof.from_dict(original.to_dict())
        assert restored.proof_id == original.proof_id
        assert restored.root_hash == original.root_hash
        assert restored.signature == original.signature
        assert len(restored.steps) == len(original.steps)
        verifier = ProofVerifier(secret_key=SECRET)
        assert verifier.verify(restored) is True

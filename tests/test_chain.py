"""Tests for maatproof.chain — fluent reasoning chain builder."""

import pytest

from maatproof.chain import ReasoningChain
from maatproof.proof import ProofBuilder, ProofVerifier

SECRET = b"chain-test-secret"


def _builder() -> ProofBuilder:
    return ProofBuilder(secret_key=SECRET, model_id="test-model")


class TestReasoningChain:
    def test_single_step_seals_valid_proof(self):
        chain = ReasoningChain(builder=_builder())
        proof = chain.step(
            context="PR #1 failing",
            reasoning="Mock changed.",
            conclusion="Update mock.",
        ).seal()
        verifier = ProofVerifier(secret_key=SECRET)
        assert verifier.verify(proof) is True

    def test_multi_step_seals_valid_proof(self):
        chain = ReasoningChain(builder=_builder())
        proof = (
            chain.step(
                context="PR #1 failing",
                reasoning="Mock changed.",
                conclusion="Update mock.",
            )
            .step(
                context="Mock updated; re-running tests.",
                reasoning="All 47 tests pass.",
                conclusion="Safe to merge.",
            )
            .seal()
        )
        verifier = ProofVerifier(secret_key=SECRET)
        assert verifier.verify(proof) is True
        assert len(proof.steps) == 2

    def test_step_ids_are_sequential(self):
        chain = ReasoningChain(builder=_builder())
        for i in range(4):
            chain.step(context=f"c{i}", reasoning=f"r{i}", conclusion=f"ok{i}")
        proof = chain.seal()
        assert [s.step_id for s in proof.steps] == [0, 1, 2, 3]

    def test_seal_raises_without_steps(self):
        chain = ReasoningChain(builder=_builder())
        with pytest.raises(ValueError):
            chain.seal()

    def test_chain_id_preserved(self):
        chain = ReasoningChain(builder=_builder(), chain_id="my-chain-id")
        proof = chain.step(context="c", reasoning="r", conclusion="ok").seal()
        assert proof.chain_id == "my-chain-id"

    def test_metadata_attached(self):
        chain = ReasoningChain(builder=_builder())
        proof = (
            chain.step(context="c", reasoning="r", conclusion="ok")
            .seal(metadata={"pr": 99})
        )
        assert proof.metadata["pr"] == 99

    def test_step_count_property(self):
        chain = ReasoningChain(builder=_builder())
        assert chain.step_count == 0
        chain.step(context="c", reasoning="r", conclusion="ok")
        assert chain.step_count == 1
        chain.step(context="c2", reasoning="r2", conclusion="ok2")
        assert chain.step_count == 2

    def test_explicit_timestamp_preserved(self):
        chain = ReasoningChain(builder=_builder())
        ts = 1_700_000_000.0
        proof = chain.step(
            context="c",
            reasoning="r",
            conclusion="ok",
            timestamp=ts,
        ).seal()
        assert proof.steps[0].timestamp == ts

"""Fluent builder for constructing step-by-step reasoning chains.

Usage::

    from maatproof.chain import ReasoningChain
    from maatproof.proof import ProofBuilder

    builder = ProofBuilder(secret_key=b"my-secret", model_id="gpt-deterministic-v1")

    proof = (
        ReasoningChain(builder=builder, chain_id="pr-42-fix")
        .step(
            context="Tests are failing on PR #42 in test_auth.py line 15",
            reasoning="The mock return value changed in the latest fixture update.",
            conclusion="Updating the mock will fix the failure.",
        )
        .step(
            context="Fix applied; re-running the test suite.",
            reasoning="All 47 tests pass; coverage unchanged at 94%.",
            conclusion="Safe to merge.",
        )
        .seal(metadata={"pr": 42, "author": "agent"})
    )
"""

import time
import uuid
from typing import Any, Dict, List, Optional

from .proof import ProofBuilder, ReasoningProof, ReasoningStep


class ReasoningChain:
    """Fluent builder for constructing a step-by-step reasoning chain.

    Accumulates :class:`~maatproof.proof.ReasoningStep` objects and,
    when :meth:`seal` is called, delegates to a :class:`~maatproof.proof.ProofBuilder`
    to produce a cryptographically signed :class:`~maatproof.proof.ReasoningProof`.

    Args:
        builder:  A configured :class:`~maatproof.proof.ProofBuilder` instance.
        chain_id: Optional logical identifier for this chain.  Defaults to a
                  fresh UUID.
    """

    def __init__(
        self,
        builder: ProofBuilder,
        chain_id: Optional[str] = None,
    ) -> None:
        self._builder = builder
        self._chain_id = chain_id or str(uuid.uuid4())
        self._steps: List[ReasoningStep] = []

    def step(
        self,
        context: str,
        reasoning: str,
        conclusion: str,
        timestamp: Optional[float] = None,
    ) -> "ReasoningChain":
        """Append a reasoning step to the chain.

        Args:
            context:    The input situation or data being reasoned about.
            reasoning:  The reasoning process that was applied.
            conclusion: The conclusion reached from the reasoning.
            timestamp:  Optional explicit POSIX timestamp.  Defaults to now.

        Returns:
            *self*, allowing method chaining.
        """
        self._steps.append(
            ReasoningStep(
                step_id=len(self._steps),
                context=context,
                reasoning=reasoning,
                conclusion=conclusion,
                timestamp=timestamp if timestamp is not None else time.time(),
            )
        )
        return self

    def seal(self, metadata: Optional[Dict[str, Any]] = None) -> ReasoningProof:
        """Seal the accumulated steps into a signed :class:`~maatproof.proof.ReasoningProof`.

        Args:
            metadata: Optional key/value metadata to attach to the proof.

        Returns:
            A :class:`~maatproof.proof.ReasoningProof` with all steps hashed
            and the root hash signed.

        Raises:
            ValueError: If no steps have been added to the chain.
        """
        return self._builder.build(
            steps=self._steps,
            chain_id=self._chain_id,
            metadata=metadata,
        )

    @property
    def step_count(self) -> int:
        """The number of steps currently in the chain."""
        return len(self._steps)

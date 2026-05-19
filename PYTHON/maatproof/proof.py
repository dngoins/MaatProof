"""Core cryptographic primitives for verifiable LLM reasoning.

The central insight: deterministic reasoning + cryptographic signing = a
reasoning chain that can be independently verified, audited, and used as a
trust anchor for automated deployments.

A ``ReasoningProof`` is the "receipt" that the agent orchestrator produces — a
signed artifact that proves:

1. Exactly what input context was given.
2. The complete step-by-step reasoning chain.
3. The final conclusion reached.
4. That neither the chain nor the conclusion was tampered with after signing.

Usage::

    from maatproof.proof import ProofBuilder, ProofVerifier, ReasoningStep

    secret = b"shared-secret"

    builder = ProofBuilder(secret_key=secret, model_id="my-llm-v1")
    proof = builder.build(
        steps=[
            ReasoningStep(
                step_id=0,
                context="PR #42: tests failing in test_auth.py",
                reasoning="Examined the diff; mock return value changed.",
                conclusion="Updating mock will fix the failure.",
                timestamp=1_700_000_000.0,
            ),
        ],
        chain_id="pr-42-analysis",
    )

    verifier = ProofVerifier(secret_key=secret)
    assert verifier.verify(proof)
"""

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ReasoningStep:
    """A single step in a cryptographically chained reasoning sequence.

    Each step records the input context, the reasoning applied, and the
    conclusion reached.  The ``step_hash`` field is populated by
    :class:`ProofBuilder` and encodes this step's content *plus* the hash of
    every prior step, forming a tamper-evident chain.
    """

    step_id: int
    context: str
    reasoning: str
    conclusion: str
    timestamp: float
    step_hash: str = field(default="", compare=False)

    def compute_hash(self, previous_hash: str = "") -> str:
        """Compute a deterministic hash of this step chained to *previous_hash*.

        The hash is computed over a canonical JSON representation of all step
        fields plus ``previous_hash``, ensuring the chain cannot be tampered
        with without invalidating every subsequent hash.
        """
        canonical = json.dumps(
            {
                "step_id": self.step_id,
                "context": self.context,
                "reasoning": self.reasoning,
                "conclusion": self.conclusion,
                "timestamp": self.timestamp,
                "previous_hash": previous_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ReasoningProof:
    """A cryptographically signed, verifiable record of an LLM reasoning chain.

    Attributes:
        proof_id:   Unique identifier for this proof artifact.
        model_id:   Identifier of the model that produced the reasoning.
        chain_id:   Logical identifier grouping related reasoning steps.
        steps:      Ordered list of reasoning steps.
        root_hash:  SHA-256 hash of the final step (anchors the entire chain).
        signature:  HMAC-SHA256 signature over ``chain_id + root_hash``.
        created_at: Unix timestamp of proof creation.
        metadata:   Arbitrary key/value metadata.
    """

    proof_id: str
    model_id: str
    chain_id: str
    steps: List[ReasoningStep]
    root_hash: str
    signature: str
    created_at: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dictionary suitable for storage or transmission."""
        return {
            "proof_id": self.proof_id,
            "model_id": self.model_id,
            "chain_id": self.chain_id,
            "steps": [
                {
                    "step_id": s.step_id,
                    "context": s.context,
                    "reasoning": s.reasoning,
                    "conclusion": s.conclusion,
                    "timestamp": s.timestamp,
                    "step_hash": s.step_hash,
                }
                for s in self.steps
            ],
            "root_hash": self.root_hash,
            "signature": self.signature,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReasoningProof":
        """Deserialize from a dictionary produced by :meth:`to_dict`."""
        steps = [
            ReasoningStep(
                step_id=s["step_id"],
                context=s["context"],
                reasoning=s["reasoning"],
                conclusion=s["conclusion"],
                timestamp=s["timestamp"],
                step_hash=s["step_hash"],
            )
            for s in data["steps"]
        ]
        return cls(
            proof_id=data["proof_id"],
            model_id=data["model_id"],
            chain_id=data["chain_id"],
            steps=steps,
            root_hash=data["root_hash"],
            signature=data["signature"],
            created_at=data["created_at"],
            metadata=data.get("metadata", {}),
        )


class ProofBuilder:
    """Constructs and signs :class:`ReasoningProof` artifacts.

    The builder hashes each reasoning step in sequence, producing a
    hash-chain structure where each step's digest depends on all previous
    steps (analogous to a Merkle chain).  The final *root hash* is then
    signed with the provided secret key using HMAC-SHA256.

    Args:
        secret_key: Symmetric key used to sign the root hash.
        model_id:   Identifier of the LLM model producing the reasoning.
    """

    def __init__(self, secret_key: bytes, model_id: str = "unknown"):
        if not secret_key:
            raise ValueError("secret_key must not be empty")
        self._secret_key = secret_key
        self._model_id = model_id

    def build(
        self,
        steps: List[ReasoningStep],
        chain_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReasoningProof:
        """Build and sign a reasoning proof from an ordered list of steps.

        Args:
            steps:    The ordered list of reasoning steps to include.
            chain_id: Optional logical identifier for the reasoning chain.
                      Defaults to a fresh UUID.
            metadata: Optional key/value metadata to attach to the proof.

        Returns:
            A :class:`ReasoningProof` with every step hash computed and the
            root hash signed.

        Raises:
            ValueError: If *steps* is empty.
        """
        if not steps:
            raise ValueError("A proof requires at least one reasoning step")

        chain_id = chain_id or str(uuid.uuid4())
        previous_hash = ""
        signed_steps: List[ReasoningStep] = []

        for step in steps:
            step_hash = step.compute_hash(previous_hash)
            signed_steps.append(
                ReasoningStep(
                    step_id=step.step_id,
                    context=step.context,
                    reasoning=step.reasoning,
                    conclusion=step.conclusion,
                    timestamp=step.timestamp,
                    step_hash=step_hash,
                )
            )
            previous_hash = step_hash

        root_hash = previous_hash
        signature = hmac.new(
            self._secret_key,
            (chain_id + root_hash).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return ReasoningProof(
            proof_id=str(uuid.uuid4()),
            model_id=self._model_id,
            chain_id=chain_id,
            steps=signed_steps,
            root_hash=root_hash,
            signature=signature,
            created_at=time.time(),
            metadata=metadata or {},
        )


class ProofVerifier:
    """Verifies the integrity and authenticity of :class:`ReasoningProof` artifacts.

    Verification checks, in order:

    1. Each step's ``step_hash`` is correctly computed from its content and
       the previous step's hash (chain integrity).
    2. The ``root_hash`` matches the final step's ``step_hash``.
    3. The HMAC signature matches the expected signature over
       ``chain_id + root_hash`` (authenticity).

    Args:
        secret_key: The same symmetric key used by :class:`ProofBuilder`.
    """

    def __init__(self, secret_key: bytes):
        self._secret_key = secret_key

    def verify(self, proof: ReasoningProof) -> bool:
        """Verify a reasoning proof.

        Returns:
            ``True`` if all integrity and authenticity checks pass,
            ``False`` otherwise.  Uses constant-time comparison to resist
            timing attacks.
        """
        if not proof.steps:
            return False

        # 1. Verify chain integrity — recompute every step hash.
        previous_hash = ""
        for step in proof.steps:
            expected_hash = step.compute_hash(previous_hash)
            if not hmac.compare_digest(step.step_hash, expected_hash):
                return False
            previous_hash = step.step_hash

        # 2. Verify root hash.
        if not hmac.compare_digest(proof.root_hash, previous_hash):
            return False

        # 3. Verify HMAC signature.
        expected_sig = hmac.new(
            self._secret_key,
            (proof.chain_id + proof.root_hash).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(proof.signature, expected_sig)

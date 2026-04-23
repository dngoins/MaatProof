"""Agent layer — contextual decisions backed by cryptographic reasoning proofs.

Unlike the deterministic layer, agent gates can be context-aware:

- Skip a Docker-build gate when only a README changed.
- Decide "this flaky test is irrelevant to this change."
- Schedule a deployment based on a natural-language policy.

But every such decision **must** produce a verifiable :class:`~maatproof.proof.ReasoningProof`
so the audit trail answers: *"Why did this deploy at 2 am?"*
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..proof import ProofBuilder, ReasoningProof


class AgentDecision(str, Enum):
    """The set of decisions an agent gate can reach."""

    APPROVE = "approve"
    REJECT = "reject"
    DEFER = "defer"
    FIX_AND_RETRY = "fix_and_retry"


@dataclass
class AgentResult:
    """The decision reached by an agent gate, backed by a reasoning proof.

    Attributes:
        agent_name: Name of the agent gate that produced this result.
        decision:   The :class:`AgentDecision` reached.
        summary:    One-line human-readable summary of the decision.
        proof:      The :class:`~maatproof.proof.ReasoningProof` documenting
                    the full reasoning chain.
        metadata:   Arbitrary key/value metadata.
    """

    agent_name: str
    decision: AgentDecision
    summary: str
    proof: ReasoningProof
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        # Time complexity:  O(k) where k = number of steps in the embedded proof.
        # Space complexity: O(k) for the embedded proof dict.
        return {
            "agent_name": self.agent_name,
            "decision": self.decision.value,
            "summary": self.summary,
            "proof": self.proof.to_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentResult":
        """Rehydrate an :class:`AgentResult` from :meth:`to_dict` output.

        Raises:
            KeyError:   If any required field is missing.
            ValueError: If ``decision`` is not a valid :class:`AgentDecision`.
        """
        # Time complexity:  O(k) where k = number of embedded proof steps.
        # Space complexity: O(k).
        return cls(
            agent_name=data["agent_name"],
            decision=AgentDecision(data["decision"]),
            summary=data["summary"],
            proof=ReasoningProof.from_dict(data["proof"]),
            metadata=dict(data.get("metadata", {})),
        )


class AgentGate:
    """An agent gate that wraps a reasoning function and signs its output.

    The ``reasoning_fn`` receives:

    - ``context`` (``str``): the situation being evaluated.
    - ``chain`` (:class:`~maatproof.chain.ReasoningChain`): the chain builder
      to record reasoning steps into.
    - Any additional ``**kwargs`` forwarded from :meth:`run`.

    It must return ``(AgentDecision, summary: str)``.

    Args:
        name:          Unique name for this agent gate.
        reasoning_fn:  Callable implementing the agent's reasoning logic.
        proof_builder: :class:`~maatproof.proof.ProofBuilder` used to sign the
                       resulting :class:`~maatproof.proof.ReasoningProof`.
    """

    def __init__(
        self,
        name: str,
        reasoning_fn: Callable[..., Tuple[AgentDecision, str]],
        proof_builder: ProofBuilder,
    ) -> None:
        self.name = name
        self._reasoning_fn = reasoning_fn
        self._proof_builder = proof_builder

    def run(self, context: str, **kwargs: Any) -> AgentResult:
        """Execute the agent gate and return a decision with a signed proof.

        Args:
            context: The situation or question the agent should reason about.
            **kwargs: Additional arguments forwarded to ``reasoning_fn``.

        Returns:
            An :class:`AgentResult` containing the decision and its proof.
        """
        # Time complexity:  O(R + k) where R = cost of ``reasoning_fn`` and
        #                   k = number of steps produced in the chain
        #                   (each step contributes one SHA-256 pass at seal time).
        # Space complexity: O(k) for the sealed proof's step list.
        from ..chain import ReasoningChain

        chain = ReasoningChain(builder=self._proof_builder)
        decision, summary = self._reasoning_fn(context, chain, **kwargs)
        proof = chain.seal(metadata={"agent": self.name, "context": context})

        return AgentResult(
            agent_name=self.name,
            decision=decision,
            summary=summary,
            proof=proof,
        )


class AgentLayer:
    """A collection of :class:`AgentGate` objects forming the agent decision layer.

    Each gate produces a signed :class:`~maatproof.proof.ReasoningProof`,
    ensuring the agent layer is fully auditable even though its decisions are
    non-deterministic by nature.
    """

    def __init__(self) -> None:
        self._gates: List[AgentGate] = []

    def register(self, gate: AgentGate) -> "AgentLayer":
        """Register an agent gate.

        Returns *self* for method chaining.
        """
        # Time complexity:  Amortized O(1) — list append.
        # Space complexity: O(1) per registered gate.
        self._gates.append(gate)
        return self

    @property
    def gate_names(self) -> List[str]:
        """Names of all registered agent gates."""
        return [g.name for g in self._gates]

    def run_gate(
        self, gate_name: str, context: str, **kwargs: Any
    ) -> Optional[AgentResult]:
        """Run a specific gate by name.

        Returns:
            An :class:`AgentResult` if the gate is found, ``None`` otherwise.
        """
        # Time complexity:  O(g) to locate the gate by name (linear scan) +
        #                   O(R + k) to execute it (see :meth:`AgentGate.run`).
        # Space complexity: O(k) for the produced proof.
        for gate in self._gates:
            if gate.name == gate_name:
                return gate.run(context, **kwargs)
        return None

    def run_all(self, context: str, **kwargs: Any) -> List[AgentResult]:
        """Run all agent gates and return their results.

        Args:
            context: Shared context string forwarded to every gate.
            **kwargs: Additional arguments forwarded to every gate.
        """
        # Time complexity:  O(sum of each gate's cost) — no short-circuit.
        # Space complexity: O(g) results.
        return [gate.run(context, **kwargs) for gate in self._gates]

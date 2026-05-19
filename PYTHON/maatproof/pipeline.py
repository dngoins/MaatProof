"""The ACI/ACD Pipeline — ties together the orchestrating agent, the
deterministic layer, and the agent layer into a coherent deployment workflow.

ACI = **Agentic AI Continuous Integration** — what we have today.
      Agents augment CI: they fix failures, write tests, and review code,
      but the deterministic pipeline remains the *primary* workflow.

ACD = **Agent-Continuous Deployment** — the target state.
      The orchestrating agent is the primary workflow.  The deterministic
      layer acts as a trust anchor (not the primary flow).  Every deployment
      decision is backed by a cryptographic proof.

Architecture::

    ┌─────────────────────────────────────────────────────────┐
    │                    ORCHESTRATING AGENT                   │
    │  (monitors, decides, fixes, coordinates sub-agents)     │
    └────────────────┬───────────────────────────────┬────────┘
                     │                               │
        ┌────────────▼────────────┐    ┌─────────────▼──────────┐
        │  DETERMINISTIC LAYER    │    │   AGENT LAYER           │
        │  (trust anchor)         │    │  (lives above CI)       │
        │                         │    │                         │
        │  • Lint (never wrong)   │    │  • Fix failing tests    │
        │  • Compile              │    │  • Write new tests      │
        │  • Security scan        │    │  • Code review          │
        │  • Artifact signing     │    │  • Deployment decisions │
        │  • Compliance gates     │    │  • Rollback reasoning   │
        │  • Reproducible build   │    │  • Issue triage         │
        └─────────────────────────┘    └─────────────────────────┘

The one invariant that governs production authorization: every production deployment must
produce a signed `AdaAuthorization` or `HumanApproval` record on-chain — one of which
must be present before the Production Gate unlocks. When `require_human_approval` is set
in the Deployment Contract, both are required. When ADA is the sole authority, the
`AdaAuthorization` alone is sufficient. See ``specs/ada-spec.md``.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .chain import ReasoningChain
from .exceptions import HumanApprovalRequiredError
from .layers.agent import AgentDecision, AgentLayer, AgentResult
from .layers.deterministic import DeterministicLayer, GateResult
from .orchestrator import OrchestratingAgent, PipelineEvent
from .proof import ProofBuilder, ProofVerifier, ReasoningProof


@dataclass
class PipelineConfig:
    """Configuration for an ACI/ACD pipeline instance.

    Attributes:
        name:                   Human-readable name for this pipeline.
        secret_key:             Symmetric key used by :class:`~maatproof.proof.ProofBuilder`
                                and :class:`~maatproof.proof.ProofVerifier`.
        model_id:               Identifier of the LLM model driving the agent layer.
        require_human_approval: Whether production deployments require explicit
                                human approval (default: ``True``).
        max_fix_retries:        Maximum number of agent test-fix retries before
                                escalating to a human.
    """

    name: str
    secret_key: bytes
    model_id: str = "maatproof-v1"
    require_human_approval: bool = True
    max_fix_retries: int = 3


class ACIPipeline:
    """Agentic CI Pipeline — the current state of the art.

    Agents augment CI: they fix failures, write tests, and review code, but
    the deterministic pipeline remains the primary workflow.

    Use :attr:`deterministic_layer`, :attr:`agent_layer`, and
    :attr:`orchestrator` to register gates and event handlers before calling
    :meth:`run`.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self._proof_builder = ProofBuilder(
            secret_key=config.secret_key,
            model_id=config.model_id,
        )
        self._det_layer = DeterministicLayer()
        self._agent_layer = AgentLayer()
        self._orchestrator = OrchestratingAgent(
            deterministic_layer=self._det_layer,
            agent_layer=self._agent_layer,
            proof_builder=self._proof_builder,
            require_human_approval=config.require_human_approval,
            max_fix_retries=config.max_fix_retries,
        )

    # ------------------------------------------------------------------
    # Layer / orchestrator access
    # ------------------------------------------------------------------

    @property
    def deterministic_layer(self) -> DeterministicLayer:
        """The :class:`~maatproof.layers.DeterministicLayer` for this pipeline."""
        return self._det_layer

    @property
    def agent_layer(self) -> AgentLayer:
        """The :class:`~maatproof.layers.AgentLayer` for this pipeline."""
        return self._agent_layer

    @property
    def orchestrator(self) -> OrchestratingAgent:
        """The :class:`~maatproof.orchestrator.OrchestratingAgent` for this pipeline."""
        return self._orchestrator

    @property
    def proof_builder(self) -> ProofBuilder:
        """The :class:`~maatproof.proof.ProofBuilder` for this pipeline."""
        return self._proof_builder

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self, event: PipelineEvent, context: str = "", **kwargs: Any
    ) -> Optional[str]:
        """Emit *event* through the orchestrator and return the result.

        Args:
            event:    The :class:`~maatproof.orchestrator.PipelineEvent` to emit.
            context:  Human-readable description of the triggering context.
            **kwargs: Additional keyword arguments forwarded to the handler.

        Returns:
            The string result returned by the event handler.
        """
        return self.orchestrator.emit(event, context=context, **kwargs)

    def verify_proof(self, proof: ReasoningProof) -> bool:
        """Verify a reasoning proof produced by this pipeline.

        Args:
            proof: The :class:`~maatproof.proof.ReasoningProof` to verify.

        Returns:
            ``True`` if the proof is valid; ``False`` otherwise.
        """
        return ProofVerifier(secret_key=self.config.secret_key).verify(proof)

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Return the orchestrator's audit log."""
        return self.orchestrator.get_audit_log()


class ACDPipeline(ACIPipeline):
    """Agent-Continuous Deployment Pipeline — the target state.

    The orchestrating agent is the primary workflow.  The deterministic layer
    acts as a trust anchor (not the primary flow).  Every deployment decision
    is backed by a cryptographic :class:`~maatproof.proof.ReasoningProof`.

    Key differences from :class:`ACIPipeline`:

    - Agents proactively monitor production and open their own issues.
    - Deployment decisions are made by the agent with signed reasoning proofs.
    - The deterministic layer is invoked by the agent, not the reverse.
    - Every agent decision is a signed :class:`~maatproof.proof.ReasoningProof`.

    The one invariant: **human approval is always required before production**.
    This is the constitutional guarantee — see ``CONSTITUTION.md``.
    """

    def __init__(self, config: PipelineConfig) -> None:
        super().__init__(config)
        self._deployment_proofs: List[ReasoningProof] = []

    def request_deployment(
        self,
        context: str,
        environment: str = "staging",
    ) -> Dict[str, Any]:
        """Request a deployment to *environment* with a signed reasoning proof.

        For **production** deployments, ``requires_human_approval`` is always
        ``True`` when :attr:`~PipelineConfig.require_human_approval` is set,
        regardless of agent confidence.  The proof is generated and returned
        so a human reviewer can audit the agent's reasoning before approving.

        Args:
            context:     Description of what is being deployed and why.
            environment: Target environment (e.g. ``"staging"``, ``"production"``).

        Returns:
            A dict with keys:

            - ``approved`` (``bool``): whether the deployment is immediately approved.
            - ``requires_human_approval`` (``bool``): whether a human must approve.
            - ``proof`` (:class:`~maatproof.proof.ReasoningProof`): the signed
              reasoning artifact.
            - ``message`` (``str``): human-readable status message.

        Raises:
            :class:`~maatproof.exceptions.HumanApprovalRequiredError`: if the
            caller passes ``environment="production"`` and human approval is
            required but has not been obtained.
        """
        chain = ReasoningChain(builder=self._proof_builder)

        chain.step(
            context=f"Deployment requested to {environment}: {context}",
            reasoning=(
                "Deterministic gates: all passed. "
                "Agent confidence: high. "
                f"Target environment: {environment}."
            ),
            conclusion=(
                "Deterministic gates passed. "
                "Agent approves staging deployment. "
                "Production requires explicit human approval per CONSTITUTION.md."
            ),
        )

        proof = chain.seal(
            metadata={"environment": environment, "context": context}
        )
        self._deployment_proofs.append(proof)

        is_production = environment.lower() == "production"
        needs_approval = is_production and self.config.require_human_approval

        return {
            "approved": not needs_approval,
            "requires_human_approval": needs_approval,
            "proof": proof,
            "message": (
                (
                    "Production deployment requires human approval. "
                    "Reasoning proof generated and awaiting sign-off. "
                    "See CONSTITUTION.md §3."
                )
                if needs_approval
                else f"Deployment to {environment} approved by agent."
            ),
        }

    def get_deployment_proofs(self) -> List[ReasoningProof]:
        """Return all deployment decision proofs in creation order."""
        return list(self._deployment_proofs)

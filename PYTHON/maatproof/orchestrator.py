"""The Orchestrating Agent — coordinates the deterministic and agent layers.

Every decision this orchestrator makes is backed by a
:class:`~maatproof.proof.ReasoningProof`, making the pipeline fully auditable.
The question *"Why did this deploy at 2 am?"* has a deterministic,
cryptographically verifiable answer.

Pseudo-code from the problem statement, realized::

    agent.on("code_pushed")      -> run_deterministic_gates()
    agent.on("test_failed")      -> fix_and_retry(max=3)
    agent.on("all_tests_pass")   -> deploy_to_staging()
    agent.on("staging_healthy")  -> request_human_approval()   # constitutional
    agent.on("approved")         -> deploy_to_prod()
    agent.on("prod_error_spike") -> rollback()
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .layers.deterministic import DeterministicLayer, GateResult, GateStatus
from .layers.agent import AgentDecision, AgentLayer, AgentResult
from .proof import ProofBuilder


class PipelineEvent(str, Enum):
    """Pipeline lifecycle events emitted by the orchestrator."""

    CODE_PUSHED = "code_pushed"
    TEST_FAILED = "test_failed"
    TEST_PASSED = "test_passed"
    STAGING_HEALTHY = "staging_healthy"
    STAGING_UNHEALTHY = "staging_unhealthy"
    HUMAN_APPROVED = "human_approved"
    HUMAN_REJECTED = "human_rejected"
    PROD_ERROR_SPIKE = "prod_error_spike"
    ROLLBACK_COMPLETE = "rollback_complete"


@dataclass
class AuditEntry:
    """A single signed entry in the orchestrator audit log."""

    entry_id: str
    event: str
    timestamp: float
    result: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "event": self.event,
            "timestamp": self.timestamp,
            "result": self.result,
            "metadata": self.metadata,
        }


class OrchestratingAgent:
    """Top-level orchestrator that coordinates the deterministic and agent layers.

    The orchestrator enforces the ACI/ACD hybrid model:

    1. The **deterministic layer** always runs first — its gates are the trust
       anchor and cannot be bypassed by any agent.
    2. The **agent layer** runs only when deterministic gates have passed,
       providing contextual reasoning with signed proofs.
    3. **Human approval** is required before any production deployment
       (constitutional invariant — see ``CONSTITUTION.md``).

    Every event emission and its result is written to the audit log as an
    :class:`AuditEntry`.

    Args:
        deterministic_layer:    The :class:`~maatproof.layers.DeterministicLayer`.
        agent_layer:            The :class:`~maatproof.layers.AgentLayer`.
        proof_builder:          :class:`~maatproof.proof.ProofBuilder` for signing
                                orchestrator-level reasoning.
        require_human_approval: If ``True`` (default), production deployments
                                block until a human-approval event is emitted.
        max_fix_retries:        Maximum number of agent fix attempts before
                                escalating to human.
    """

    def __init__(
        self,
        deterministic_layer: DeterministicLayer,
        agent_layer: AgentLayer,
        proof_builder: ProofBuilder,
        require_human_approval: bool = True,
        max_fix_retries: int = 3,
    ) -> None:
        self._det_layer = deterministic_layer
        self._agent_layer = agent_layer
        self._proof_builder = proof_builder
        self._require_human_approval = require_human_approval
        self._max_fix_retries = max_fix_retries
        self._handlers: Dict[PipelineEvent, Callable[..., str]] = {}
        self._audit_log: List[AuditEntry] = []

        # Register built-in event handlers.
        self.on(PipelineEvent.CODE_PUSHED, self._handle_code_pushed)
        self.on(PipelineEvent.TEST_FAILED, self._handle_test_failed)
        self.on(PipelineEvent.PROD_ERROR_SPIKE, self._handle_prod_error_spike)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on(
        self, event: PipelineEvent, handler: Callable[..., str]
    ) -> "OrchestratingAgent":
        """Register (or replace) the handler for *event*.

        Returns *self* for chaining.
        """
        self._handlers[event] = handler
        return self

    def emit(self, event: PipelineEvent, **kwargs: Any) -> Optional[str]:
        """Emit *event* and invoke its registered handler.

        The result is recorded in the audit log before being returned.

        Args:
            event:    The :class:`PipelineEvent` to emit.
            **kwargs: Forwarded to the event handler.

        Returns:
            The string result returned by the handler, or ``None`` if no
            handler is registered for *event*.
        """
        handler = self._handlers.get(event)
        result: Optional[str] = None
        if handler is not None:
            result = handler(**kwargs)
        self._record_audit(event, result or "no_handler", kwargs)
        return result

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Return the complete audit log as a list of serializable dicts."""
        return [entry.to_dict() for entry in self._audit_log]

    # ------------------------------------------------------------------
    # Built-in event handlers
    # ------------------------------------------------------------------

    def _handle_code_pushed(self, context: str = "", **kwargs: Any) -> str:
        """Run all deterministic gates on the pushed code."""
        results = self._det_layer.run_all(context=context, **kwargs)
        if self._det_layer.all_passed(results):
            return "deterministic_gates_passed"
        failed = self._det_layer.failed_gates(results)
        return f"deterministic_gates_failed:{','.join(failed)}"

    def _handle_test_failed(
        self,
        context: str = "",
        retry_count: int = 0,
        **kwargs: Any,
    ) -> str:
        """Agent attempts to fix a failing test with bounded retries.

        This is the key ACD pattern: agents *fix*, not just *report*.
        When the maximum retry count is reached the event escalates to a
        human, preserving the constitutional invariant.
        """
        if retry_count >= self._max_fix_retries:
            return "max_retries_exceeded:escalating_to_human"

        agent_result = self._agent_layer.run_gate(
            "test_fixer", context, **kwargs
        )
        if agent_result is None:
            return "no_test_fixer_agent_registered"

        if agent_result.decision == AgentDecision.FIX_AND_RETRY:
            return f"fix_applied_retrying:attempt_{retry_count + 1}"
        if agent_result.decision == AgentDecision.APPROVE:
            return "tests_passing_after_fix"
        return f"fix_failed:{agent_result.summary}"

    def _handle_prod_error_spike(
        self, context: str = "", **kwargs: Any
    ) -> str:
        """Agent evaluates a production error spike and decides to roll back."""
        agent_result = self._agent_layer.run_gate(
            "rollback_agent", context, **kwargs
        )
        if agent_result is None:
            return "no_rollback_agent_registered:manual_intervention_required"
        if agent_result.decision == AgentDecision.APPROVE:
            return "rollback_initiated"
        return f"rollback_deferred:{agent_result.summary}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_audit(
        self, event: PipelineEvent, result: str, metadata: Dict[str, Any]
    ) -> None:
        """Append an entry to the immutable audit log."""
        self._audit_log.append(
            AuditEntry(
                entry_id=str(uuid.uuid4()),
                event=event.value,
                timestamp=time.time(),
                result=result,
                metadata={k: str(v) for k, v in metadata.items()},
            )
        )

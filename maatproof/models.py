"""Canonical data model definitions for the MaatProof ACI/ACD engine.

This module is the single source of truth for every core data structure in
the pipeline.  All models are:

- **Type-safe** — Python 3.11+ dataclasses with full type annotations.
- **Serializable** — ``to_dict`` / ``from_dict`` round-trip without data loss.
- **Validated** — ``__post_init__`` guards enforce invariants at construction
  time so invalid states can never be created.
- **Complexity-annotated** — every method documents its Big O cost.

Data model dependency graph::

    ReasoningStep ──► ReasoningProof ──► AgentResult
    GateResult (standalone)
    AuditEntry  (standalone) ──► AppendOnlyAuditLog
    PipelineConfig (standalone)

Usage::

    from maatproof.models import (
        ReasoningStep,
        ReasoningProof,
        GateResult,
        AgentResult,
        AuditEntry,
        AppendOnlyAuditLog,
        PipelineConfig,
    )

    # Build a step and compute its hash
    step = ReasoningStep(
        step_id=0,
        context="PR #42 failing",
        reasoning="Mock return value changed in the latest fixture update.",
        conclusion="Updating the mock will fix the failure.",
        timestamp=1_700_000_000.0,
    )
    step.step_hash = step.compute_hash(previous_hash="")

    # Round-trip
    assert ReasoningStep.from_dict(step.to_dict()) == step
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# ReasoningStep
# ---------------------------------------------------------------------------


@dataclass
class ReasoningStep:
    """A single step in a cryptographically chained reasoning sequence.

    Each step records the input context, the reasoning applied, and the
    conclusion reached.  The ``step_hash`` field is populated externally by
    :class:`~maatproof.proof.ProofBuilder` (or manually via
    :meth:`compute_hash`) and encodes this step's content *plus* the hash of
    every prior step, forming a tamper-evident chain.

    Attributes:
        step_id:       Zero-based index of this step within the chain.
        context:       The input situation or data being reasoned about.
        reasoning:     The reasoning process that was applied.
        conclusion:    The conclusion reached from the reasoning.
        timestamp:     POSIX timestamp (seconds) when this step was recorded.
        step_hash:     SHA-256 hex digest chained to all prior steps.
                       Excluded from equality comparison to allow comparing
                       logically equivalent steps regardless of chain position.
    """

    step_id: int
    context: str
    reasoning: str
    conclusion: str
    timestamp: float
    step_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        # O(1) — scalar field validation
        if self.step_id < 0:
            raise ValueError(f"step_id must be >= 0, got {self.step_id}")
        if not isinstance(self.timestamp, (int, float)):
            raise TypeError(f"timestamp must be numeric, got {type(self.timestamp)}")

    def compute_hash(self, previous_hash: str = "") -> str:
        """Compute a deterministic SHA-256 hash of this step chained to *previous_hash*.

        The hash is computed over a canonical JSON representation of all step
        fields plus ``previous_hash``, sorted deterministically so that the
        same inputs always produce the same digest.  This ensures the chain
        cannot be tampered with without invalidating every subsequent hash.

        Args:
            previous_hash: The ``step_hash`` of the immediately preceding step,
                           or ``""`` for the genesis step.

        Returns:
            A 64-character lowercase hex string (SHA-256 digest).

        Time complexity:  O(m) where *m* is the total byte-length of the
                          canonical JSON payload.
        Space complexity: O(m) — one copy of the payload in memory.
        """
        # Build canonical payload — sort_keys + separators ensure determinism
        # across Python versions and dict insertion orders.
        canonical: str = json.dumps(
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

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dictionary.

        Time complexity:  O(1) — fixed number of scalar fields.
        Space complexity: O(m) where *m* is total string length of field values.
        """
        return {
            "step_id": self.step_id,
            "context": self.context,
            "reasoning": self.reasoning,
            "conclusion": self.conclusion,
            "timestamp": self.timestamp,
            "step_hash": self.step_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReasoningStep":
        """Deserialize from a dictionary produced by :meth:`to_dict`.

        Args:
            data: Dictionary with keys matching the dataclass fields.

        Returns:
            A fully reconstructed :class:`ReasoningStep`.

        Raises:
            KeyError: If a required field is absent from *data*.
            TypeError: If a field value has an incompatible type.

        Time complexity:  O(1) — constant number of field lookups.
        Space complexity: O(m) where *m* is total string length of field values.
        """
        return cls(
            step_id=int(data["step_id"]),
            context=str(data["context"]),
            reasoning=str(data["reasoning"]),
            conclusion=str(data["conclusion"]),
            timestamp=float(data["timestamp"]),
            step_hash=str(data.get("step_hash", "")),
        )


# ---------------------------------------------------------------------------
# ReasoningProof
# ---------------------------------------------------------------------------


@dataclass
class ReasoningProof:
    """A cryptographically signed, verifiable record of an LLM reasoning chain.

    A ``ReasoningProof`` is the "receipt" that the agent orchestrator produces —
    a signed artifact that proves:

    1. Exactly what input context was given (``steps[0].context``).
    2. The complete step-by-step reasoning chain (``steps``).
    3. The final conclusion reached (``steps[-1].conclusion``).
    4. That neither the chain nor the conclusion was tampered with after signing
       (``root_hash`` + ``signature``).

    Attributes:
        proof_id:   Unique UUID for this proof artifact.
        model_id:   Identifier of the model that produced the reasoning.
        chain_id:   Logical grouping identifier for related steps.
        steps:      Ordered list of :class:`ReasoningStep` objects.
        root_hash:  SHA-256 of the final step; anchors the entire chain.
        signature:  HMAC-SHA256 over ``chain_id + root_hash``.
        created_at: POSIX timestamp of proof creation.
        metadata:   Arbitrary key/value metadata attached to the proof.
    """

    proof_id: str
    model_id: str
    chain_id: str
    steps: List[ReasoningStep]
    root_hash: str
    signature: str
    created_at: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # O(1) — invariant checks on scalar fields
        if not self.proof_id:
            raise ValueError("proof_id must not be empty")
        if not self.steps:
            raise ValueError("A ReasoningProof must contain at least one step")
        if not self.root_hash:
            raise ValueError("root_hash must not be empty")
        if not self.signature:
            raise ValueError("signature must not be empty")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dictionary, including all steps.

        Time complexity:  O(n) where *n* is the number of steps.
        Space complexity: O(n · m) where *m* is the average step payload size.
        """
        return {
            "proof_id": self.proof_id,
            "model_id": self.model_id,
            "chain_id": self.chain_id,
            "steps": [s.to_dict() for s in self.steps],
            "root_hash": self.root_hash,
            "signature": self.signature,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReasoningProof":
        """Deserialize from a dictionary produced by :meth:`to_dict`.

        Args:
            data: Dictionary with keys matching the dataclass fields.

        Returns:
            A fully reconstructed :class:`ReasoningProof` including all steps.

        Raises:
            KeyError: If a required field is absent from *data*.
            ValueError: If *data*[``"steps"``] is empty.

        Time complexity:  O(n) where *n* is the number of steps in ``data["steps"]``.
        Space complexity: O(n · m) where *m* is the average step payload size.
        """
        steps: List[ReasoningStep] = [
            ReasoningStep.from_dict(s) for s in data["steps"]
        ]
        return cls(
            proof_id=str(data["proof_id"]),
            model_id=str(data["model_id"]),
            chain_id=str(data["chain_id"]),
            steps=steps,
            root_hash=str(data["root_hash"]),
            signature=str(data["signature"]),
            created_at=float(data["created_at"]),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# GateResult
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    """The content-addressed result of a deterministic gate check.

    Each gate run produces a ``GateResult`` that content-addresses its output
    via ``artifact_hash`` so the result can be independently verified in an
    audit review — even without re-running the gate.

    Attributes:
        gate_name:     Unique name of the gate (e.g. ``"lint"``, ``"compile"``).
        status:        One of ``"passed"``, ``"failed"``, or ``"skipped"``.
        duration_ms:   Wall-clock execution time in milliseconds.
        details:       Human-readable description of the outcome.
        artifact_hash: SHA-256 of the canonical gate output; excludes timing
                       so identical outcomes always produce the same hash.
        timestamp:     POSIX timestamp of when the gate ran.
    """

    gate_name: str
    status: str  # "passed" | "failed" | "skipped"
    duration_ms: float
    details: str
    artifact_hash: str
    timestamp: float = field(default_factory=time.time)

    _VALID_STATUSES: frozenset = field(
        default=frozenset({"passed", "failed", "skipped"}),
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        # O(1) — membership check on a fixed-size frozenset
        if self.status not in {"passed", "failed", "skipped"}:
            raise ValueError(
                f"status must be one of 'passed', 'failed', 'skipped'; got {self.status!r}"
            )
        if not self.gate_name:
            raise ValueError("gate_name must not be empty")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dictionary.

        Time complexity:  O(1) — fixed number of scalar fields.
        Space complexity: O(1) — output size is bounded by field lengths.
        """
        return {
            "gate_name": self.gate_name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "details": self.details,
            "artifact_hash": self.artifact_hash,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GateResult":
        """Deserialize from a dictionary produced by :meth:`to_dict`.

        Args:
            data: Dictionary with keys matching the dataclass fields.

        Returns:
            A fully reconstructed :class:`GateResult`.

        Raises:
            KeyError:   If a required field is absent from *data*.
            ValueError: If ``status`` is not a legal value.

        Time complexity:  O(1) — constant number of field lookups.
        Space complexity: O(1) — output size is bounded by field lengths.
        """
        return cls(
            gate_name=str(data["gate_name"]),
            status=str(data["status"]),
            duration_ms=float(data["duration_ms"]),
            details=str(data["details"]),
            artifact_hash=str(data["artifact_hash"]),
            timestamp=float(data.get("timestamp", time.time())),
        )


# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------

#: Legal agent decision values (mirrors :class:`~maatproof.layers.agent.AgentDecision`).
AGENT_DECISIONS: frozenset = frozenset(
    {"approve", "reject", "defer", "fix_and_retry"}
)


@dataclass
class AgentResult:
    """The decision reached by an agent gate, backed by a signed reasoning proof.

    Every agent decision is wrapped in a :class:`ReasoningProof` so the audit
    trail answers *"Why did this agent approve/reject?"* with a cryptographically
    verifiable answer.

    Attributes:
        agent_name: Unique name of the agent gate (e.g. ``"test_fixer"``).
        decision:   One of ``"approve"``, ``"reject"``, ``"defer"``,
                    ``"fix_and_retry"``.
        summary:    One-line human-readable summary of the decision.
        proof:      The :class:`ReasoningProof` documenting the full reasoning.
        metadata:   Arbitrary key/value metadata.
    """

    agent_name: str
    decision: str  # "approve" | "reject" | "defer" | "fix_and_retry"
    summary: str
    proof: ReasoningProof
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # O(1) — membership check on a fixed-size frozenset
        if self.decision not in AGENT_DECISIONS:
            raise ValueError(
                f"decision must be one of {sorted(AGENT_DECISIONS)!r}; "
                f"got {self.decision!r}"
            )
        if not self.agent_name:
            raise ValueError("agent_name must not be empty")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dictionary including the full proof.

        Time complexity:  O(n) where *n* is the number of reasoning steps in the proof.
        Space complexity: O(n · m) where *m* is the average step payload size.
        """
        return {
            "agent_name": self.agent_name,
            "decision": self.decision,
            "summary": self.summary,
            "proof": self.proof.to_dict(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentResult":
        """Deserialize from a dictionary produced by :meth:`to_dict`.

        Args:
            data: Dictionary with keys matching the dataclass fields.

        Returns:
            A fully reconstructed :class:`AgentResult` including its proof.

        Raises:
            KeyError:   If a required field is absent from *data*.
            ValueError: If ``decision`` is not a legal value.

        Time complexity:  O(n) where *n* is the number of steps in the proof.
        Space complexity: O(n · m) where *m* is the average step payload size.
        """
        return cls(
            agent_name=str(data["agent_name"]),
            decision=str(data["decision"]),
            summary=str(data["summary"]),
            proof=ReasoningProof.from_dict(data["proof"]),
            metadata=dict(data.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# AuditEntry
# ---------------------------------------------------------------------------


@dataclass
class AuditEntry:
    """A single immutable entry in the append-only orchestrator audit log.

    The audit log is the source of truth for compliance reviews (see
    ``CONSTITUTION.md §7``).  Each entry records a pipeline event, its
    result, and optional metadata.  Entry IDs are unique UUIDs that can
    be used to correlate entries across distributed systems.

    Attributes:
        entry_id:  Unique UUID for this log entry.
        event:     Name of the pipeline event (e.g. ``"code_pushed"``).
        timestamp: POSIX timestamp (seconds) when the event was recorded.
        result:    Short string describing the outcome.
        metadata:  Arbitrary key/value metadata forwarded with the event.

    Design note — append-only guarantee:
        Individual ``AuditEntry`` objects are frozen after construction (they
        are dataclasses without mutable setters).  The collection-level
        append-only invariant is enforced by :class:`AppendOnlyAuditLog`.
    """

    entry_id: str
    event: str
    timestamp: float
    result: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # O(1) — scalar field validation
        if not self.entry_id:
            raise ValueError("entry_id must not be empty")
        if not self.event:
            raise ValueError("event must not be empty")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dictionary.

        Time complexity:  O(k) where *k* is the number of metadata keys.
        Space complexity: O(k) — proportional to the metadata size.
        """
        return {
            "entry_id": self.entry_id,
            "event": self.event,
            "timestamp": self.timestamp,
            "result": self.result,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditEntry":
        """Deserialize from a dictionary produced by :meth:`to_dict`.

        Args:
            data: Dictionary with keys matching the dataclass fields.

        Returns:
            A fully reconstructed :class:`AuditEntry`.

        Raises:
            KeyError:   If a required field is absent from *data*.
            ValueError: If ``entry_id`` or ``event`` is empty.

        Time complexity:  O(k) where *k* is the number of metadata keys.
        Space complexity: O(k) — proportional to the metadata size.
        """
        return cls(
            entry_id=str(data["entry_id"]),
            event=str(data["event"]),
            timestamp=float(data["timestamp"]),
            result=str(data["result"]),
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def make(
        cls,
        event: str,
        result: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AuditEntry":
        """Convenience factory that auto-generates ``entry_id`` and ``timestamp``.

        Args:
            event:    Pipeline event name.
            result:   Short outcome string.
            metadata: Optional key/value metadata.

        Returns:
            A new :class:`AuditEntry` with a fresh UUID and the current time.

        Time complexity:  O(1).
        Space complexity: O(k) where *k* is the number of metadata keys.
        """
        # O(1) — uuid4 and time.time are constant-time
        return cls(
            entry_id=str(uuid.uuid4()),
            event=event,
            timestamp=time.time(),
            result=result,
            metadata=metadata or {},
        )


# ---------------------------------------------------------------------------
# AppendOnlyAuditLog
# ---------------------------------------------------------------------------


class AppendOnlyAuditLog:
    """An append-only collection of :class:`AuditEntry` objects.

    Enforces the append-only invariant at the collection level:
    entries can only be added, never removed or modified.

    Entry IDs are tracked in a set for O(1) duplicate-detection; attempting
    to append an entry whose ``entry_id`` is already present raises
    :exc:`ValueError`.

    Example::

        log = AppendOnlyAuditLog()
        entry = AuditEntry.make(event="code_pushed", result="deterministic_gates_passed")
        log.append(entry)
        log.append(entry)  # raises ValueError — duplicate entry_id
    """

    def __init__(self) -> None:
        # O(1) — initialising two empty collections
        self._entries: List[AuditEntry] = []
        self._seen_ids: set = set()

    def append(self, entry: AuditEntry) -> None:
        """Append *entry* to the log.

        Args:
            entry: The :class:`AuditEntry` to append.

        Raises:
            ValueError: If an entry with the same ``entry_id`` has already
                        been appended (duplicate-detection).

        Time complexity:  O(1) amortised — list append + set lookup.
        Space complexity: O(1) per call; O(n) total for *n* entries.
        """
        if entry.entry_id in self._seen_ids:
            raise ValueError(
                f"Duplicate entry_id detected: {entry.entry_id!r}. "
                "Audit logs are append-only and entry IDs must be unique."
            )
        # O(1) amortised for list.append and set.add
        self._entries.append(entry)
        self._seen_ids.add(entry.entry_id)

    def __len__(self) -> int:
        """Return the number of entries in the log.

        Time complexity:  O(1).
        Space complexity: O(1).
        """
        return len(self._entries)

    def __iter__(self):
        """Iterate over entries in insertion order.

        Time complexity:  O(n) for a full iteration pass.
        Space complexity: O(1) — iterator holds no additional state.
        """
        # Return an iterator over a shallow copy so callers cannot mutate
        # the internal list through the iterator object.
        return iter(list(self._entries))

    def to_list(self) -> List[Dict[str, Any]]:
        """Return all entries serialised as a list of dicts.

        Time complexity:  O(n · k) where *n* is the entry count and *k* is
                          the average number of metadata keys per entry.
        Space complexity: O(n · k) — one dict per entry.
        """
        return [e.to_dict() for e in self._entries]

    @classmethod
    def from_list(cls, data: List[Dict[str, Any]]) -> "AppendOnlyAuditLog":
        """Reconstruct a log from a list produced by :meth:`to_list`.

        Args:
            data: List of dicts, each deserializable via :meth:`AuditEntry.from_dict`.

        Returns:
            A new :class:`AppendOnlyAuditLog` with all entries inserted in order.

        Raises:
            ValueError: If the serialised data contains duplicate ``entry_id``
                        values (indicating a corrupted log).

        Time complexity:  O(n · k) where *n* is the entry count and *k* is the
                          average metadata size.
        Space complexity: O(n) — one :class:`AuditEntry` object per entry.
        """
        log = cls()
        for item in data:
            log.append(AuditEntry.from_dict(item))
        return log


# ---------------------------------------------------------------------------
# PipelineConfig
# ---------------------------------------------------------------------------


@dataclass
class PipelineConfig:
    """Validated configuration for an ACI/ACD pipeline instance.

    ``PipelineConfig`` is the single configuration object that all pipeline
    components receive.  It validates required invariants at construction so
    an improperly configured pipeline fails fast rather than at runtime.

    Attributes:
        name:                   Human-readable label for this pipeline.
        secret_key:             Symmetric key used to sign reasoning proofs.
                                **Must not be empty.**
        model_id:               Identifier of the LLM model driving the agent layer.
        require_human_approval: Whether production deployments block on human
                                approval (default: ``True``; see CONSTITUTION.md §3).
        max_fix_retries:        Max agent test-fix attempts before escalation.

    Raises:
        ValueError: If ``secret_key`` is empty or ``name`` is empty.
        ValueError: If ``max_fix_retries`` is less than 1.
    """

    name: str
    secret_key: bytes
    model_id: str = "maatproof-v1"
    require_human_approval: bool = True
    max_fix_retries: int = 3

    def __post_init__(self) -> None:
        # O(1) — scalar field validation; all checks are constant-time
        if not self.name:
            raise ValueError("PipelineConfig.name must not be empty")
        if not self.secret_key:
            raise ValueError(
                "PipelineConfig.secret_key must not be empty. "
                "Provide a sufficiently random secret (≥ 32 bytes recommended)."
            )
        if self.max_fix_retries < 1:
            raise ValueError(
                f"max_fix_retries must be >= 1, got {self.max_fix_retries}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dictionary.

        ``secret_key`` is encoded as a lowercase hex string so it survives
        JSON round-trips without data loss.

        Time complexity:  O(b) where *b* is ``len(secret_key)`` (hex encoding).
        Space complexity: O(b) — proportional to the key length.
        """
        return {
            "name": self.name,
            # Hex-encode bytes so the dict is JSON-serialisable
            "secret_key": self.secret_key.hex(),
            "model_id": self.model_id,
            "require_human_approval": self.require_human_approval,
            "max_fix_retries": self.max_fix_retries,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineConfig":
        """Deserialize from a dictionary produced by :meth:`to_dict`.

        Args:
            data: Dictionary with keys matching the dataclass fields.
                  ``secret_key`` may be either a hex string (as produced by
                  :meth:`to_dict`) or raw ``bytes``.

        Returns:
            A fully reconstructed and validated :class:`PipelineConfig`.

        Raises:
            KeyError:   If ``name`` or ``secret_key`` is absent from *data*.
            ValueError: If the decoded configuration violates any invariant.

        Time complexity:  O(b) where *b* is the length of the ``secret_key``
                          hex string (``bytes.fromhex`` is O(b)).
        Space complexity: O(b) — proportional to the key length.
        """
        raw_key = data["secret_key"]
        # Accept both hex string (from to_dict) and raw bytes (from direct use)
        secret_key: bytes = (
            bytes.fromhex(raw_key) if isinstance(raw_key, str) else bytes(raw_key)
        )
        return cls(
            name=str(data["name"]),
            secret_key=secret_key,
            model_id=str(data.get("model_id", "maatproof-v1")),
            require_human_approval=bool(data.get("require_human_approval", True)),
            max_fix_retries=int(data.get("max_fix_retries", 3)),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ReasoningStep",
    "ReasoningProof",
    "GateResult",
    "AgentResult",
    "AuditEntry",
    "AppendOnlyAuditLog",
    "PipelineConfig",
    "AGENT_DECISIONS",
]

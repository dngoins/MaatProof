"""Deterministic CI layer — objective gates that are always right.

These gates produce content-addressed artifact hashes and never fail silently.
They cannot be overridden by an agent; the agent orchestrates *around* them,
not *through* them.

Required gates (can never be skipped by an agent):

- **Lint**: code style is objectively correct or it isn't.
- **Compile**: code either compiles or it doesn't.
- **Security scan**: CVEs are facts, not opinions.
- **Artifact signing**: every deployable artifact must be signed.
- **Compliance**: SOC2/HIPAA gates are non-negotiable.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


class GateStatus(str, Enum):
    """Outcome of a deterministic gate check."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class GateResult:
    """The signed result of a deterministic gate check.

    Attributes:
        gate_name:    Name of the gate that produced this result.
        status:       Whether the gate passed, failed, or was skipped.
        duration_ms:  Wall-clock time taken to execute the gate.
        details:      Human-readable description of the outcome.
        artifact_hash: SHA-256 of the canonical gate output — content-addresses
                       the result for later audit verification.
        timestamp:    POSIX timestamp of when the gate ran.
    """

    gate_name: str
    status: GateStatus
    duration_ms: float
    details: str
    artifact_hash: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dictionary."""
        return {
            "gate_name": self.gate_name,
            "status": self.status.value,
            "duration_ms": self.duration_ms,
            "details": self.details,
            "artifact_hash": self.artifact_hash,
            "timestamp": self.timestamp,
        }


class DeterministicGate:
    """A single deterministic gate that content-addresses its output.

    A gate wraps a ``check_fn`` that returns ``(passed: bool, details: str)``.
    The gate captures the outcome in a :class:`GateResult` with an
    ``artifact_hash`` so every run is independently auditable.

    Gates are the *always right* layer — there is no context under which a
    failing lint check is acceptable to ignore.

    Args:
        name:     Unique name for this gate (e.g. ``"lint"``, ``"compile"``).
        check_fn: Callable that returns ``(passed, details)`` given ``**kwargs``.
    """

    def __init__(
        self,
        name: str,
        check_fn: Callable[..., Tuple[bool, str]],
    ) -> None:
        self.name = name
        self._check_fn = check_fn

    def run(self, **kwargs: Any) -> GateResult:
        """Execute the gate check and return a :class:`GateResult`.

        Exceptions raised by ``check_fn`` are caught and recorded as failures
        so the pipeline always receives a result, never an uncaught exception.
        """
        start = time.monotonic()
        try:
            passed, details = self._check_fn(**kwargs)
            status = GateStatus.PASSED if passed else GateStatus.FAILED
        except Exception as exc:  # noqa: BLE001
            passed = False
            details = f"Gate raised an exception: {exc}"
            status = GateStatus.FAILED

        duration_ms = (time.monotonic() - start) * 1000

        # Content-address the result so it can be independently verified.
        # Timing (duration_ms) is intentionally excluded from the hash so that
        # two runs of the same gate with the same outcome produce the same hash.
        canonical = json.dumps(
            {
                "gate": self.name,
                "status": status.value,
                "details": details,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        artifact_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        return GateResult(
            gate_name=self.name,
            status=status,
            duration_ms=duration_ms,
            details=details,
            artifact_hash=artifact_hash,
        )


class DeterministicLayer:
    """A collection of :class:`DeterministicGate` objects that forms the CI trust anchor.

    All registered gates are run in order.  Gates do **not** short-circuit on
    failure — the entire layer runs to completion to give a full picture of the
    current state.

    This layer must always execute before any agent-layer decisions.  It is the
    foundation of the ACI/ACD hybrid model.
    """

    def __init__(self) -> None:
        self._gates: List[DeterministicGate] = []

    def register(self, gate: DeterministicGate) -> "DeterministicLayer":
        """Register a gate with this layer.

        Returns *self* for method chaining.
        """
        self._gates.append(gate)
        return self

    @property
    def gate_names(self) -> List[str]:
        """Names of all registered gates, in registration order."""
        return [g.name for g in self._gates]

    def run_all(self, **kwargs: Any) -> List[GateResult]:
        """Run every gate and return the results.

        All gates run even if earlier ones fail.

        Args:
            **kwargs: Passed through to every gate's ``check_fn``.

        Returns:
            Ordered list of :class:`GateResult` objects.
        """
        return [gate.run(**kwargs) for gate in self._gates]

    @staticmethod
    def all_passed(results: List[GateResult]) -> bool:
        """Return ``True`` if every result has :attr:`GateStatus.PASSED`."""
        return all(r.status == GateStatus.PASSED for r in results)

    @staticmethod
    def failed_gates(results: List[GateResult]) -> List[str]:
        """Return the names of all gates that did not pass."""
        return [r.gate_name for r in results if r.status != GateStatus.PASSED]

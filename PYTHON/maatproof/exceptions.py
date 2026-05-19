"""Custom exceptions for the MaatProof ACI/ACD pipeline.

Migration Note (Addresses EDGE-045):
  ``HumanApprovalRequiredError`` is preserved for backward compatibility with
  non-ADA code paths.  ADA-managed deployments raise
  ``AutonomousDeploymentBlockedError`` instead.

  When updating catch blocks:
    Old:  except HumanApprovalRequiredError as e: ...
    New:  except (HumanApprovalRequiredError, AutonomousDeploymentBlockedError) as e: ...
"""

from __future__ import annotations
from typing import Any, Dict, Optional


class MaatProofError(Exception):
    """Base exception for all MaatProof errors."""


class ProofVerificationError(MaatProofError):
    """Raised when a reasoning proof fails integrity or authenticity checks."""


class GateFailureError(MaatProofError):
    """Raised when a deterministic gate fails and blocks the pipeline."""


class MaxRetriesExceededError(MaatProofError):
    """Raised when the maximum number of agent fix retries is exceeded."""


class HumanApprovalRequiredError(MaatProofError):
    """Raised when a production deployment is attempted without human approval.

    Deprecated for ADA-managed deployments: use AutonomousDeploymentBlockedError.
    This class is retained for non-ADA code paths and backward compatibility.
    """


class AutonomousDeploymentBlockedError(MaatProofError):
    """Raised when the ADA system cannot authorise an autonomous deployment.

    Replaces HumanApprovalRequiredError for ADA-managed deployments.

    This exception is automatically recorded in the immutable audit trail
    (CONSTITUTION §7) by the ADA orchestrator before raising.

    Compliance classification (docs/07-regulatory-compliance.md §ADA Compliance Tiers):
      - SOC 2 Type II: CC6.1 (Logical Access Controls)
      - HIPAA: §164.312(a)(2)(i) (Access control)
      - SOX: ITGC IT-CC-03 (Change Management)
      - EU AI Act: Art. 14 (Human oversight)

    Addresses EDGE-045, EDGE-061, EDGE-073.
    """

    def __init__(
        self,
        reason: str,
        authority_level: Optional[str] = None,
        deployment_score: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        self.reason           = reason
        self.authority_level  = authority_level
        self.deployment_score = deployment_score
        self.trace_id         = trace_id
        super().__init__(
            f"Autonomous deployment blocked: {reason}"
            + (f" (authority_level={authority_level})" if authority_level else "")
            + (f" [trace={trace_id}]" if trace_id else "")
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for audit log and JSON API responses."""
        return {
            "error":            "AutonomousDeploymentBlockedError",
            "reason":           self.reason,
            "authority_level":  self.authority_level,
            "deployment_score": self.deployment_score,
            "trace_id":         self.trace_id,
        }


class RollbackProofKeyError(MaatProofError):
    """Raised when the HMAC secret key for RollbackProof signing is missing or empty.

    Addresses EDGE-040, EDGE-072.
    """

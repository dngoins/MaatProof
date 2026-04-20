"""Custom exceptions for the MaatProof ACI/ACD pipeline."""


class MaatProofError(Exception):
    """Base exception for all MaatProof errors."""


class ProofVerificationError(MaatProofError):
    """Raised when a reasoning proof fails integrity or authenticity checks."""


class GateFailureError(MaatProofError):
    """Raised when a deterministic gate fails and blocks the pipeline."""


class MaxRetriesExceededError(MaatProofError):
    """Raised when the maximum number of agent fix retries is exceeded."""


class HumanApprovalRequiredError(MaatProofError):
    """Raised when a production deployment is attempted without human approval."""

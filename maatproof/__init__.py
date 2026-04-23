"""MaatProof — Cryptographically verifiable, deterministic LLM reasoning
for the ACI/ACD pipeline.

    *"The day LLMs have cryptographically verifiable, deterministic reasoning
    is the day you can drop the pipeline entirely."*

Public API::

    from maatproof import (
        ReasoningStep, ReasoningProof, ProofBuilder, ProofVerifier,
        ReasoningChain,
        DeterministicLayer, DeterministicGate, GateResult, GateStatus,
        AgentLayer, AgentGate, AgentResult, AgentDecision,
        OrchestratingAgent, PipelineEvent,
        ACIPipeline, ACDPipeline, PipelineConfig,
        # Canonical schema models (maatproof.models)
        AppendOnlyAuditLog,
    )
"""

from .chain import ReasoningChain
from .exceptions import (
    GateFailureError,
    HumanApprovalRequiredError,
    MaatProofError,
    MaxRetriesExceededError,
    ProofVerificationError,
)
from .layers.agent import AgentDecision, AgentGate, AgentLayer, AgentResult
from .layers.deterministic import (
    DeterministicGate,
    DeterministicLayer,
    GateResult,
    GateStatus,
)
from .models import AppendOnlyAuditLog
from .orchestrator import AuditEntry, OrchestratingAgent, PipelineEvent
from .pipeline import ACDPipeline, ACIPipeline, PipelineConfig
from .proof import ProofBuilder, ProofVerifier, ReasoningProof, ReasoningStep

__version__ = "0.1.0"

__all__ = [
    # Proof primitives
    "ReasoningStep",
    "ReasoningProof",
    "ProofBuilder",
    "ProofVerifier",
    # Chain builder
    "ReasoningChain",
    # Deterministic layer
    "DeterministicLayer",
    "DeterministicGate",
    "GateResult",
    "GateStatus",
    # Agent layer
    "AgentLayer",
    "AgentGate",
    "AgentResult",
    "AgentDecision",
    # Orchestrator
    "OrchestratingAgent",
    "PipelineEvent",
    "AuditEntry",
    # Pipelines
    "ACIPipeline",
    "ACDPipeline",
    "PipelineConfig",
    # Canonical schema models
    "AppendOnlyAuditLog",
    # Exceptions
    "MaatProofError",
    "ProofVerificationError",
    "GateFailureError",
    "MaxRetriesExceededError",
    "HumanApprovalRequiredError",
]

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
    )
"""

from .chain import ReasoningChain
from .avm import AgentAction, DeploymentTrace, ToolTrace
from .certificate import (
    AcceptanceFailure,
    AcceptanceReport,
    CertificateChecker,
    CertificateDigest,
    DeploymentCertificate,
    DeploymentRequest,
)
from .evidence import EvidenceAuthReport, EvidenceBundle, EvidenceObject, signed_evidence
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
from .ledger import JsonlDeploymentLedger, LedgerEntry
from .orchestrator import AuditEntry, OrchestratingAgent, PipelineEvent
from .pipeline import ACDPipeline, ACIPipeline, PipelineConfig
from .pod import (
    FinalityReport,
    QuorumRule,
    ValidatorAttestation,
    quorum_finality,
    simulate_validators,
)
from .policy import DeploymentPolicy, PolicyCheckReport, PolicyPredicate
from .proof import ProofBuilder, ProofVerifier, ReasoningProof, ReasoningStep
from .vrp import ProofDerivation, ProofStep, VrpCheckReport, check_derivation

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
    # Proof-of-Deploy certificate core
    "AcceptanceFailure",
    "AcceptanceReport",
    "CertificateChecker",
    "CertificateDigest",
    "DeploymentCertificate",
    "DeploymentRequest",
    # Policy and evidence
    "DeploymentPolicy",
    "PolicyCheckReport",
    "PolicyPredicate",
    "EvidenceAuthReport",
    "EvidenceBundle",
    "EvidenceObject",
    "signed_evidence",
    # VRP and finality
    "ProofDerivation",
    "ProofStep",
    "VrpCheckReport",
    "check_derivation",
    "FinalityReport",
    "QuorumRule",
    "ValidatorAttestation",
    "quorum_finality",
    "simulate_validators",
    "JsonlDeploymentLedger",
    "LedgerEntry",
    # AVM boundary
    "AgentAction",
    "DeploymentTrace",
    "ToolTrace",
    # Exceptions
    "MaatProofError",
    "ProofVerificationError",
    "GateFailureError",
    "MaxRetriesExceededError",
    "HumanApprovalRequiredError",
]

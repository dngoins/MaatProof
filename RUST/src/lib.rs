//! Rust reference implementation of the MaatProof proof-carrying deployment model.
//!
//! The crate mirrors the Python reference implementation now stored in `PYTHON/`.
//! Public modules are organized by the same concerns as the Python package.

mod core;

pub mod canonical;
pub mod certificate;
pub mod evidence;
pub mod ledger;
pub mod avm;
pub mod policy;
pub mod pod;
pub mod proof;
pub mod chain;
pub mod vrp;
pub mod orchestrator;
pub mod pipeline;
pub mod layers;

pub use avm::{AgentAction, DeploymentTrace, ToolTrace};
pub use canonical::{canonical_dumps, canonical_hash, hmac_sign, hmac_verify};
pub use certificate::{
    AcceptanceFailure, AcceptanceReport, CertificateChecker, CertificateDigest,
    DeploymentCertificate, DeploymentRequest,
};
pub use chain::ReasoningChain;
pub use evidence::{signed_evidence, EvidenceAuthReport, EvidenceBundle, EvidenceObject};
pub use layers::agent::{AgentDecision, AgentGate, AgentLayer, AgentResult};
pub use layers::deterministic::{
    DeterministicGate, DeterministicLayer, GateResult, GateStatus,
};
pub use ledger::{JsonlDeploymentLedger, LedgerEntry};
pub use orchestrator::{AuditEntry, OrchestratingAgent, PipelineEvent};
pub use pipeline::{ACDPipeline, ACIPipeline, DeploymentDecision, PipelineConfig};
pub use pod::{
    quorum_finality, simulate_validators, FinalityReport, QuorumRule, ValidatorAttestation,
};
pub use policy::{DeploymentPolicy, PolicyCheckReport, PolicyPredicate};
pub use proof::{ProofBuilder, ProofVerifier, ReasoningProof, ReasoningStep};
pub use vrp::{check_derivation, ProofDerivation, ProofStep, VrpCheckReport};

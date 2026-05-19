"""Deployment certificate model and top-level ``Accept(C)`` checker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .canonical import canonical_hash
from .evidence import EvidenceBundle
from .pod import FinalityReport, QuorumRule, ValidatorAttestation, quorum_finality
from .policy import DeploymentPolicy
from .vrp import ProofDerivation, check_derivation


@dataclass(frozen=True)
class DeploymentRequest:
    """The exact deployment target authorized by a certificate."""

    deployment_id: str
    service: str
    environment: str
    commit_sha: str
    artifact_hash: str
    requested_by: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "deployment_id": self.deployment_id,
            "service": self.service,
            "environment": self.environment,
            "commit_sha": self.commit_sha,
            "artifact_hash": self.artifact_hash,
            "requested_by": self.requested_by,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "DeploymentRequest":
        return cls(**data)


@dataclass(frozen=True)
class CertificateDigest:
    """Named digest values for certificate sub-roots."""

    certificate: str
    policy: str
    evidence: str
    proof: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "certificate": self.certificate,
            "policy": self.policy,
            "evidence": self.evidence,
            "proof": self.proof,
        }


@dataclass
class AcceptanceFailure:
    """Structured certificate rejection reason."""

    component: str
    code: str
    message: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "component": self.component,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class AcceptanceReport:
    """Structured result for ``Accept(C)``."""

    accepted: bool
    certificate_digest: str
    wf_policy: bool
    auth_evidence: bool
    check_proof: bool
    quorum: bool
    failures: List[AcceptanceFailure] = field(default_factory=list)
    evidence_root: str = ""
    proof_root: str = ""
    policy_root: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "accepted": self.accepted,
            "certificate_digest": self.certificate_digest,
            "wf_policy": self.wf_policy,
            "auth_evidence": self.auth_evidence,
            "check_proof": self.check_proof,
            "quorum": self.quorum,
            "evidence_root": self.evidence_root,
            "proof_root": self.proof_root,
            "policy_root": self.policy_root,
            "failures": [failure.to_dict() for failure in self.failures],
        }


@dataclass
class DeploymentCertificate:
    """Proof-carrying deployment certificate ``C = <P, E, pi, A>``."""

    request: DeploymentRequest
    policy: DeploymentPolicy
    evidence: EvidenceBundle
    proof: ProofDerivation
    attestations: List[ValidatorAttestation] = field(default_factory=list)

    def body(self) -> Dict[str, object]:
        return {
            "request": self.request.to_dict(),
            "policy": self.policy.to_dict(),
            "evidence": self.evidence.to_dict(),
            "proof": self.proof.to_dict(),
        }

    def to_dict(self) -> Dict[str, object]:
        return {
            **self.body(),
            "attestations": [att.to_dict() for att in self.attestations],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "DeploymentCertificate":
        return cls(
            request=DeploymentRequest.from_dict(data["request"]),  # type: ignore[arg-type]
            policy=DeploymentPolicy.from_dict(data["policy"]),  # type: ignore[arg-type]
            evidence=EvidenceBundle.from_dict(data["evidence"]),  # type: ignore[arg-type]
            proof=ProofDerivation.from_dict(data["proof"]),  # type: ignore[arg-type]
            attestations=[
                ValidatorAttestation.from_dict(att)
                for att in data.get("attestations", [])  # type: ignore[union-attr]
            ],
        )

    def digest(self) -> str:
        """Digest signed by validators; attestations are deliberately excluded."""
        return canonical_hash(self.body())

    def digests(self) -> CertificateDigest:
        return CertificateDigest(
            certificate=self.digest(),
            policy=canonical_hash(self.policy.to_dict()),
            evidence=self.evidence.root(),
            proof=canonical_hash(self.proof.to_dict()),
        )


class CertificateChecker:
    """Top-level checker for ``WF(P) && Auth(E) && CheckR(pi,P,E) && Quorum(A)``."""

    def __init__(
        self,
        evidence_secret_key: bytes,
        validator_secret_keys: Optional[Dict[str, bytes]] = None,
        quorum_rule: Optional[QuorumRule] = None,
        now: Optional[float] = None,
    ) -> None:
        self.evidence_secret_key = evidence_secret_key
        self.validator_secret_keys = validator_secret_keys or {}
        self.quorum_rule = quorum_rule or QuorumRule.majority()
        self.now = now

    def accept(self, certificate: DeploymentCertificate) -> AcceptanceReport:
        return self._accept(certificate, include_quorum=True)

    def accept_without_quorum(
        self, certificate: DeploymentCertificate
    ) -> AcceptanceReport:
        return self._accept(certificate, include_quorum=False)

    def _accept(
        self,
        certificate: DeploymentCertificate,
        include_quorum: bool,
    ) -> AcceptanceReport:
        failures: List[AcceptanceFailure] = []
        digests = certificate.digests()

        policy_report = certificate.policy.well_formed()
        failures.extend(
            AcceptanceFailure("WF(P)", code) for code in policy_report.failures
        )

        evidence_report = certificate.evidence.authenticate(
            certificate.policy,
            certificate.request,
            self.evidence_secret_key,
            now=self.now,
        )
        failures.extend(
            AcceptanceFailure("Auth(E)", code)
            for code in evidence_report.failures
        )

        proof_report = check_derivation(
            certificate.proof,
            certificate.policy,
            certificate.evidence,
            certificate.request,
        )
        failures.extend(
            AcceptanceFailure("CheckR", code) for code in proof_report.failures
        )

        finality = FinalityReport(finalized=True)
        if include_quorum:
            finality = quorum_finality(
                certificate.attestations,
                digests.certificate,
                self.validator_secret_keys,
                self.quorum_rule,
            )
            failures.extend(
                AcceptanceFailure("Quorum(A)", code)
                for code in finality.failures
            )

        wf_policy = policy_report.well_formed
        auth_evidence = evidence_report.authenticated
        check_proof = proof_report.valid
        quorum = finality.finalized
        return AcceptanceReport(
            accepted=wf_policy and auth_evidence and check_proof and quorum,
            certificate_digest=digests.certificate,
            wf_policy=wf_policy,
            auth_evidence=auth_evidence,
            check_proof=check_proof,
            quorum=quorum,
            failures=failures,
            evidence_root=digests.evidence,
            proof_root=digests.proof,
            policy_root=digests.policy,
        )

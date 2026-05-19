"""Proof-of-Deploy validator attestations and quorum finality."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .canonical import hmac_sign, hmac_verify


@dataclass
class ValidatorAttestation:
    """Signed validator decision over a canonical certificate digest."""

    validator_id: str
    decision: str
    certificate_digest: str
    timestamp: float
    stake: float = 1.0
    reason: str = ""
    signature: str = ""

    def payload(self) -> Dict[str, Any]:
        return {
            "validator_id": self.validator_id,
            "decision": self.decision,
            "certificate_digest": self.certificate_digest,
            "timestamp": self.timestamp,
            "stake": self.stake,
            "reason": self.reason,
        }

    def sign(self, secret_key: bytes) -> "ValidatorAttestation":
        self.signature = hmac_sign(secret_key, self.payload())
        return self

    def verify(self, secret_key: bytes) -> bool:
        return hmac_verify(secret_key, self.payload(), self.signature)

    def to_dict(self) -> Dict[str, Any]:
        return {**self.payload(), "signature": self.signature}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ValidatorAttestation":
        return cls(
            validator_id=data["validator_id"],
            decision=data["decision"],
            certificate_digest=data["certificate_digest"],
            timestamp=float(data["timestamp"]),
            stake=float(data.get("stake", 1.0)),
            reason=data.get("reason", ""),
            signature=data.get("signature", ""),
        )


@dataclass(frozen=True)
class QuorumRule:
    """Parameterized finality rule for local validator replay."""

    kind: str = "majority"
    threshold: Optional[float] = None

    @classmethod
    def majority(cls) -> "QuorumRule":
        return cls(kind="majority")

    @classmethod
    def supermajority(cls, fraction: float = 2 / 3) -> "QuorumRule":
        return cls(kind="supermajority", threshold=fraction)

    @classmethod
    def weighted_threshold(cls, weight: float) -> "QuorumRule":
        return cls(kind="weighted_threshold", threshold=weight)

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "threshold": self.threshold}


@dataclass
class FinalityReport:
    """Result of evaluating ``Quorum(A)``."""

    finalized: bool
    failures: List[str] = field(default_factory=list)
    accepted_weight: float = 0.0
    total_weight: float = 0.0
    accepted_validators: List[str] = field(default_factory=list)
    disputed_validators: List[str] = field(default_factory=list)


def quorum_finality(
    attestations: Iterable[ValidatorAttestation],
    certificate_digest: str,
    validator_secret_keys: Dict[str, bytes],
    rule: QuorumRule,
) -> FinalityReport:
    failures: List[str] = []
    accepted_weight = 0.0
    total_weight = 0.0
    accepted_validators: List[str] = []
    disputed_validators: List[str] = []
    seen = set()

    for attestation in attestations:
        if attestation.validator_id in seen:
            failures.append(f"QUORUM_DUPLICATE_VALIDATOR:{attestation.validator_id}")
            continue
        seen.add(attestation.validator_id)

        if attestation.certificate_digest != certificate_digest:
            failures.append(f"QUORUM_DIGEST_MISMATCH:{attestation.validator_id}")
            continue
        secret_key = validator_secret_keys.get(attestation.validator_id)
        if secret_key is None:
            failures.append(f"QUORUM_VALIDATOR_UNKNOWN:{attestation.validator_id}")
            continue
        if not attestation.signature or not attestation.verify(secret_key):
            failures.append(f"QUORUM_SIGNATURE_INVALID:{attestation.validator_id}")
            continue
        if attestation.decision not in {"accept", "reject", "dispute"}:
            failures.append(f"QUORUM_DECISION_INVALID:{attestation.validator_id}")
            continue

        total_weight += attestation.stake
        if attestation.decision == "accept":
            accepted_weight += attestation.stake
            accepted_validators.append(attestation.validator_id)
        elif attestation.decision == "dispute":
            disputed_validators.append(attestation.validator_id)

    if total_weight <= 0:
        failures.append("QUORUM_NO_VALID_ATTESTATIONS")

    threshold = _required_weight(rule, total_weight)
    if accepted_weight < threshold:
        failures.append("QUORUM_INSUFFICIENT_ACCEPT_WEIGHT")

    return FinalityReport(
        finalized=not failures,
        failures=failures,
        accepted_weight=accepted_weight,
        total_weight=total_weight,
        accepted_validators=accepted_validators,
        disputed_validators=disputed_validators,
    )


def simulate_validators(
    certificate: Any,
    checker: Any,
    validator_secret_keys: Dict[str, bytes],
    timestamp: float,
    stakes: Optional[Dict[str, float]] = None,
) -> List[ValidatorAttestation]:
    """Replay a certificate locally and sign validator decisions."""
    digest = certificate.digest()
    attestations: List[ValidatorAttestation] = []
    for validator_id, secret_key in sorted(validator_secret_keys.items()):
        report = checker.accept_without_quorum(certificate)
        decision = "accept" if report.accepted else "reject"
        reason = "" if report.accepted else ",".join(f.code for f in report.failures)
        attestations.append(
            ValidatorAttestation(
                validator_id=validator_id,
                decision=decision,
                certificate_digest=digest,
                timestamp=timestamp,
                stake=(stakes or {}).get(validator_id, 1.0),
                reason=reason,
            ).sign(secret_key)
        )
    return attestations


def _required_weight(rule: QuorumRule, total_weight: float) -> float:
    if rule.kind == "majority":
        return (total_weight / 2) + 1e-12
    if rule.kind == "supermajority":
        return total_weight * float(rule.threshold or (2 / 3))
    if rule.kind == "weighted_threshold":
        return float(rule.threshold or 0.0)
    raise ValueError(f"unsupported quorum rule: {rule.kind}")

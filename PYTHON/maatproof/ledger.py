"""Append-only JSONL ledger for finalized deployment certificates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .certificate import AcceptanceReport, CertificateChecker, DeploymentCertificate
from .canonical import canonical_dumps


@dataclass(frozen=True)
class LedgerEntry:
    """A finalized certificate record stored in JSONL."""

    certificate_digest: str
    policy_hash: str
    evidence_root: str
    proof_root: str
    finality_result: Dict[str, Any]
    environment: str
    timestamp: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "certificate_digest": self.certificate_digest,
            "policy_hash": self.policy_hash,
            "evidence_root": self.evidence_root,
            "proof_root": self.proof_root,
            "finality_result": self.finality_result,
            "environment": self.environment,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LedgerEntry":
        return cls(
            certificate_digest=data["certificate_digest"],
            policy_hash=data["policy_hash"],
            evidence_root=data["evidence_root"],
            proof_root=data["proof_root"],
            finality_result=dict(data["finality_result"]),
            environment=data["environment"],
            timestamp=float(data["timestamp"]),
        )


class JsonlDeploymentLedger:
    """Local append-only JSONL ledger suitable for Colab and tests."""

    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)

    def append(
        self,
        certificate: DeploymentCertificate,
        report: AcceptanceReport,
        timestamp: float,
    ) -> LedgerEntry:
        if not report.accepted:
            raise ValueError("only accepted certificates may be appended")
        entry = LedgerEntry(
            certificate_digest=report.certificate_digest,
            policy_hash=report.policy_root,
            evidence_root=report.evidence_root,
            proof_root=report.proof_root,
            finality_result=report.to_dict(),
            environment=certificate.request.environment,
            timestamp=timestamp,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_dumps(entry.to_dict()) + "\n")
        return entry

    def entries(self) -> List[LedgerEntry]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            return [
                LedgerEntry.from_dict(json.loads(line))
                for line in handle
                if line.strip()
            ]

    def find(self, certificate_digest: str) -> Optional[LedgerEntry]:
        for entry in self.entries():
            if entry.certificate_digest == certificate_digest:
                return entry
        return None

    def replay_verify(
        self,
        certificate: DeploymentCertificate,
        checker: CertificateChecker,
    ) -> AcceptanceReport:
        report = checker.accept(certificate)
        entry = self.find(certificate.digest())
        if entry is None:
            report.failures.append(_ledger_failure("LEDGER_ENTRY_MISSING"))
            report.accepted = False
            return report
        if report.accepted and entry.certificate_digest != report.certificate_digest:
            report.failures.append(_ledger_failure("LEDGER_DIGEST_MISMATCH"))
            report.accepted = False
        return report


def _ledger_failure(code: str):
    from .certificate import AcceptanceFailure

    return AcceptanceFailure("Ledger", code)

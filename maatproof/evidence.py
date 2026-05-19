"""Evidence objects, canonical evidence bundles, and ``Auth(E)`` checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .canonical import canonical_hash, hmac_sign, hmac_verify


@dataclass
class EvidenceObject:
    """A signed external fact used by a deployment certificate."""

    evidence_id: str
    evidence_type: str
    value: Dict[str, Any]
    source: str
    timestamp: float
    dependencies: List[str] = field(default_factory=list)
    hash: str = ""
    signature: str = ""

    def payload(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type,
            "value": self.value,
            "source": self.source,
            "timestamp": self.timestamp,
            "dependencies": sorted(self.dependencies),
        }

    def compute_hash(self) -> str:
        return canonical_hash(self.payload())

    def sign(self, secret_key: bytes) -> "EvidenceObject":
        self.hash = self.compute_hash()
        self.signature = hmac_sign(secret_key, self.hash)
        return self

    def verify(self, secret_key: bytes) -> bool:
        return self.hash == self.compute_hash() and hmac_verify(
            secret_key, self.hash, self.signature
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.payload(),
            "hash": self.hash,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceObject":
        return cls(
            evidence_id=data["evidence_id"],
            evidence_type=data["evidence_type"],
            value=dict(data.get("value", {})),
            source=data["source"],
            timestamp=float(data["timestamp"]),
            dependencies=list(data.get("dependencies", [])),
            hash=data.get("hash", ""),
            signature=data.get("signature", ""),
        )


@dataclass
class EvidenceAuthReport:
    """Result of evaluating ``Auth(E)``."""

    authenticated: bool
    failures: List[str] = field(default_factory=list)
    evidence_root: str = ""


@dataclass
class EvidenceBundle:
    """Canonical bundle of signed deployment evidence."""

    objects: List[EvidenceObject]

    def sorted_objects(self) -> List[EvidenceObject]:
        return sorted(self.objects, key=lambda obj: obj.evidence_id)

    def to_dict(self) -> Dict[str, Any]:
        return {"objects": [obj.to_dict() for obj in self.sorted_objects()]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceBundle":
        return cls([EvidenceObject.from_dict(obj) for obj in data.get("objects", [])])

    def root(self) -> str:
        return canonical_hash(self.to_dict())

    def by_id(self) -> Dict[str, EvidenceObject]:
        return {obj.evidence_id: obj for obj in self.objects}

    def of_type(self, evidence_type: str) -> List[EvidenceObject]:
        return [
            obj
            for obj in self.sorted_objects()
            if obj.evidence_type == evidence_type
        ]

    def first_of_type(self, evidence_type: str) -> Optional[EvidenceObject]:
        matches = self.of_type(evidence_type)
        return matches[0] if matches else None

    def authenticate(
        self,
        policy: Any,
        request: Any,
        secret_key: bytes,
        now: Optional[float] = None,
    ) -> EvidenceAuthReport:
        failures: List[str] = []
        objects_by_id: Dict[str, EvidenceObject] = {}

        for obj in self.objects:
            if obj.evidence_id in objects_by_id:
                failures.append(f"EVIDENCE_DUPLICATE_ID:{obj.evidence_id}")
            objects_by_id[obj.evidence_id] = obj
            if not obj.hash:
                failures.append(f"EVIDENCE_HASH_MISSING:{obj.evidence_id}")
            elif obj.hash != obj.compute_hash():
                failures.append(f"EVIDENCE_HASH_INVALID:{obj.evidence_id}")
            if not obj.signature:
                failures.append(f"EVIDENCE_SIGNATURE_MISSING:{obj.evidence_id}")
            elif obj.hash == obj.compute_hash() and not obj.verify(secret_key):
                failures.append(f"EVIDENCE_SIGNATURE_INVALID:{obj.evidence_id}")

        for obj in self.objects:
            for dependency in obj.dependencies:
                if dependency not in objects_by_id:
                    failures.append(
                        f"EVIDENCE_DEPENDENCY_MISSING:{obj.evidence_id}:{dependency}"
                    )

        required_types = policy.required_evidence_types()
        for evidence_type in required_types:
            if not self.of_type(evidence_type):
                failures.append(f"EVIDENCE_REQUIRED_MISSING:{evidence_type}")

        if now is not None:
            for evidence_type, max_age in policy.freshness_seconds.items():
                for obj in self.of_type(evidence_type):
                    if now - obj.timestamp > max_age:
                        failures.append(f"EVIDENCE_STALE:{obj.evidence_id}")

        failures.extend(_check_request_bindings(self, request))
        failures.extend(_check_policy_predicates(self, policy, request))

        return EvidenceAuthReport(
            authenticated=not failures,
            failures=failures,
            evidence_root=self.root(),
        )


def signed_evidence(
    evidence_id: str,
    evidence_type: str,
    value: Dict[str, Any],
    source: str,
    timestamp: float,
    secret_key: bytes,
    dependencies: Optional[Iterable[str]] = None,
) -> EvidenceObject:
    """Build and sign an evidence object for examples and tests."""
    obj = EvidenceObject(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        value=value,
        source=source,
        timestamp=timestamp,
        dependencies=list(dependencies or []),
    )
    return obj.sign(secret_key)


def _check_request_bindings(bundle: EvidenceBundle, request: Any) -> List[str]:
    failures: List[str] = []
    commit = bundle.first_of_type("commit_snapshot")
    if commit and commit.value.get("commit_sha") != request.commit_sha:
        failures.append("EVIDENCE_COMMIT_MISMATCH")

    artifact = bundle.first_of_type("build_artifact")
    if artifact and artifact.value.get("artifact_hash") != request.artifact_hash:
        failures.append("EVIDENCE_ARTIFACT_MISMATCH")

    env = bundle.first_of_type("environment_descriptor")
    if env and env.value.get("environment") != request.environment:
        failures.append("EVIDENCE_ENVIRONMENT_MISMATCH")

    for obj in bundle.sorted_objects():
        deployment_id = obj.value.get("deployment_id")
        if deployment_id is not None and deployment_id != request.deployment_id:
            failures.append(f"EVIDENCE_DEPLOYMENT_MISMATCH:{obj.evidence_id}")
    return failures


def _check_policy_predicates(
    bundle: EvidenceBundle, policy: Any, request: Any
) -> List[str]:
    failures: List[str] = []
    for predicate in policy.all_predicates():
        name = predicate.name
        params = predicate.params
        if name in {"and", "or", "not"}:
            continue
        if name == "test_passed" and not _has_test_passed(bundle, params["suite"]):
            failures.append(f"EVIDENCE_TEST_NOT_PASSED:{params['suite']}")
        elif name == "vuln_count" and not _vuln_count_ok(bundle, params):
            failures.append(
                f"EVIDENCE_VULN_THRESHOLD_EXCEEDED:{params['severity']}"
            )
        elif name == "human_attested" and not _has_human_attestation(
            bundle, params["role"], request.deployment_id
        ):
            failures.append(f"EVIDENCE_HUMAN_ATTESTATION_MISSING:{params['role']}")
        elif name == "rollback_defined" and not _has_service_value(
            bundle, "rollback_spec", params["service"], "rollback_plan"
        ):
            failures.append(f"EVIDENCE_ROLLBACK_MISSING:{params['service']}")
        elif name == "canary_enabled" and not _has_canary(bundle, params["service"]):
            failures.append(f"EVIDENCE_CANARY_MISSING:{params['service']}")
        elif name == "environment_matches":
            target = params["target"]
            if target != policy.environment or target != request.environment:
                failures.append(f"EVIDENCE_ENVIRONMENT_TARGET_MISMATCH:{target}")
    return failures


def _has_test_passed(bundle: EvidenceBundle, suite: str) -> bool:
    return any(
        obj.value.get("suite") == suite and obj.value.get("passed") is True
        for obj in bundle.of_type("test_result")
    )


def _vuln_count_ok(bundle: EvidenceBundle, params: Dict[str, Any]) -> bool:
    severity = params["severity"]
    threshold = params["threshold"]
    operator = params["operator"]
    for obj in bundle.of_type("scan_report"):
        counts = obj.value.get("vulnerabilities", {})
        count = int(counts.get(severity, 0))
        if operator == "<=" and count <= threshold:
            return True
        if operator == "==" and count == threshold:
            return True
    return False


def _has_human_attestation(
    bundle: EvidenceBundle, role: str, deployment_id: str
) -> bool:
    return any(
        obj.value.get("role") == role
        and obj.value.get("deployment_id") == deployment_id
        and obj.value.get("approved") is True
        for obj in bundle.of_type("human_attestation")
    )


def _has_service_value(
    bundle: EvidenceBundle, evidence_type: str, service: str, value_key: str
) -> bool:
    return any(
        obj.value.get("service") == service and bool(obj.value.get(value_key))
        for obj in bundle.of_type(evidence_type)
    )


def _has_canary(bundle: EvidenceBundle, service: str) -> bool:
    return any(
        obj.value.get("service") == service
        and bool(obj.value.get("canary_enabled"))
        for obj in bundle.of_type("rollout_spec")
    )

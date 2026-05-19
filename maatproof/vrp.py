"""Verifiable Reasoning Protocol derivations for Proof-of-Deploy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

from .evidence import EvidenceBundle


VALID_RULES = {
    "TEST_PASS",
    "VULN_OK",
    "SIGNATURE_VALID",
    "EVIDENCE_BOUND",
    "ENVIRONMENT_MATCH",
    "ROLLBACK_READY",
    "HUMAN_ATTESTED",
    "POLICY_SATISFIED",
    "DEPLOY_AUTH",
}


@dataclass
class ProofStep:
    """A typed admissible proof step in the derivation ``pi``."""

    step_id: str
    rule: str
    conclusion: str
    evidence_refs: List[str] = field(default_factory=list)
    premises: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "rule": self.rule,
            "conclusion": self.conclusion,
            "evidence_refs": sorted(self.evidence_refs),
            "premises": list(self.premises),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProofStep":
        return cls(
            step_id=data["step_id"],
            rule=data["rule"],
            conclusion=data["conclusion"],
            evidence_refs=list(data.get("evidence_refs", [])),
            premises=list(data.get("premises", [])),
        )


@dataclass
class ProofDerivation:
    """Machine-checkable derivation used by ``CheckR(pi, P, E)``."""

    steps: List[ProofStep]
    final_conclusion: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": [step.to_dict() for step in self.steps],
            "final_conclusion": self.final_conclusion,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProofDerivation":
        return cls(
            steps=[ProofStep.from_dict(step) for step in data.get("steps", [])],
            final_conclusion=data["final_conclusion"],
        )


@dataclass
class VrpCheckReport:
    """Result of evaluating ``CheckR(pi, P, E)``."""

    valid: bool
    failures: List[str] = field(default_factory=list)
    proof_root: str = ""


def check_derivation(
    derivation: ProofDerivation,
    policy: Any,
    evidence: EvidenceBundle,
    request: Any,
) -> VrpCheckReport:
    failures: List[str] = []
    known_steps: Set[str] = set()
    evidence_by_id = evidence.by_id()

    if not derivation.steps:
        failures.append("VRP_STEPS_REQUIRED")

    for step in derivation.steps:
        if not step.step_id:
            failures.append("VRP_STEP_ID_REQUIRED")
        if step.step_id in known_steps:
            failures.append(f"VRP_STEP_DUPLICATE:{step.step_id}")
        known_steps.add(step.step_id)
        if step.rule not in VALID_RULES:
            failures.append(f"VRP_RULE_UNKNOWN:{step.rule}")
        for premise in step.premises:
            if premise not in known_steps:
                failures.append(f"VRP_PREMISE_MISSING:{step.step_id}:{premise}")
        for evidence_ref in step.evidence_refs:
            if evidence_ref not in evidence_by_id:
                failures.append(
                    f"VRP_EVIDENCE_REF_MISSING:{step.step_id}:{evidence_ref}"
                )
        failures.extend(_check_rule_semantics(step, policy, evidence, request))

    expected_final = f"deploy_authorized:{request.deployment_id}"
    if derivation.final_conclusion != expected_final:
        failures.append("VRP_FINAL_CONCLUSION_MISMATCH")
    if not derivation.steps or derivation.steps[-1].conclusion != expected_final:
        failures.append("VRP_FINAL_STEP_MISMATCH")

    from .canonical import canonical_hash

    return VrpCheckReport(
        valid=not failures,
        failures=failures,
        proof_root=canonical_hash(derivation.to_dict()),
    )


def _check_rule_semantics(
    step: ProofStep,
    policy: Any,
    evidence: EvidenceBundle,
    request: Any,
) -> List[str]:
    failures: List[str] = []
    refs = [evidence.by_id()[ref] for ref in step.evidence_refs if ref in evidence.by_id()]

    if step.rule == "TEST_PASS":
        if not any(obj.evidence_type == "test_result" and obj.value.get("passed") for obj in refs):
            failures.append(f"VRP_TEST_PASS_UNSUPPORTED:{step.step_id}")
    elif step.rule == "VULN_OK":
        if not any(obj.evidence_type == "scan_report" for obj in refs):
            failures.append(f"VRP_VULN_OK_UNSUPPORTED:{step.step_id}")
    elif step.rule == "SIGNATURE_VALID":
        if not refs:
            failures.append(f"VRP_SIGNATURE_REF_REQUIRED:{step.step_id}")
    elif step.rule == "EVIDENCE_BOUND":
        if not refs:
            failures.append(f"VRP_EVIDENCE_BOUND_REF_REQUIRED:{step.step_id}")
        for obj in refs:
            deployment_id = obj.value.get("deployment_id")
            if deployment_id is not None and deployment_id != request.deployment_id:
                failures.append(f"VRP_EVIDENCE_NOT_BOUND:{step.step_id}")
    elif step.rule == "ENVIRONMENT_MATCH":
        if policy.environment != request.environment:
            failures.append(f"VRP_ENVIRONMENT_POLICY_MISMATCH:{step.step_id}")
        if not any(
            obj.evidence_type == "environment_descriptor"
            and obj.value.get("environment") == request.environment
            for obj in refs
        ):
            failures.append(f"VRP_ENVIRONMENT_REF_MISSING:{step.step_id}")
    elif step.rule == "ROLLBACK_READY":
        if not any(obj.evidence_type == "rollback_spec" for obj in refs):
            failures.append(f"VRP_ROLLBACK_REF_MISSING:{step.step_id}")
    elif step.rule == "HUMAN_ATTESTED":
        if policy.human_attestation_required() and not any(
            obj.evidence_type == "human_attestation"
            and obj.value.get("approved") is True
            for obj in refs
        ):
            failures.append(f"VRP_HUMAN_ATTESTATION_REF_MISSING:{step.step_id}")
    elif step.rule == "POLICY_SATISFIED":
        if not step.premises:
            failures.append(f"VRP_POLICY_PREMISES_REQUIRED:{step.step_id}")
        if step.conclusion != "policy_satisfied":
            failures.append(f"VRP_POLICY_CONCLUSION_INVALID:{step.step_id}")
    elif step.rule == "DEPLOY_AUTH":
        if "policy_satisfied" not in step.conclusion and step.conclusion != f"deploy_authorized:{request.deployment_id}":
            failures.append(f"VRP_DEPLOY_AUTH_CONCLUSION_INVALID:{step.step_id}")
        if not step.premises:
            failures.append(f"VRP_DEPLOY_AUTH_PREMISE_REQUIRED:{step.step_id}")
    return failures

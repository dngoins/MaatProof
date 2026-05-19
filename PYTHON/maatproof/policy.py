"""Deployment policy model and well-formedness checks for Proof-of-Deploy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List


VALID_PREDICATES = {
    "test_passed",
    "vuln_count",
    "human_attested",
    "rollback_defined",
    "canary_enabled",
    "environment_matches",
    "and",
    "or",
    "not",
}

DEFAULT_RULE_SET = "pod-v1"


@dataclass(frozen=True)
class PolicyPredicate:
    """A typed deployment-policy predicate."""

    name: str
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "params": self.params}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PolicyPredicate":
        params = dict(data.get("params", {}))
        if data.get("name") in {"and", "or"}:
            params["children"] = [
                cls.from_dict(child) for child in params.get("children", [])
            ]
        elif data.get("name") == "not" and isinstance(params.get("child"), dict):
            params["child"] = cls.from_dict(params["child"])
        return cls(name=data["name"], params=params)


@dataclass
class PolicyCheckReport:
    """Result of evaluating ``WF(P)``."""

    well_formed: bool
    failures: List[str] = field(default_factory=list)


@dataclass
class DeploymentPolicy:
    """Executable deployment contract used as the policy object ``P``."""

    policy_id: str
    version: int
    environment: str
    predicates: List[PolicyPredicate]
    rule_set_id: str = DEFAULT_RULE_SET
    freshness_seconds: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "environment": self.environment,
            "rule_set_id": self.rule_set_id,
            "freshness_seconds": dict(sorted(self.freshness_seconds.items())),
            "predicates": [_predicate_to_dict(p) for p in self.predicates],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeploymentPolicy":
        return cls(
            policy_id=data["policy_id"],
            version=int(data["version"]),
            environment=data["environment"],
            rule_set_id=data.get("rule_set_id", DEFAULT_RULE_SET),
            freshness_seconds=dict(data.get("freshness_seconds", {})),
            predicates=[
                PolicyPredicate.from_dict(p) for p in data.get("predicates", [])
            ],
        )

    def all_predicates(self) -> List[PolicyPredicate]:
        return list(_walk_predicates(self.predicates))

    def required_evidence_types(self) -> List[str]:
        required = {"commit_snapshot", "build_artifact", "environment_descriptor"}
        for predicate in self.all_predicates():
            if predicate.name == "test_passed":
                required.add("test_result")
            elif predicate.name == "vuln_count":
                required.add("scan_report")
            elif predicate.name == "human_attested":
                required.add("human_attestation")
            elif predicate.name == "rollback_defined":
                required.add("rollback_spec")
            elif predicate.name == "canary_enabled":
                required.add("rollout_spec")
            elif predicate.name == "environment_matches":
                required.add("environment_descriptor")
        return sorted(required)

    def human_attestation_required(self) -> bool:
        return any(p.name == "human_attested" for p in self.all_predicates())

    def well_formed(self) -> PolicyCheckReport:
        failures: List[str] = []
        if not self.policy_id:
            failures.append("POLICY_ID_REQUIRED")
        if self.version <= 0:
            failures.append("POLICY_VERSION_INVALID")
        if not self.environment:
            failures.append("POLICY_ENVIRONMENT_REQUIRED")
        if self.rule_set_id != DEFAULT_RULE_SET:
            failures.append("POLICY_RULE_SET_UNSUPPORTED")
        if not self.predicates:
            failures.append("POLICY_PREDICATES_REQUIRED")

        for predicate in self.all_predicates():
            failures.extend(_validate_predicate(predicate))

        environments = [
            p.params.get("target")
            for p in self.all_predicates()
            if p.name == "environment_matches"
        ]
        if environments and self.environment not in environments:
            failures.append("POLICY_ENVIRONMENT_CONTRADICTION")

        for evidence_type, seconds in self.freshness_seconds.items():
            if not isinstance(evidence_type, str) or not evidence_type:
                failures.append("POLICY_FRESHNESS_TYPE_INVALID")
            if not isinstance(seconds, (int, float)) or seconds <= 0:
                failures.append(f"POLICY_FRESHNESS_INVALID:{evidence_type}")

        return PolicyCheckReport(well_formed=not failures, failures=failures)


def _predicate_to_dict(predicate: PolicyPredicate) -> Dict[str, Any]:
    params = {}
    for key, value in predicate.params.items():
        if isinstance(value, PolicyPredicate):
            params[key] = _predicate_to_dict(value)
        elif isinstance(value, list) and value and isinstance(value[0], PolicyPredicate):
            params[key] = [_predicate_to_dict(item) for item in value]
        else:
            params[key] = value
    return {"name": predicate.name, "params": params}


def _walk_predicates(predicates: Iterable[PolicyPredicate]) -> Iterable[PolicyPredicate]:
    for predicate in predicates:
        yield predicate
        if predicate.name in {"and", "or"}:
            yield from _walk_predicates(predicate.params.get("children", []))
        elif predicate.name == "not" and isinstance(
            predicate.params.get("child"), PolicyPredicate
        ):
            yield from _walk_predicates([predicate.params["child"]])


def _validate_predicate(predicate: PolicyPredicate) -> List[str]:
    failures: List[str] = []
    if predicate.name not in VALID_PREDICATES:
        return [f"POLICY_PREDICATE_UNKNOWN:{predicate.name}"]

    params = predicate.params
    if predicate.name == "test_passed":
        if not isinstance(params.get("suite"), str) or not params["suite"]:
            failures.append("POLICY_TEST_SUITE_INVALID")
    elif predicate.name == "vuln_count":
        if not isinstance(params.get("severity"), str) or not params["severity"]:
            failures.append("POLICY_VULN_SEVERITY_INVALID")
        if params.get("operator") not in {"<=", "=="}:
            failures.append("POLICY_VULN_OPERATOR_INVALID")
        if not isinstance(params.get("threshold"), int) or params["threshold"] < 0:
            failures.append("POLICY_VULN_THRESHOLD_INVALID")
    elif predicate.name == "human_attested":
        if not isinstance(params.get("role"), str) or not params["role"]:
            failures.append("POLICY_HUMAN_ROLE_INVALID")
    elif predicate.name in {"rollback_defined", "canary_enabled"}:
        if not isinstance(params.get("service"), str) or not params["service"]:
            failures.append(f"POLICY_{predicate.name.upper()}_SERVICE_INVALID")
    elif predicate.name == "environment_matches":
        if not isinstance(params.get("target"), str) or not params["target"]:
            failures.append("POLICY_ENVIRONMENT_TARGET_INVALID")
    elif predicate.name in {"and", "or"}:
        children = params.get("children")
        if not isinstance(children, list) or not children:
            failures.append(f"POLICY_{predicate.name.upper()}_CHILDREN_REQUIRED")
    elif predicate.name == "not":
        if not isinstance(params.get("child"), PolicyPredicate):
            failures.append("POLICY_NOT_CHILD_REQUIRED")
    return failures

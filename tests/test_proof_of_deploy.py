"""Tests for the Python Proof-of-Deploy reference implementation."""

from pathlib import Path

import pytest

from maatproof.avm import DeploymentTrace, ToolTrace
from maatproof.certificate import (
    CertificateChecker,
    DeploymentCertificate,
    DeploymentRequest,
)
from maatproof.evidence import EvidenceBundle, signed_evidence
from maatproof.ledger import JsonlDeploymentLedger
from maatproof.pod import QuorumRule, ValidatorAttestation, simulate_validators
from maatproof.policy import DeploymentPolicy, PolicyPredicate
from maatproof.vrp import ProofDerivation, ProofStep


EVIDENCE_SECRET = b"evidence-secret"
VALIDATORS = {
    "validator-a": b"validator-a-secret",
    "validator-b": b"validator-b-secret",
    "validator-c": b"validator-c-secret",
}
NOW = 1_700_000_100.0


def _request(environment: str = "production") -> DeploymentRequest:
    return DeploymentRequest(
        deployment_id="deploy-123",
        service="checkout",
        environment=environment,
        commit_sha="abc123",
        artifact_hash="sha256:artifact",
        requested_by="agent:planner",
    )


def _policy(environment: str = "production") -> DeploymentPolicy:
    return DeploymentPolicy(
        policy_id="checkout-prod",
        version=1,
        environment=environment,
        freshness_seconds={"human_attestation": 3600, "scan_report": 3600},
        predicates=[
            PolicyPredicate("test_passed", {"suite": "unit"}),
            PolicyPredicate(
                "vuln_count",
                {"severity": "critical", "operator": "<=", "threshold": 0},
            ),
            PolicyPredicate("environment_matches", {"target": environment}),
            PolicyPredicate("rollback_defined", {"service": "checkout"}),
            PolicyPredicate("human_attested", {"role": "release-manager"}),
        ],
    )


def _evidence(
    request: DeploymentRequest,
    *,
    timestamp: float = NOW,
    include_scan: bool = True,
    human_timestamp: float = NOW,
) -> EvidenceBundle:
    objects = [
        signed_evidence(
            "commit",
            "commit_snapshot",
            {
                "deployment_id": request.deployment_id,
                "commit_sha": request.commit_sha,
            },
            "git",
            timestamp,
            EVIDENCE_SECRET,
        ),
        signed_evidence(
            "artifact",
            "build_artifact",
            {
                "deployment_id": request.deployment_id,
                "artifact_hash": request.artifact_hash,
            },
            "builder",
            timestamp,
            EVIDENCE_SECRET,
            dependencies=["commit"],
        ),
        signed_evidence(
            "test",
            "test_result",
            {"deployment_id": request.deployment_id, "suite": "unit", "passed": True},
            "pytest",
            timestamp,
            EVIDENCE_SECRET,
            dependencies=["artifact"],
        ),
        signed_evidence(
            "env",
            "environment_descriptor",
            {
                "deployment_id": request.deployment_id,
                "environment": request.environment,
            },
            "cluster",
            timestamp,
            EVIDENCE_SECRET,
        ),
        signed_evidence(
            "rollback",
            "rollback_spec",
            {
                "deployment_id": request.deployment_id,
                "service": request.service,
                "rollback_plan": "restore previous image",
            },
            "deploy-plan",
            timestamp,
            EVIDENCE_SECRET,
        ),
        signed_evidence(
            "human",
            "human_attestation",
            {
                "deployment_id": request.deployment_id,
                "role": "release-manager",
                "approved": True,
            },
            "approvals",
            human_timestamp,
            EVIDENCE_SECRET,
        ),
    ]
    if include_scan:
        objects.append(
            signed_evidence(
                "scan",
                "scan_report",
                {
                    "deployment_id": request.deployment_id,
                    "vulnerabilities": {"critical": 0, "high": 1},
                },
                "scanner",
                timestamp,
                EVIDENCE_SECRET,
                dependencies=["artifact"],
            )
        )
    return EvidenceBundle(objects)


def _proof(request: DeploymentRequest, *, final: str = "") -> ProofDerivation:
    return ProofDerivation(
        final_conclusion=final or f"deploy_authorized:{request.deployment_id}",
        steps=[
            ProofStep("test-pass", "TEST_PASS", "test_passed:unit", ["test"]),
            ProofStep("scan-ok", "VULN_OK", "vuln_count:critical<=0", ["scan"]),
            ProofStep("env-ok", "ENVIRONMENT_MATCH", "environment_matches", ["env"]),
            ProofStep("rollback-ok", "ROLLBACK_READY", "rollback_defined", ["rollback"]),
            ProofStep(
                "human-ok",
                "HUMAN_ATTESTED",
                "human_attested:release-manager",
                ["human"],
            ),
            ProofStep(
                "policy",
                "POLICY_SATISFIED",
                "policy_satisfied",
                premises=[
                    "test-pass",
                    "scan-ok",
                    "env-ok",
                    "rollback-ok",
                    "human-ok",
                ],
            ),
            ProofStep(
                "deploy",
                "DEPLOY_AUTH",
                f"deploy_authorized:{request.deployment_id}",
                premises=["policy"],
            ),
        ],
    )


def _certificate(include_scan: bool = True) -> DeploymentCertificate:
    request = _request()
    return DeploymentCertificate(
        request=request,
        policy=_policy(),
        evidence=_evidence(request, include_scan=include_scan),
        proof=_proof(request),
    )


def _accepted_certificate() -> DeploymentCertificate:
    certificate = _certificate()
    checker = CertificateChecker(EVIDENCE_SECRET, now=NOW)
    certificate.attestations = simulate_validators(
        certificate, checker, VALIDATORS, timestamp=NOW
    )
    return certificate


def test_valid_certificate_requires_all_four_acceptance_terms():
    certificate = _accepted_certificate()
    checker = CertificateChecker(EVIDENCE_SECRET, VALIDATORS, now=NOW)

    report = checker.accept(certificate)

    assert report.accepted
    assert report.wf_policy
    assert report.auth_evidence
    assert report.check_proof
    assert report.quorum


def test_missing_scan_evidence_is_structured_rejection():
    certificate = _certificate(include_scan=False)
    checker = CertificateChecker(EVIDENCE_SECRET, VALIDATORS, now=NOW)

    report = checker.accept(certificate)

    assert not report.accepted
    codes = {failure.code for failure in report.failures}
    assert "EVIDENCE_REQUIRED_MISSING:scan_report" in codes
    assert "VRP_EVIDENCE_REF_MISSING:scan-ok:scan" in codes


def test_stale_human_attestation_rejects_when_policy_requires_freshness():
    request = _request()
    certificate = DeploymentCertificate(
        request=request,
        policy=_policy(),
        evidence=_evidence(request, human_timestamp=NOW - 7200),
        proof=_proof(request),
    )
    checker = CertificateChecker(EVIDENCE_SECRET, VALIDATORS, now=NOW)

    report = checker.accept(certificate)

    assert not report.accepted
    assert "EVIDENCE_STALE:human" in {failure.code for failure in report.failures}


def test_wrong_environment_binding_rejects():
    request = _request("staging")
    certificate = DeploymentCertificate(
        request=request,
        policy=_policy("production"),
        evidence=_evidence(request),
        proof=_proof(request),
    )
    checker = CertificateChecker(EVIDENCE_SECRET, VALIDATORS, now=NOW)

    report = checker.accept(certificate)

    assert not report.accepted
    codes = {failure.code for failure in report.failures}
    assert "EVIDENCE_ENVIRONMENT_TARGET_MISMATCH:production" in codes
    assert "VRP_ENVIRONMENT_POLICY_MISMATCH:env-ok" in codes


def test_invalid_derivation_step_rejects():
    certificate = _certificate()
    certificate.proof.steps[0].rule = "TRUST_ME"
    checker = CertificateChecker(EVIDENCE_SECRET, VALIDATORS, now=NOW)

    report = checker.accept(certificate)

    assert not report.accepted
    assert "VRP_RULE_UNKNOWN:TRUST_ME" in {failure.code for failure in report.failures}


def test_insufficient_validator_quorum_rejects():
    certificate = _certificate()
    digest = certificate.digest()
    certificate.attestations = [
        ValidatorAttestation("validator-a", "accept", digest, NOW).sign(
            VALIDATORS["validator-a"]
        ),
        ValidatorAttestation("validator-b", "reject", digest, NOW).sign(
            VALIDATORS["validator-b"]
        ),
    ]
    checker = CertificateChecker(EVIDENCE_SECRET, VALIDATORS, now=NOW)

    report = checker.accept(certificate)

    assert not report.accepted
    assert "QUORUM_INSUFFICIENT_ACCEPT_WEIGHT" in {
        failure.code for failure in report.failures
    }


def test_canonical_evidence_root_is_order_independent():
    certificate = _certificate()
    reversed_bundle = EvidenceBundle(list(reversed(certificate.evidence.objects)))

    assert certificate.evidence.root() == reversed_bundle.root()


def test_unsigned_or_tampered_evidence_rejects():
    certificate = _certificate()
    certificate.evidence.objects[0].value["commit_sha"] = "tampered"
    checker = CertificateChecker(EVIDENCE_SECRET, VALIDATORS, now=NOW)

    report = checker.accept(certificate)

    assert not report.accepted
    codes = {failure.code for failure in report.failures}
    assert "EVIDENCE_HASH_INVALID:commit" in codes
    assert "EVIDENCE_COMMIT_MISMATCH" in codes


def test_jsonl_ledger_appends_and_replays_finalized_certificate(tmp_path: Path):
    certificate = _accepted_certificate()
    checker = CertificateChecker(EVIDENCE_SECRET, VALIDATORS, now=NOW)
    report = checker.accept(certificate)
    ledger = JsonlDeploymentLedger(tmp_path / "deployments.jsonl")

    entry = ledger.append(certificate, report, timestamp=NOW)
    replay = ledger.replay_verify(certificate, checker)

    assert entry.certificate_digest == certificate.digest()
    assert replay.accepted


def test_avm_trace_can_emit_signed_evidence_bundle():
    trace = DeploymentTrace(
        deployment_id="deploy-123",
        tools=[
            ToolTrace(
                tool_name="pytest",
                output_type="test_result",
                output={"suite": "unit", "passed": True},
                timestamp=NOW,
            )
        ],
    )

    bundle = trace.to_evidence_bundle(EVIDENCE_SECRET)

    assert bundle.objects[0].evidence_type == "test_result"
    assert bundle.objects[0].verify(EVIDENCE_SECRET)


def test_policy_well_formed_rejects_unknown_predicate():
    policy = DeploymentPolicy(
        policy_id="bad",
        version=1,
        environment="production",
        predicates=[PolicyPredicate("trust_me", {})],
    )

    report = policy.well_formed()

    assert not report.well_formed
    assert "POLICY_PREDICATE_UNKNOWN:trust_me" in report.failures

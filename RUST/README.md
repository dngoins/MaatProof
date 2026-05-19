# MaatProof Rust implementation

This folder contains the Rust reference implementation of the core MaatProof proof-carrying deployment model.

## Scope

The crate implements canonical hashing, HMAC signatures, reasoning proofs, policy and evidence validation, VRP derivation checks, validator quorum, deployment certificates, JSONL ledger entries, AVM trace conversion, deterministic gates, agent gates, orchestration, and ACI/ACD pipeline helpers.

## Use it from another Rust project

Add the crate as a path dependency while working from this repository:

```toml
[dependencies]
maatproof-rust = { path = "../RUST" }
serde_json = "1"
```

Then import the public API from `maatproof_rust`:

```rust
use maatproof_rust::{
    CertificateChecker, DeploymentCertificate, DeploymentPolicy, DeploymentRequest,
    EvidenceBundle, PolicyPredicate, ProofDerivation, ProofStep, signed_evidence,
};
use serde_json::json;

fn main() -> Result<(), String> {
    let evidence_key = b"evidence-secret";
    let now = 1_700_000_100.0;

    let request = DeploymentRequest {
        deployment_id: "deploy-123".to_string(),
        service: "checkout".to_string(),
        environment: "production".to_string(),
        commit_sha: "abc123".to_string(),
        artifact_hash: "sha256:artifact".to_string(),
        requested_by: "agent:planner".to_string(),
    };

    let policy = DeploymentPolicy::new(
        "checkout-prod",
        1,
        "production",
        vec![
            PolicyPredicate::new("test_passed", json!({"suite": "unit"})),
            PolicyPredicate::new(
                "vuln_count",
                json!({"severity": "critical", "operator": "<=", "threshold": 0}),
            ),
            PolicyPredicate::new("environment_matches", json!({"target": "production"})),
        ],
    );

    let evidence = EvidenceBundle::new(vec![
        signed_evidence(
            "commit",
            "commit_snapshot",
            json!({"deployment_id": &request.deployment_id, "commit_sha": &request.commit_sha}),
            "git",
            now,
            evidence_key,
            vec![],
        )?,
        signed_evidence(
            "artifact",
            "build_artifact",
            json!({"deployment_id": &request.deployment_id, "artifact_hash": &request.artifact_hash}),
            "builder",
            now,
            evidence_key,
            vec![],
        )?,
        signed_evidence(
            "test",
            "test_result",
            json!({"deployment_id": &request.deployment_id, "suite": "unit", "passed": true}),
            "cargo test",
            now,
            evidence_key,
            vec![],
        )?,
        signed_evidence(
            "scan",
            "scan_report",
            json!({"deployment_id": &request.deployment_id, "vulnerabilities": {"critical": 0}}),
            "scanner",
            now,
            evidence_key,
            vec![],
        )?,
        signed_evidence(
            "env",
            "environment_descriptor",
            json!({"deployment_id": &request.deployment_id, "environment": "production"}),
            "cluster",
            now,
            evidence_key,
            vec![],
        )?,
    ]);

    let proof = ProofDerivation {
        final_conclusion: format!("deploy_authorized:{}", request.deployment_id),
        steps: vec![
            ProofStep::new("test-pass", "TEST_PASS", "test_passed:unit", vec!["test".into()], vec![]),
            ProofStep::new("scan-ok", "VULN_OK", "vuln_count:critical<=0", vec!["scan".into()], vec![]),
            ProofStep::new("env-ok", "ENVIRONMENT_MATCH", "environment_matches", vec!["env".into()], vec![]),
            ProofStep::new(
                "policy",
                "POLICY_SATISFIED",
                "policy_satisfied",
                vec![],
                vec!["test-pass".into(), "scan-ok".into(), "env-ok".into()],
            ),
            ProofStep::new(
                "deploy",
                "DEPLOY_AUTH",
                format!("deploy_authorized:{}", request.deployment_id),
                vec![],
                vec!["policy".into()],
            ),
        ],
    };

    let certificate = DeploymentCertificate {
        request,
        policy,
        evidence,
        proof,
        attestations: vec![],
    };

    let report = CertificateChecker::new(evidence_key.to_vec())
        .with_now(now)
        .accept_without_quorum(&certificate);

    assert!(report.accepted);
    Ok(())
}
```

For a fuller end-to-end sample with validator attestations, quorum, AVM traces, and pipeline helpers, see the unit tests in `src/core.rs`.

## Run checks

```powershell
Set-Location .\RUST
cargo test
```


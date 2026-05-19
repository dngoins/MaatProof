//! Rust reference implementation of the MaatProof proof-carrying deployment model.
//!
//! The crate mirrors the Python reference implementation now stored in `PYTHON/`.
//! It provides canonical hashing, HMAC signatures, reasoning proofs, policy and
//! evidence checks, VRP derivation validation, validator quorum, deployment
//! certificates, a JSONL ledger, AVM trace conversion, and ACI/ACD pipeline helpers.

use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};
use uuid::Uuid;

type HmacSha256 = Hmac<Sha256>;

fn now_seconds() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .unwrap_or(0.0)
}

fn sorted_value(value: &Value) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.iter().map(sorted_value).collect()),
        Value::Object(object) => {
            let mut sorted = Map::new();
            let by_key: BTreeMap<_, _> = object.iter().collect();
            for (key, child) in by_key {
                sorted.insert(key.clone(), sorted_value(child));
            }
            Value::Object(sorted)
        }
        _ => value.clone(),
    }
}

/// Return deterministic JSON for hashing and signing.
pub fn canonical_dumps<T: Serialize>(value: &T) -> String {
    let value = serde_json::to_value(value).expect("serializable canonical value");
    serde_json::to_string(&sorted_value(&value)).expect("canonical JSON")
}

/// Return a SHA-256 hex digest over canonical JSON.
pub fn canonical_hash<T: Serialize>(value: &T) -> String {
    let mut hasher = Sha256::new();
    hasher.update(canonical_dumps(value).as_bytes());
    hex_lower(&hasher.finalize())
}

/// Return an HMAC-SHA256 signature over canonical JSON.
pub fn hmac_sign<T: Serialize>(secret_key: &[u8], value: &T) -> Result<String, String> {
    if secret_key.is_empty() {
        return Err("secret_key must not be empty".to_string());
    }
    hmac_sign_raw(secret_key, canonical_dumps(value).as_bytes())
}

/// Verify an HMAC-SHA256 signature over canonical JSON.
pub fn hmac_verify<T: Serialize>(secret_key: &[u8], value: &T, signature: &str) -> bool {
    hmac_sign(secret_key, value)
        .map(|expected| constant_time_eq(expected.as_bytes(), signature.as_bytes()))
        .unwrap_or(false)
}

fn hmac_sign_raw(secret_key: &[u8], message: &[u8]) -> Result<String, String> {
    if secret_key.is_empty() {
        return Err("secret_key must not be empty".to_string());
    }
    let mut mac = HmacSha256::new_from_slice(secret_key)
        .map_err(|_| "invalid HMAC key length".to_string())?;
    mac.update(message);
    Ok(hex_lower(&mac.finalize().into_bytes()))
}

fn sha256_hex(message: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(message.as_bytes());
    hex_lower(&hasher.finalize())
}

fn hex_lower(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.iter()
        .zip(right)
        .fold(0u8, |acc, (a, b)| acc | (a ^ b))
        == 0
}

fn as_string(value: Option<&Value>) -> Option<&str> {
    value.and_then(Value::as_str).filter(|text| !text.is_empty())
}

fn as_bool(value: Option<&Value>) -> Option<bool> {
    value.and_then(Value::as_bool)
}

fn as_i64(value: Option<&Value>) -> Option<i64> {
    value.and_then(Value::as_i64)
}

fn map_from_value(value: Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

fn value_map(items: &[(&str, Value)]) -> Map<String, Value> {
    items
        .iter()
        .map(|(key, value)| ((*key).to_string(), value.clone()))
        .collect()
}

/// A typed deployment-policy predicate.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PolicyPredicate {
    pub name: String,
    #[serde(default)]
    pub params: Map<String, Value>,
}

impl PolicyPredicate {
    pub fn new(name: impl Into<String>, params: Value) -> Self {
        Self {
            name: name.into(),
            params: map_from_value(params),
        }
    }

    pub fn to_value(&self) -> Value {
        json!({"name": self.name, "params": self.params})
    }
}

/// Result of evaluating `WF(P)`.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct PolicyCheckReport {
    pub well_formed: bool,
    #[serde(default)]
    pub failures: Vec<String>,
}

/// Executable deployment contract used as the policy object `P`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DeploymentPolicy {
    pub policy_id: String,
    pub version: i64,
    pub environment: String,
    pub predicates: Vec<PolicyPredicate>,
    #[serde(default = "default_rule_set")]
    pub rule_set_id: String,
    #[serde(default)]
    pub freshness_seconds: BTreeMap<String, f64>,
}

fn default_rule_set() -> String {
    "pod-v1".to_string()
}

impl DeploymentPolicy {
    pub fn new(
        policy_id: impl Into<String>,
        version: i64,
        environment: impl Into<String>,
        predicates: Vec<PolicyPredicate>,
    ) -> Self {
        Self {
            policy_id: policy_id.into(),
            version,
            environment: environment.into(),
            predicates,
            rule_set_id: default_rule_set(),
            freshness_seconds: BTreeMap::new(),
        }
    }

    pub fn to_value(&self) -> Value {
        json!({
            "policy_id": self.policy_id,
            "version": self.version,
            "environment": self.environment,
            "rule_set_id": self.rule_set_id,
            "freshness_seconds": self.freshness_seconds,
            "predicates": self.predicates.iter().map(PolicyPredicate::to_value).collect::<Vec<_>>(),
        })
    }

    pub fn all_predicates(&self) -> Vec<PolicyPredicate> {
        let mut predicates = Vec::new();
        walk_predicates(&self.predicates, &mut predicates);
        predicates
    }

    pub fn required_evidence_types(&self) -> Vec<String> {
        let mut required = BTreeSet::from([
            "commit_snapshot".to_string(),
            "build_artifact".to_string(),
            "environment_descriptor".to_string(),
        ]);
        for predicate in self.all_predicates() {
            match predicate.name.as_str() {
                "test_passed" => {
                    required.insert("test_result".to_string());
                }
                "vuln_count" => {
                    required.insert("scan_report".to_string());
                }
                "human_attested" => {
                    required.insert("human_attestation".to_string());
                }
                "rollback_defined" => {
                    required.insert("rollback_spec".to_string());
                }
                "canary_enabled" => {
                    required.insert("rollout_spec".to_string());
                }
                "environment_matches" => {
                    required.insert("environment_descriptor".to_string());
                }
                _ => {}
            }
        }
        required.into_iter().collect()
    }

    pub fn human_attestation_required(&self) -> bool {
        self.all_predicates()
            .iter()
            .any(|predicate| predicate.name == "human_attested")
    }

    pub fn well_formed(&self) -> PolicyCheckReport {
        let mut failures = Vec::new();
        if self.policy_id.is_empty() {
            failures.push("POLICY_ID_REQUIRED".to_string());
        }
        if self.version <= 0 {
            failures.push("POLICY_VERSION_INVALID".to_string());
        }
        if self.environment.is_empty() {
            failures.push("POLICY_ENVIRONMENT_REQUIRED".to_string());
        }
        if self.rule_set_id != "pod-v1" {
            failures.push("POLICY_RULE_SET_UNSUPPORTED".to_string());
        }
        if self.predicates.is_empty() {
            failures.push("POLICY_PREDICATES_REQUIRED".to_string());
        }
        let all_predicates = self.all_predicates();
        for predicate in &all_predicates {
            failures.extend(validate_predicate(predicate));
        }
        let environments: Vec<_> = all_predicates
            .iter()
            .filter(|predicate| predicate.name == "environment_matches")
            .filter_map(|predicate| as_string(predicate.params.get("target")))
            .collect();
        if !environments.is_empty() && !environments.contains(&self.environment.as_str()) {
            failures.push("POLICY_ENVIRONMENT_CONTRADICTION".to_string());
        }
        for (evidence_type, seconds) in &self.freshness_seconds {
            if evidence_type.is_empty() {
                failures.push("POLICY_FRESHNESS_TYPE_INVALID".to_string());
            }
            if *seconds <= 0.0 {
                failures.push(format!("POLICY_FRESHNESS_INVALID:{evidence_type}"));
            }
        }
        PolicyCheckReport {
            well_formed: failures.is_empty(),
            failures,
        }
    }
}

fn walk_predicates(source: &[PolicyPredicate], target: &mut Vec<PolicyPredicate>) {
    for predicate in source {
        target.push(predicate.clone());
        match predicate.name.as_str() {
            "and" | "or" => {
                if let Some(children) = predicate.params.get("children").and_then(Value::as_array) {
                    let parsed: Vec<_> = children
                        .iter()
                        .filter_map(|child| serde_json::from_value(child.clone()).ok())
                        .collect();
                    walk_predicates(&parsed, target);
                }
            }
            "not" => {
                if let Some(child) = predicate.params.get("child") {
                    if let Ok(child) = serde_json::from_value::<PolicyPredicate>(child.clone()) {
                        walk_predicates(&[child], target);
                    }
                }
            }
            _ => {}
        }
    }
}

fn validate_predicate(predicate: &PolicyPredicate) -> Vec<String> {
    let mut failures = Vec::new();
    let params = &predicate.params;
    match predicate.name.as_str() {
        "test_passed" => {
            if as_string(params.get("suite")).is_none() {
                failures.push("POLICY_TEST_SUITE_INVALID".to_string());
            }
        }
        "vuln_count" => {
            if as_string(params.get("severity")).is_none() {
                failures.push("POLICY_VULN_SEVERITY_INVALID".to_string());
            }
            if !matches!(as_string(params.get("operator")), Some("<=" | "==")) {
                failures.push("POLICY_VULN_OPERATOR_INVALID".to_string());
            }
            if as_i64(params.get("threshold")).map(|value| value < 0).unwrap_or(true) {
                failures.push("POLICY_VULN_THRESHOLD_INVALID".to_string());
            }
        }
        "human_attested" => {
            if as_string(params.get("role")).is_none() {
                failures.push("POLICY_HUMAN_ROLE_INVALID".to_string());
            }
        }
        "rollback_defined" | "canary_enabled" => {
            if as_string(params.get("service")).is_none() {
                failures.push(format!(
                    "POLICY_{}_SERVICE_INVALID",
                    predicate.name.to_uppercase()
                ));
            }
        }
        "environment_matches" => {
            if as_string(params.get("target")).is_none() {
                failures.push("POLICY_ENVIRONMENT_TARGET_INVALID".to_string());
            }
        }
        "and" | "or" => {
            if params
                .get("children")
                .and_then(Value::as_array)
                .map(|items| items.is_empty())
                .unwrap_or(true)
            {
                failures.push(format!("POLICY_{}_CHILDREN_REQUIRED", predicate.name.to_uppercase()));
            }
        }
        "not" => {
            if !params.get("child").map(Value::is_object).unwrap_or(false) {
                failures.push("POLICY_NOT_CHILD_REQUIRED".to_string());
            }
        }
        unknown => failures.push(format!("POLICY_PREDICATE_UNKNOWN:{unknown}")),
    }
    failures
}

/// A signed external fact used by a deployment certificate.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EvidenceObject {
    pub evidence_id: String,
    pub evidence_type: String,
    #[serde(default)]
    pub value: Map<String, Value>,
    pub source: String,
    pub timestamp: f64,
    #[serde(default)]
    pub dependencies: Vec<String>,
    #[serde(default)]
    pub hash: String,
    #[serde(default)]
    pub signature: String,
}

impl EvidenceObject {
    pub fn payload(&self) -> Value {
        let mut dependencies = self.dependencies.clone();
        dependencies.sort();
        json!({
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type,
            "value": self.value,
            "source": self.source,
            "timestamp": self.timestamp,
            "dependencies": dependencies,
        })
    }

    pub fn compute_hash(&self) -> String {
        canonical_hash(&self.payload())
    }

    pub fn sign(mut self, secret_key: &[u8]) -> Result<Self, String> {
        self.hash = self.compute_hash();
        self.signature = hmac_sign(secret_key, &self.hash)?;
        Ok(self)
    }

    pub fn verify(&self, secret_key: &[u8]) -> bool {
        self.hash == self.compute_hash() && hmac_verify(secret_key, &self.hash, &self.signature)
    }

    pub fn to_value(&self) -> Value {
        let mut payload = self.payload();
        if let Value::Object(ref mut object) = payload {
            object.insert("hash".to_string(), Value::String(self.hash.clone()));
            object.insert("signature".to_string(), Value::String(self.signature.clone()));
        }
        payload
    }
}

/// Result of evaluating `Auth(E)`.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct EvidenceAuthReport {
    pub authenticated: bool,
    #[serde(default)]
    pub failures: Vec<String>,
    #[serde(default)]
    pub evidence_root: String,
}

/// Canonical bundle of signed deployment evidence.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct EvidenceBundle {
    pub objects: Vec<EvidenceObject>,
}

impl EvidenceBundle {
    pub fn new(objects: Vec<EvidenceObject>) -> Self {
        Self { objects }
    }

    pub fn sorted_objects(&self) -> Vec<EvidenceObject> {
        let mut objects = self.objects.clone();
        objects.sort_by(|left, right| left.evidence_id.cmp(&right.evidence_id));
        objects
    }

    pub fn to_value(&self) -> Value {
        json!({"objects": self.sorted_objects().iter().map(EvidenceObject::to_value).collect::<Vec<_>>()})
    }

    pub fn root(&self) -> String {
        canonical_hash(&self.to_value())
    }

    pub fn by_id(&self) -> HashMap<String, EvidenceObject> {
        self.objects
            .iter()
            .map(|object| (object.evidence_id.clone(), object.clone()))
            .collect()
    }

    pub fn of_type(&self, evidence_type: &str) -> Vec<EvidenceObject> {
        self.sorted_objects()
            .into_iter()
            .filter(|object| object.evidence_type == evidence_type)
            .collect()
    }

    pub fn first_of_type(&self, evidence_type: &str) -> Option<EvidenceObject> {
        self.of_type(evidence_type).into_iter().next()
    }

    pub fn authenticate(
        &self,
        policy: &DeploymentPolicy,
        request: &DeploymentRequest,
        secret_key: &[u8],
        now: Option<f64>,
    ) -> EvidenceAuthReport {
        let mut failures = Vec::new();
        let mut objects_by_id = HashMap::new();
        for object in &self.objects {
            if objects_by_id.contains_key(&object.evidence_id) {
                failures.push(format!("EVIDENCE_DUPLICATE_ID:{}", object.evidence_id));
            }
            objects_by_id.insert(object.evidence_id.clone(), object.clone());
            if object.hash.is_empty() {
                failures.push(format!("EVIDENCE_HASH_MISSING:{}", object.evidence_id));
            } else if object.hash != object.compute_hash() {
                failures.push(format!("EVIDENCE_HASH_INVALID:{}", object.evidence_id));
            }
            if object.signature.is_empty() {
                failures.push(format!("EVIDENCE_SIGNATURE_MISSING:{}", object.evidence_id));
            } else if object.hash == object.compute_hash() && !object.verify(secret_key) {
                failures.push(format!("EVIDENCE_SIGNATURE_INVALID:{}", object.evidence_id));
            }
        }
        for object in &self.objects {
            for dependency in &object.dependencies {
                if !objects_by_id.contains_key(dependency) {
                    failures.push(format!(
                        "EVIDENCE_DEPENDENCY_MISSING:{}:{}",
                        object.evidence_id, dependency
                    ));
                }
            }
        }
        for evidence_type in policy.required_evidence_types() {
            if self.of_type(&evidence_type).is_empty() {
                failures.push(format!("EVIDENCE_REQUIRED_MISSING:{evidence_type}"));
            }
        }
        if let Some(now) = now {
            for (evidence_type, max_age) in &policy.freshness_seconds {
                for object in self.of_type(evidence_type) {
                    if now - object.timestamp > *max_age {
                        failures.push(format!("EVIDENCE_STALE:{}", object.evidence_id));
                    }
                }
            }
        }
        failures.extend(check_request_bindings(self, request));
        failures.extend(check_policy_predicates(self, policy, request));
        EvidenceAuthReport {
            authenticated: failures.is_empty(),
            failures,
            evidence_root: self.root(),
        }
    }
}

pub fn signed_evidence(
    evidence_id: impl Into<String>,
    evidence_type: impl Into<String>,
    value: Value,
    source: impl Into<String>,
    timestamp: f64,
    secret_key: &[u8],
    dependencies: Vec<String>,
) -> Result<EvidenceObject, String> {
    EvidenceObject {
        evidence_id: evidence_id.into(),
        evidence_type: evidence_type.into(),
        value: map_from_value(value),
        source: source.into(),
        timestamp,
        dependencies,
        hash: String::new(),
        signature: String::new(),
    }
    .sign(secret_key)
}

fn check_request_bindings(bundle: &EvidenceBundle, request: &DeploymentRequest) -> Vec<String> {
    let mut failures = Vec::new();
    if let Some(commit) = bundle.first_of_type("commit_snapshot") {
        if as_string(commit.value.get("commit_sha")) != Some(request.commit_sha.as_str()) {
            failures.push("EVIDENCE_COMMIT_MISMATCH".to_string());
        }
    }
    if let Some(artifact) = bundle.first_of_type("build_artifact") {
        if as_string(artifact.value.get("artifact_hash")) != Some(request.artifact_hash.as_str()) {
            failures.push("EVIDENCE_ARTIFACT_MISMATCH".to_string());
        }
    }
    if let Some(env) = bundle.first_of_type("environment_descriptor") {
        if as_string(env.value.get("environment")) != Some(request.environment.as_str()) {
            failures.push("EVIDENCE_ENVIRONMENT_MISMATCH".to_string());
        }
    }
    for object in bundle.sorted_objects() {
        if let Some(deployment_id) = as_string(object.value.get("deployment_id")) {
            if deployment_id != request.deployment_id {
                failures.push(format!("EVIDENCE_DEPLOYMENT_MISMATCH:{}", object.evidence_id));
            }
        }
    }
    failures
}

fn check_policy_predicates(
    bundle: &EvidenceBundle,
    policy: &DeploymentPolicy,
    request: &DeploymentRequest,
) -> Vec<String> {
    let mut failures = Vec::new();
    for predicate in policy.all_predicates() {
        let params = &predicate.params;
        match predicate.name.as_str() {
            "and" | "or" | "not" => {}
            "test_passed" => {
                let suite = as_string(params.get("suite")).unwrap_or_default();
                if !has_test_passed(bundle, suite) {
                    failures.push(format!("EVIDENCE_TEST_NOT_PASSED:{suite}"));
                }
            }
            "vuln_count" => {
                if !vuln_count_ok(bundle, params) {
                    failures.push(format!(
                        "EVIDENCE_VULN_THRESHOLD_EXCEEDED:{}",
                        as_string(params.get("severity")).unwrap_or_default()
                    ));
                }
            }
            "human_attested" => {
                let role = as_string(params.get("role")).unwrap_or_default();
                if !has_human_attestation(bundle, role, &request.deployment_id) {
                    failures.push(format!("EVIDENCE_HUMAN_ATTESTATION_MISSING:{role}"));
                }
            }
            "rollback_defined" => {
                let service = as_string(params.get("service")).unwrap_or_default();
                if !has_service_value(bundle, "rollback_spec", service, "rollback_plan") {
                    failures.push(format!("EVIDENCE_ROLLBACK_MISSING:{service}"));
                }
            }
            "canary_enabled" => {
                let service = as_string(params.get("service")).unwrap_or_default();
                if !has_canary(bundle, service) {
                    failures.push(format!("EVIDENCE_CANARY_MISSING:{service}"));
                }
            }
            "environment_matches" => {
                let target = as_string(params.get("target")).unwrap_or_default();
                if target != policy.environment || target != request.environment {
                    failures.push(format!("EVIDENCE_ENVIRONMENT_TARGET_MISMATCH:{target}"));
                }
            }
            _ => {}
        }
    }
    failures
}

fn has_test_passed(bundle: &EvidenceBundle, suite: &str) -> bool {
    bundle.of_type("test_result").iter().any(|object| {
        as_string(object.value.get("suite")) == Some(suite)
            && as_bool(object.value.get("passed")) == Some(true)
    })
}

fn vuln_count_ok(bundle: &EvidenceBundle, params: &Map<String, Value>) -> bool {
    let severity = as_string(params.get("severity")).unwrap_or_default();
    let threshold = as_i64(params.get("threshold")).unwrap_or(0);
    let operator = as_string(params.get("operator")).unwrap_or_default();
    bundle.of_type("scan_report").iter().any(|object| {
        let count = object
            .value
            .get("vulnerabilities")
            .and_then(Value::as_object)
            .and_then(|counts| counts.get(severity))
            .and_then(Value::as_i64)
            .unwrap_or(0);
        (operator == "<=" && count <= threshold) || (operator == "==" && count == threshold)
    })
}

fn has_human_attestation(bundle: &EvidenceBundle, role: &str, deployment_id: &str) -> bool {
    bundle.of_type("human_attestation").iter().any(|object| {
        as_string(object.value.get("role")) == Some(role)
            && as_string(object.value.get("deployment_id")) == Some(deployment_id)
            && as_bool(object.value.get("approved")) == Some(true)
    })
}

fn has_service_value(
    bundle: &EvidenceBundle,
    evidence_type: &str,
    service: &str,
    value_key: &str,
) -> bool {
    bundle.of_type(evidence_type).iter().any(|object| {
        as_string(object.value.get("service")) == Some(service)
            && object.value.get(value_key).map(is_truthy).unwrap_or(false)
    })
}

fn has_canary(bundle: &EvidenceBundle, service: &str) -> bool {
    bundle.of_type("rollout_spec").iter().any(|object| {
        as_string(object.value.get("service")) == Some(service)
            && object
                .value
                .get("canary_enabled")
                .map(is_truthy)
                .unwrap_or(false)
    })
}

fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Bool(flag) => *flag,
        Value::Null => false,
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(items) => !items.is_empty(),
        Value::Number(number) => number.as_i64().map(|n| n != 0).unwrap_or(true),
    }
}

/// A typed admissible proof step in the derivation `pi`.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct ProofStep {
    pub step_id: String,
    pub rule: String,
    pub conclusion: String,
    #[serde(default)]
    pub evidence_refs: Vec<String>,
    #[serde(default)]
    pub premises: Vec<String>,
}

impl ProofStep {
    pub fn new(
        step_id: impl Into<String>,
        rule: impl Into<String>,
        conclusion: impl Into<String>,
        evidence_refs: Vec<String>,
        premises: Vec<String>,
    ) -> Self {
        Self {
            step_id: step_id.into(),
            rule: rule.into(),
            conclusion: conclusion.into(),
            evidence_refs,
            premises,
        }
    }

    pub fn to_value(&self) -> Value {
        let mut evidence_refs = self.evidence_refs.clone();
        evidence_refs.sort();
        json!({
            "step_id": self.step_id,
            "rule": self.rule,
            "conclusion": self.conclusion,
            "evidence_refs": evidence_refs,
            "premises": self.premises,
        })
    }
}

/// Machine-checkable derivation used by `CheckR(pi, P, E)`.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct ProofDerivation {
    pub steps: Vec<ProofStep>,
    pub final_conclusion: String,
}

impl ProofDerivation {
    pub fn to_value(&self) -> Value {
        json!({
            "steps": self.steps.iter().map(ProofStep::to_value).collect::<Vec<_>>(),
            "final_conclusion": self.final_conclusion,
        })
    }
}

/// Result of evaluating `CheckR(pi, P, E)`.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct VrpCheckReport {
    pub valid: bool,
    #[serde(default)]
    pub failures: Vec<String>,
    #[serde(default)]
    pub proof_root: String,
}

pub fn check_derivation(
    derivation: &ProofDerivation,
    policy: &DeploymentPolicy,
    evidence: &EvidenceBundle,
    request: &DeploymentRequest,
) -> VrpCheckReport {
    let mut failures = Vec::new();
    let mut known_steps = HashSet::new();
    let evidence_by_id = evidence.by_id();
    if derivation.steps.is_empty() {
        failures.push("VRP_STEPS_REQUIRED".to_string());
    }
    for step in &derivation.steps {
        if step.step_id.is_empty() {
            failures.push("VRP_STEP_ID_REQUIRED".to_string());
        }
        if known_steps.contains(&step.step_id) {
            failures.push(format!("VRP_STEP_DUPLICATE:{}", step.step_id));
        }
        known_steps.insert(step.step_id.clone());
        if !valid_rules().contains(step.rule.as_str()) {
            failures.push(format!("VRP_RULE_UNKNOWN:{}", step.rule));
        }
        for premise in &step.premises {
            if !known_steps.contains(premise) {
                failures.push(format!("VRP_PREMISE_MISSING:{}:{premise}", step.step_id));
            }
        }
        for evidence_ref in &step.evidence_refs {
            if !evidence_by_id.contains_key(evidence_ref) {
                failures.push(format!(
                    "VRP_EVIDENCE_REF_MISSING:{}:{evidence_ref}",
                    step.step_id
                ));
            }
        }
        failures.extend(check_rule_semantics(step, policy, evidence, request));
    }
    let expected_final = format!("deploy_authorized:{}", request.deployment_id);
    if derivation.final_conclusion != expected_final {
        failures.push("VRP_FINAL_CONCLUSION_MISMATCH".to_string());
    }
    if derivation
        .steps
        .last()
        .map(|step| step.conclusion.as_str())
        != Some(expected_final.as_str())
    {
        failures.push("VRP_FINAL_STEP_MISMATCH".to_string());
    }
    VrpCheckReport {
        valid: failures.is_empty(),
        failures,
        proof_root: canonical_hash(&derivation.to_value()),
    }
}

fn valid_rules() -> BTreeSet<&'static str> {
    BTreeSet::from([
        "TEST_PASS",
        "VULN_OK",
        "SIGNATURE_VALID",
        "EVIDENCE_BOUND",
        "ENVIRONMENT_MATCH",
        "ROLLBACK_READY",
        "HUMAN_ATTESTED",
        "POLICY_SATISFIED",
        "DEPLOY_AUTH",
    ])
}

fn check_rule_semantics(
    step: &ProofStep,
    policy: &DeploymentPolicy,
    evidence: &EvidenceBundle,
    request: &DeploymentRequest,
) -> Vec<String> {
    let mut failures = Vec::new();
    let evidence_by_id = evidence.by_id();
    let refs: Vec<_> = step
        .evidence_refs
        .iter()
        .filter_map(|reference| evidence_by_id.get(reference))
        .cloned()
        .collect();
    match step.rule.as_str() {
        "TEST_PASS" => {
            if !refs.iter().any(|object| {
                object.evidence_type == "test_result"
                    && object.value.get("passed").map(is_truthy).unwrap_or(false)
            }) {
                failures.push(format!("VRP_TEST_PASS_UNSUPPORTED:{}", step.step_id));
            }
        }
        "VULN_OK" => {
            if !refs.iter().any(|object| object.evidence_type == "scan_report") {
                failures.push(format!("VRP_VULN_OK_UNSUPPORTED:{}", step.step_id));
            }
        }
        "SIGNATURE_VALID" => {
            if refs.is_empty() {
                failures.push(format!("VRP_SIGNATURE_REF_REQUIRED:{}", step.step_id));
            }
        }
        "EVIDENCE_BOUND" => {
            if refs.is_empty() {
                failures.push(format!("VRP_EVIDENCE_BOUND_REF_REQUIRED:{}", step.step_id));
            }
            for object in refs {
                if let Some(deployment_id) = as_string(object.value.get("deployment_id")) {
                    if deployment_id != request.deployment_id {
                        failures.push(format!("VRP_EVIDENCE_NOT_BOUND:{}", step.step_id));
                    }
                }
            }
        }
        "ENVIRONMENT_MATCH" => {
            if policy.environment != request.environment {
                failures.push(format!("VRP_ENVIRONMENT_POLICY_MISMATCH:{}", step.step_id));
            }
            if !refs.iter().any(|object| {
                object.evidence_type == "environment_descriptor"
                    && as_string(object.value.get("environment"))
                        == Some(request.environment.as_str())
            }) {
                failures.push(format!("VRP_ENVIRONMENT_REF_MISSING:{}", step.step_id));
            }
        }
        "ROLLBACK_READY" => {
            if !refs.iter().any(|object| object.evidence_type == "rollback_spec") {
                failures.push(format!("VRP_ROLLBACK_REF_MISSING:{}", step.step_id));
            }
        }
        "HUMAN_ATTESTED" => {
            if policy.human_attestation_required()
                && !refs.iter().any(|object| {
                    object.evidence_type == "human_attestation"
                        && as_bool(object.value.get("approved")) == Some(true)
                })
            {
                failures.push(format!(
                    "VRP_HUMAN_ATTESTATION_REF_MISSING:{}",
                    step.step_id
                ));
            }
        }
        "POLICY_SATISFIED" => {
            if step.premises.is_empty() {
                failures.push(format!("VRP_POLICY_PREMISES_REQUIRED:{}", step.step_id));
            }
            if step.conclusion != "policy_satisfied" {
                failures.push(format!("VRP_POLICY_CONCLUSION_INVALID:{}", step.step_id));
            }
        }
        "DEPLOY_AUTH" => {
            if !step.conclusion.contains("policy_satisfied")
                && step.conclusion != format!("deploy_authorized:{}", request.deployment_id)
            {
                failures.push(format!(
                    "VRP_DEPLOY_AUTH_CONCLUSION_INVALID:{}",
                    step.step_id
                ));
            }
            if step.premises.is_empty() {
                failures.push(format!("VRP_DEPLOY_AUTH_PREMISE_REQUIRED:{}", step.step_id));
            }
        }
        _ => {}
    }
    failures
}

/// Signed validator decision over a canonical certificate digest.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ValidatorAttestation {
    pub validator_id: String,
    pub decision: String,
    pub certificate_digest: String,
    pub timestamp: f64,
    #[serde(default = "default_stake")]
    pub stake: f64,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub signature: String,
}

fn default_stake() -> f64 {
    1.0
}

impl ValidatorAttestation {
    pub fn new(
        validator_id: impl Into<String>,
        decision: impl Into<String>,
        certificate_digest: impl Into<String>,
        timestamp: f64,
    ) -> Self {
        Self {
            validator_id: validator_id.into(),
            decision: decision.into(),
            certificate_digest: certificate_digest.into(),
            timestamp,
            stake: 1.0,
            reason: String::new(),
            signature: String::new(),
        }
    }

    pub fn payload(&self) -> Value {
        json!({
            "validator_id": self.validator_id,
            "decision": self.decision,
            "certificate_digest": self.certificate_digest,
            "timestamp": self.timestamp,
            "stake": self.stake,
            "reason": self.reason,
        })
    }

    pub fn sign(mut self, secret_key: &[u8]) -> Result<Self, String> {
        self.signature = hmac_sign(secret_key, &self.payload())?;
        Ok(self)
    }

    pub fn verify(&self, secret_key: &[u8]) -> bool {
        hmac_verify(secret_key, &self.payload(), &self.signature)
    }

    pub fn to_value(&self) -> Value {
        let mut payload = self.payload();
        if let Value::Object(ref mut object) = payload {
            object.insert("signature".to_string(), Value::String(self.signature.clone()));
        }
        payload
    }
}

/// Parameterized finality rule for local validator replay.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct QuorumRule {
    pub kind: String,
    pub threshold: Option<f64>,
}

impl QuorumRule {
    pub fn majority() -> Self {
        Self {
            kind: "majority".to_string(),
            threshold: None,
        }
    }

    pub fn supermajority(fraction: f64) -> Self {
        Self {
            kind: "supermajority".to_string(),
            threshold: Some(fraction),
        }
    }

    pub fn weighted_threshold(weight: f64) -> Self {
        Self {
            kind: "weighted_threshold".to_string(),
            threshold: Some(weight),
        }
    }
}

/// Result of evaluating `Quorum(A)`.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct FinalityReport {
    pub finalized: bool,
    #[serde(default)]
    pub failures: Vec<String>,
    pub accepted_weight: f64,
    pub total_weight: f64,
    #[serde(default)]
    pub accepted_validators: Vec<String>,
    #[serde(default)]
    pub disputed_validators: Vec<String>,
}

pub fn quorum_finality(
    attestations: &[ValidatorAttestation],
    certificate_digest: &str,
    validator_secret_keys: &HashMap<String, Vec<u8>>,
    rule: &QuorumRule,
) -> FinalityReport {
    let mut failures = Vec::new();
    let mut accepted_weight = 0.0;
    let mut total_weight = 0.0;
    let mut accepted_validators = Vec::new();
    let mut disputed_validators = Vec::new();
    let mut seen = HashSet::new();
    for attestation in attestations {
        if seen.contains(&attestation.validator_id) {
            failures.push(format!(
                "QUORUM_DUPLICATE_VALIDATOR:{}",
                attestation.validator_id
            ));
            continue;
        }
        seen.insert(attestation.validator_id.clone());
        if attestation.certificate_digest != certificate_digest {
            failures.push(format!("QUORUM_DIGEST_MISMATCH:{}", attestation.validator_id));
            continue;
        }
        let Some(secret_key) = validator_secret_keys.get(&attestation.validator_id) else {
            failures.push(format!("QUORUM_VALIDATOR_UNKNOWN:{}", attestation.validator_id));
            continue;
        };
        if attestation.signature.is_empty() || !attestation.verify(secret_key) {
            failures.push(format!(
                "QUORUM_SIGNATURE_INVALID:{}",
                attestation.validator_id
            ));
            continue;
        }
        if !matches!(attestation.decision.as_str(), "accept" | "reject" | "dispute") {
            failures.push(format!("QUORUM_DECISION_INVALID:{}", attestation.validator_id));
            continue;
        }
        total_weight += attestation.stake;
        if attestation.decision == "accept" {
            accepted_weight += attestation.stake;
            accepted_validators.push(attestation.validator_id.clone());
        } else if attestation.decision == "dispute" {
            disputed_validators.push(attestation.validator_id.clone());
        }
    }
    if total_weight <= 0.0 {
        failures.push("QUORUM_NO_VALID_ATTESTATIONS".to_string());
    }
    let threshold = required_weight(rule, total_weight);
    if accepted_weight < threshold {
        failures.push("QUORUM_INSUFFICIENT_ACCEPT_WEIGHT".to_string());
    }
    FinalityReport {
        finalized: failures.is_empty(),
        failures,
        accepted_weight,
        total_weight,
        accepted_validators,
        disputed_validators,
    }
}

pub fn simulate_validators(
    certificate: &DeploymentCertificate,
    checker: &CertificateChecker,
    validator_secret_keys: &HashMap<String, Vec<u8>>,
    timestamp: f64,
    stakes: Option<&HashMap<String, f64>>,
) -> Vec<ValidatorAttestation> {
    let digest = certificate.digest();
    let report = checker.accept_without_quorum(certificate);
    let decision = if report.accepted { "accept" } else { "reject" }.to_string();
    let reason = if report.accepted {
        String::new()
    } else {
        report
            .failures
            .iter()
            .map(|failure| failure.code.clone())
            .collect::<Vec<_>>()
            .join(",")
    };
    let mut validators: Vec<_> = validator_secret_keys.iter().collect();
    validators.sort_by(|left, right| left.0.cmp(right.0));
    validators
        .into_iter()
        .map(|(validator_id, secret_key)| {
            let mut attestation =
                ValidatorAttestation::new(validator_id, decision.clone(), digest.clone(), timestamp);
            attestation.stake = stakes
                .and_then(|items| items.get(validator_id))
                .copied()
                .unwrap_or(1.0);
            attestation.reason = reason.clone();
            attestation.sign(secret_key).expect("validator secret key")
        })
        .collect()
}

fn required_weight(rule: &QuorumRule, total_weight: f64) -> f64 {
    match rule.kind.as_str() {
        "majority" => (total_weight / 2.0) + 1e-12,
        "supermajority" => total_weight * rule.threshold.unwrap_or(2.0 / 3.0),
        "weighted_threshold" => rule.threshold.unwrap_or(0.0),
        _ => f64::INFINITY,
    }
}

/// The exact deployment target authorized by a certificate.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DeploymentRequest {
    pub deployment_id: String,
    pub service: String,
    pub environment: String,
    pub commit_sha: String,
    pub artifact_hash: String,
    pub requested_by: String,
}

/// Named digest values for certificate sub-roots.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CertificateDigest {
    pub certificate: String,
    pub policy: String,
    pub evidence: String,
    pub proof: String,
}

/// Structured certificate rejection reason.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AcceptanceFailure {
    pub component: String,
    pub code: String,
    #[serde(default)]
    pub message: String,
}

impl AcceptanceFailure {
    pub fn new(component: impl Into<String>, code: impl Into<String>) -> Self {
        Self {
            component: component.into(),
            code: code.into(),
            message: String::new(),
        }
    }
}

/// Structured result for `Accept(C)`.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct AcceptanceReport {
    pub accepted: bool,
    pub certificate_digest: String,
    pub wf_policy: bool,
    pub auth_evidence: bool,
    pub check_proof: bool,
    pub quorum: bool,
    #[serde(default)]
    pub failures: Vec<AcceptanceFailure>,
    #[serde(default)]
    pub evidence_root: String,
    #[serde(default)]
    pub proof_root: String,
    #[serde(default)]
    pub policy_root: String,
}

/// Proof-carrying deployment certificate `C = <P, E, pi, A>`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DeploymentCertificate {
    pub request: DeploymentRequest,
    pub policy: DeploymentPolicy,
    pub evidence: EvidenceBundle,
    pub proof: ProofDerivation,
    #[serde(default)]
    pub attestations: Vec<ValidatorAttestation>,
}

impl DeploymentCertificate {
    pub fn body(&self) -> Value {
        json!({
            "request": self.request,
            "policy": self.policy.to_value(),
            "evidence": self.evidence.to_value(),
            "proof": self.proof.to_value(),
        })
    }

    pub fn to_value(&self) -> Value {
        let mut body = self.body();
        if let Value::Object(ref mut object) = body {
            object.insert(
                "attestations".to_string(),
                Value::Array(
                    self.attestations
                        .iter()
                        .map(ValidatorAttestation::to_value)
                        .collect(),
                ),
            );
        }
        body
    }

    pub fn digest(&self) -> String {
        canonical_hash(&self.body())
    }

    pub fn digests(&self) -> CertificateDigest {
        CertificateDigest {
            certificate: self.digest(),
            policy: canonical_hash(&self.policy.to_value()),
            evidence: self.evidence.root(),
            proof: canonical_hash(&self.proof.to_value()),
        }
    }
}

/// Top-level checker for `WF(P) && Auth(E) && CheckR(pi,P,E) && Quorum(A)`.
#[derive(Clone, Debug)]
pub struct CertificateChecker {
    pub evidence_secret_key: Vec<u8>,
    pub validator_secret_keys: HashMap<String, Vec<u8>>,
    pub quorum_rule: QuorumRule,
    pub now: Option<f64>,
}

impl CertificateChecker {
    pub fn new(evidence_secret_key: impl Into<Vec<u8>>) -> Self {
        Self {
            evidence_secret_key: evidence_secret_key.into(),
            validator_secret_keys: HashMap::new(),
            quorum_rule: QuorumRule::majority(),
            now: None,
        }
    }

    pub fn with_validators(mut self, validators: HashMap<String, Vec<u8>>) -> Self {
        self.validator_secret_keys = validators;
        self
    }

    pub fn with_now(mut self, now: f64) -> Self {
        self.now = Some(now);
        self
    }

    pub fn accept(&self, certificate: &DeploymentCertificate) -> AcceptanceReport {
        self.accept_inner(certificate, true)
    }

    pub fn accept_without_quorum(&self, certificate: &DeploymentCertificate) -> AcceptanceReport {
        self.accept_inner(certificate, false)
    }

    fn accept_inner(
        &self,
        certificate: &DeploymentCertificate,
        include_quorum: bool,
    ) -> AcceptanceReport {
        let mut failures = Vec::new();
        let digests = certificate.digests();
        let policy_report = certificate.policy.well_formed();
        failures.extend(
            policy_report
                .failures
                .iter()
                .map(|code| AcceptanceFailure::new("WF(P)", code)),
        );
        let evidence_report = certificate.evidence.authenticate(
            &certificate.policy,
            &certificate.request,
            &self.evidence_secret_key,
            self.now,
        );
        failures.extend(
            evidence_report
                .failures
                .iter()
                .map(|code| AcceptanceFailure::new("Auth(E)", code)),
        );
        let proof_report = check_derivation(
            &certificate.proof,
            &certificate.policy,
            &certificate.evidence,
            &certificate.request,
        );
        failures.extend(
            proof_report
                .failures
                .iter()
                .map(|code| AcceptanceFailure::new("CheckR", code)),
        );
        let finality = if include_quorum {
            quorum_finality(
                &certificate.attestations,
                &digests.certificate,
                &self.validator_secret_keys,
                &self.quorum_rule,
            )
        } else {
            FinalityReport {
                finalized: true,
                ..FinalityReport::default()
            }
        };
        failures.extend(
            finality
                .failures
                .iter()
                .map(|code| AcceptanceFailure::new("Quorum(A)", code)),
        );
        AcceptanceReport {
            accepted: policy_report.well_formed
                && evidence_report.authenticated
                && proof_report.valid
                && finality.finalized,
            certificate_digest: digests.certificate,
            wf_policy: policy_report.well_formed,
            auth_evidence: evidence_report.authenticated,
            check_proof: proof_report.valid,
            quorum: finality.finalized,
            failures,
            evidence_root: digests.evidence,
            proof_root: digests.proof,
            policy_root: digests.policy,
        }
    }
}

/// A single step in a cryptographically chained reasoning sequence.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ReasoningStep {
    pub step_id: usize,
    pub context: String,
    pub reasoning: String,
    pub conclusion: String,
    pub timestamp: f64,
    #[serde(default)]
    pub step_hash: String,
}

impl ReasoningStep {
    pub fn compute_hash(&self, previous_hash: &str) -> String {
        let canonical = canonical_dumps(&json!({
            "step_id": self.step_id,
            "context": self.context,
            "reasoning": self.reasoning,
            "conclusion": self.conclusion,
            "timestamp": self.timestamp,
            "previous_hash": previous_hash,
        }));
        sha256_hex(&canonical)
    }
}

/// A cryptographically signed, verifiable record of an LLM reasoning chain.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ReasoningProof {
    pub proof_id: String,
    pub model_id: String,
    pub chain_id: String,
    pub steps: Vec<ReasoningStep>,
    pub root_hash: String,
    pub signature: String,
    pub created_at: f64,
    #[serde(default)]
    pub metadata: Map<String, Value>,
}

/// Constructs and signs `ReasoningProof` artifacts.
#[derive(Clone, Debug)]
pub struct ProofBuilder {
    secret_key: Vec<u8>,
    model_id: String,
}

impl ProofBuilder {
    pub fn new(secret_key: impl Into<Vec<u8>>, model_id: impl Into<String>) -> Result<Self, String> {
        let secret_key = secret_key.into();
        if secret_key.is_empty() {
            return Err("secret_key must not be empty".to_string());
        }
        Ok(Self {
            secret_key,
            model_id: model_id.into(),
        })
    }

    pub fn build(
        &self,
        steps: Vec<ReasoningStep>,
        chain_id: Option<String>,
        metadata: Option<Map<String, Value>>,
    ) -> Result<ReasoningProof, String> {
        if steps.is_empty() {
            return Err("A proof requires at least one reasoning step".to_string());
        }
        let chain_id = chain_id.unwrap_or_else(|| Uuid::new_v4().to_string());
        let mut previous_hash = String::new();
        let mut signed_steps = Vec::new();
        for mut step in steps {
            step.step_hash = step.compute_hash(&previous_hash);
            previous_hash = step.step_hash.clone();
            signed_steps.push(step);
        }
        let root_hash = previous_hash;
        let signature = hmac_sign_raw(&self.secret_key, format!("{chain_id}{root_hash}").as_bytes())?;
        Ok(ReasoningProof {
            proof_id: Uuid::new_v4().to_string(),
            model_id: self.model_id.clone(),
            chain_id,
            steps: signed_steps,
            root_hash,
            signature,
            created_at: now_seconds(),
            metadata: metadata.unwrap_or_default(),
        })
    }
}

/// Verifies the integrity and authenticity of `ReasoningProof` artifacts.
#[derive(Clone, Debug)]
pub struct ProofVerifier {
    secret_key: Vec<u8>,
}

impl ProofVerifier {
    pub fn new(secret_key: impl Into<Vec<u8>>) -> Self {
        Self {
            secret_key: secret_key.into(),
        }
    }

    pub fn verify(&self, proof: &ReasoningProof) -> bool {
        if proof.steps.is_empty() {
            return false;
        }
        let mut previous_hash = String::new();
        for step in &proof.steps {
            let expected_hash = step.compute_hash(&previous_hash);
            if !constant_time_eq(step.step_hash.as_bytes(), expected_hash.as_bytes()) {
                return false;
            }
            previous_hash = step.step_hash.clone();
        }
        if !constant_time_eq(proof.root_hash.as_bytes(), previous_hash.as_bytes()) {
            return false;
        }
        hmac_sign_raw(
            &self.secret_key,
            format!("{}{}", proof.chain_id, proof.root_hash).as_bytes(),
        )
        .map(|expected| constant_time_eq(proof.signature.as_bytes(), expected.as_bytes()))
        .unwrap_or(false)
    }
}

/// Fluent builder for constructing a step-by-step reasoning chain.
#[derive(Clone, Debug)]
pub struct ReasoningChain {
    builder: ProofBuilder,
    chain_id: String,
    steps: Vec<ReasoningStep>,
}

impl ReasoningChain {
    pub fn new(builder: ProofBuilder, chain_id: Option<String>) -> Self {
        Self {
            builder,
            chain_id: chain_id.unwrap_or_else(|| Uuid::new_v4().to_string()),
            steps: Vec::new(),
        }
    }

    pub fn step(
        &mut self,
        context: impl Into<String>,
        reasoning: impl Into<String>,
        conclusion: impl Into<String>,
        timestamp: Option<f64>,
    ) -> &mut Self {
        self.steps.push(ReasoningStep {
            step_id: self.steps.len(),
            context: context.into(),
            reasoning: reasoning.into(),
            conclusion: conclusion.into(),
            timestamp: timestamp.unwrap_or_else(now_seconds),
            step_hash: String::new(),
        });
        self
    }

    pub fn seal(&self, metadata: Option<Map<String, Value>>) -> Result<ReasoningProof, String> {
        self.builder
            .build(self.steps.clone(), Some(self.chain_id.clone()), metadata)
    }

    pub fn step_count(&self) -> usize {
        self.steps.len()
    }
}

/// A finalized certificate record stored in JSONL.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct LedgerEntry {
    pub certificate_digest: String,
    pub policy_hash: String,
    pub evidence_root: String,
    pub proof_root: String,
    pub finality_result: Value,
    pub environment: String,
    pub timestamp: f64,
}

/// Local append-only JSONL ledger suitable for examples and tests.
#[derive(Clone, Debug)]
pub struct JsonlDeploymentLedger {
    path: PathBuf,
}

impl JsonlDeploymentLedger {
    pub fn new(path: impl AsRef<Path>) -> Self {
        Self {
            path: path.as_ref().to_path_buf(),
        }
    }

    pub fn append(
        &self,
        certificate: &DeploymentCertificate,
        report: &AcceptanceReport,
        timestamp: f64,
    ) -> Result<LedgerEntry, String> {
        if !report.accepted {
            return Err("only accepted certificates may be appended".to_string());
        }
        let entry = LedgerEntry {
            certificate_digest: report.certificate_digest.clone(),
            policy_hash: report.policy_root.clone(),
            evidence_root: report.evidence_root.clone(),
            proof_root: report.proof_root.clone(),
            finality_result: serde_json::to_value(report).map_err(|error| error.to_string())?,
            environment: certificate.request.environment.clone(),
            timestamp,
        };
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(|error| error.to_string())?;
        writeln!(file, "{}", canonical_dumps(&entry)).map_err(|error| error.to_string())?;
        Ok(entry)
    }

    pub fn entries(&self) -> Result<Vec<LedgerEntry>, String> {
        if !self.path.exists() {
            return Ok(Vec::new());
        }
        let file = fs::File::open(&self.path).map_err(|error| error.to_string())?;
        BufReader::new(file)
            .lines()
            .filter_map(|line| match line {
                Ok(line) if !line.trim().is_empty() => Some(Ok(line)),
                Ok(_) => None,
                Err(error) => Some(Err(error.to_string())),
            })
            .map(|line| {
                serde_json::from_str(&line?).map_err(|error| error.to_string())
            })
            .collect()
    }

    pub fn find(&self, certificate_digest: &str) -> Result<Option<LedgerEntry>, String> {
        Ok(self
            .entries()?
            .into_iter()
            .find(|entry| entry.certificate_digest == certificate_digest))
    }

    pub fn replay_verify(
        &self,
        certificate: &DeploymentCertificate,
        checker: &CertificateChecker,
    ) -> Result<AcceptanceReport, String> {
        let mut report = checker.accept(certificate);
        if self.find(&certificate.digest())?.is_none() {
            report
                .failures
                .push(AcceptanceFailure::new("Ledger", "LEDGER_ENTRY_MISSING"));
            report.accepted = false;
        }
        Ok(report)
    }
}

/// Externally observable tool output captured by the AVM boundary.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ToolTrace {
    pub tool_name: String,
    pub output_type: String,
    #[serde(default)]
    pub output: Map<String, Value>,
    pub timestamp: f64,
    #[serde(default = "default_avm_source")]
    pub source: String,
    #[serde(default)]
    pub dependencies: Vec<String>,
}

fn default_avm_source() -> String {
    "avm".to_string()
}

/// Agent-visible action without private model chain-of-thought.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AgentAction {
    pub action_id: String,
    pub action_type: String,
    pub deployment_id: String,
    pub timestamp: f64,
    pub summary: String,
}

/// Trace-to-evidence binding for one deployment request.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct DeploymentTrace {
    pub deployment_id: String,
    #[serde(default)]
    pub actions: Vec<AgentAction>,
    #[serde(default)]
    pub tools: Vec<ToolTrace>,
}

impl DeploymentTrace {
    pub fn to_evidence_bundle(&self, secret_key: &[u8]) -> Result<EvidenceBundle, String> {
        let mut evidence = Vec::new();
        for (index, tool) in self.tools.iter().enumerate() {
            let mut value = tool.output.clone();
            value
                .entry("deployment_id".to_string())
                .or_insert_with(|| Value::String(self.deployment_id.clone()));
            evidence.push(signed_evidence(
                format!("{}:{index}:{}", self.deployment_id, tool.output_type),
                tool.output_type.clone(),
                Value::Object(value),
                format!("avm:{}", tool.tool_name),
                tool.timestamp,
                secret_key,
                tool.dependencies.clone(),
            )?);
        }
        Ok(EvidenceBundle::new(evidence))
    }
}

/// Outcome of a deterministic gate check.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum GateStatus {
    Passed,
    Failed,
    Skipped,
}

impl GateStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            GateStatus::Passed => "passed",
            GateStatus::Failed => "failed",
            GateStatus::Skipped => "skipped",
        }
    }
}

/// The signed result of a deterministic gate check.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct GateResult {
    pub gate_name: String,
    pub status: GateStatus,
    pub duration_ms: f64,
    pub details: String,
    pub artifact_hash: String,
    pub timestamp: f64,
}

/// A single deterministic gate that content-addresses its output.
pub struct DeterministicGate {
    pub name: String,
    check_fn: Box<dyn Fn(&HashMap<String, Value>) -> Result<(bool, String), String> + Send + Sync>,
}

impl DeterministicGate {
    pub fn new(
        name: impl Into<String>,
        check_fn: impl Fn(&HashMap<String, Value>) -> Result<(bool, String), String>
            + Send
            + Sync
            + 'static,
    ) -> Self {
        Self {
            name: name.into(),
            check_fn: Box::new(check_fn),
        }
    }

    pub fn run(&self, kwargs: &HashMap<String, Value>) -> GateResult {
        let started = SystemTime::now();
        let (status, details) = match (self.check_fn)(kwargs) {
            Ok((true, details)) => (GateStatus::Passed, details),
            Ok((false, details)) => (GateStatus::Failed, details),
            Err(error) => (
                GateStatus::Failed,
                format!("Gate raised an exception: {error}"),
            ),
        };
        let duration_ms = started.elapsed().map(|elapsed| elapsed.as_secs_f64() * 1000.0).unwrap_or(0.0);
        let artifact_hash = canonical_hash(&json!({
            "gate": self.name,
            "status": status.as_str(),
            "details": details,
        }));
        GateResult {
            gate_name: self.name.clone(),
            status,
            duration_ms,
            details,
            artifact_hash,
            timestamp: now_seconds(),
        }
    }
}

/// A collection of deterministic gates that forms the CI trust anchor.
#[derive(Default)]
pub struct DeterministicLayer {
    gates: Vec<DeterministicGate>,
}

impl DeterministicLayer {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register(&mut self, gate: DeterministicGate) -> &mut Self {
        self.gates.push(gate);
        self
    }

    pub fn gate_names(&self) -> Vec<String> {
        self.gates.iter().map(|gate| gate.name.clone()).collect()
    }

    pub fn run_all(&self, kwargs: &HashMap<String, Value>) -> Vec<GateResult> {
        self.gates.iter().map(|gate| gate.run(kwargs)).collect()
    }

    pub fn all_passed(results: &[GateResult]) -> bool {
        results.iter().all(|result| result.status == GateStatus::Passed)
    }

    pub fn failed_gates(results: &[GateResult]) -> Vec<String> {
        results
            .iter()
            .filter(|result| result.status != GateStatus::Passed)
            .map(|result| result.gate_name.clone())
            .collect()
    }
}

/// The set of decisions an agent gate can reach.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum AgentDecision {
    Approve,
    Reject,
    Defer,
    FixAndRetry,
}

impl AgentDecision {
    pub fn as_str(&self) -> &'static str {
        match self {
            AgentDecision::Approve => "approve",
            AgentDecision::Reject => "reject",
            AgentDecision::Defer => "defer",
            AgentDecision::FixAndRetry => "fix_and_retry",
        }
    }
}

/// The decision reached by an agent gate, backed by a reasoning proof.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AgentResult {
    pub agent_name: String,
    pub decision: AgentDecision,
    pub summary: String,
    pub proof: ReasoningProof,
    #[serde(default)]
    pub metadata: Map<String, Value>,
}

/// An agent gate that wraps a reasoning function and signs its output.
pub struct AgentGate {
    pub name: String,
    reasoning_fn: Box<
        dyn Fn(&str, &mut ReasoningChain, &HashMap<String, Value>) -> (AgentDecision, String)
            + Send
            + Sync,
    >,
    proof_builder: ProofBuilder,
}

impl AgentGate {
    pub fn new(
        name: impl Into<String>,
        reasoning_fn: impl Fn(&str, &mut ReasoningChain, &HashMap<String, Value>) -> (AgentDecision, String)
            + Send
            + Sync
            + 'static,
        proof_builder: ProofBuilder,
    ) -> Self {
        Self {
            name: name.into(),
            reasoning_fn: Box::new(reasoning_fn),
            proof_builder,
        }
    }

    pub fn run(
        &self,
        context: &str,
        kwargs: &HashMap<String, Value>,
    ) -> Result<AgentResult, String> {
        let mut chain = ReasoningChain::new(self.proof_builder.clone(), None);
        let (decision, summary) = (self.reasoning_fn)(context, &mut chain, kwargs);
        let metadata = value_map(&[
            ("agent", Value::String(self.name.clone())),
            ("context", Value::String(context.to_string())),
        ]);
        let proof = chain.seal(Some(metadata.clone()))?;
        Ok(AgentResult {
            agent_name: self.name.clone(),
            decision,
            summary,
            proof,
            metadata,
        })
    }
}

/// A collection of agent gates forming the agent decision layer.
#[derive(Default)]
pub struct AgentLayer {
    gates: Vec<AgentGate>,
}

impl AgentLayer {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register(&mut self, gate: AgentGate) -> &mut Self {
        self.gates.push(gate);
        self
    }

    pub fn gate_names(&self) -> Vec<String> {
        self.gates.iter().map(|gate| gate.name.clone()).collect()
    }

    pub fn run_gate(
        &self,
        gate_name: &str,
        context: &str,
        kwargs: &HashMap<String, Value>,
    ) -> Option<Result<AgentResult, String>> {
        self.gates
            .iter()
            .find(|gate| gate.name == gate_name)
            .map(|gate| gate.run(context, kwargs))
    }

    pub fn run_all(
        &self,
        context: &str,
        kwargs: &HashMap<String, Value>,
    ) -> Vec<Result<AgentResult, String>> {
        self.gates
            .iter()
            .map(|gate| gate.run(context, kwargs))
            .collect()
    }
}

/// Pipeline lifecycle events emitted by the orchestrator.
#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum PipelineEvent {
    CodePushed,
    TestFailed,
    TestPassed,
    StagingHealthy,
    StagingUnhealthy,
    HumanApproved,
    HumanRejected,
    ProdErrorSpike,
    RollbackComplete,
}

impl PipelineEvent {
    pub fn as_str(&self) -> &'static str {
        match self {
            PipelineEvent::CodePushed => "code_pushed",
            PipelineEvent::TestFailed => "test_failed",
            PipelineEvent::TestPassed => "test_passed",
            PipelineEvent::StagingHealthy => "staging_healthy",
            PipelineEvent::StagingUnhealthy => "staging_unhealthy",
            PipelineEvent::HumanApproved => "human_approved",
            PipelineEvent::HumanRejected => "human_rejected",
            PipelineEvent::ProdErrorSpike => "prod_error_spike",
            PipelineEvent::RollbackComplete => "rollback_complete",
        }
    }
}

/// A single signed entry in the orchestrator audit log.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AuditEntry {
    pub entry_id: String,
    pub event: String,
    pub timestamp: f64,
    pub result: String,
    #[serde(default)]
    pub metadata: Map<String, Value>,
}

type EventHandler = Box<dyn Fn(&str, &HashMap<String, Value>) -> String + Send + Sync>;

/// Top-level orchestrator that coordinates deterministic and agent layers.
pub struct OrchestratingAgent {
    deterministic_layer: DeterministicLayer,
    agent_layer: AgentLayer,
    handlers: HashMap<PipelineEvent, EventHandler>,
    max_fix_retries: usize,
    audit_log: Vec<AuditEntry>,
}

impl OrchestratingAgent {
    pub fn new(
        deterministic_layer: DeterministicLayer,
        agent_layer: AgentLayer,
        max_fix_retries: usize,
    ) -> Self {
        Self {
            deterministic_layer,
            agent_layer,
            handlers: HashMap::new(),
            max_fix_retries,
            audit_log: Vec::new(),
        }
    }

    pub fn on(
        &mut self,
        event: PipelineEvent,
        handler: impl Fn(&str, &HashMap<String, Value>) -> String + Send + Sync + 'static,
    ) -> &mut Self {
        self.handlers.insert(event, Box::new(handler));
        self
    }

    pub fn emit(
        &mut self,
        event: PipelineEvent,
        context: &str,
        kwargs: HashMap<String, Value>,
    ) -> Option<String> {
        let result = if let Some(handler) = self.handlers.get(&event) {
            Some(handler(context, &kwargs))
        } else {
            match event {
                PipelineEvent::CodePushed => Some(self.handle_code_pushed(context, &kwargs)),
                PipelineEvent::TestFailed => Some(self.handle_test_failed(context, &kwargs)),
                PipelineEvent::ProdErrorSpike => {
                    Some(self.handle_prod_error_spike(context, &kwargs))
                }
                _ => None,
            }
        };
        self.record_audit(
            &event,
            result.clone().unwrap_or_else(|| "no_handler".to_string()),
            kwargs,
        );
        result
    }

    pub fn deterministic_layer_mut(&mut self) -> &mut DeterministicLayer {
        &mut self.deterministic_layer
    }

    pub fn agent_layer_mut(&mut self) -> &mut AgentLayer {
        &mut self.agent_layer
    }

    pub fn get_audit_log(&self) -> Vec<AuditEntry> {
        self.audit_log.clone()
    }

    fn handle_code_pushed(&self, _context: &str, kwargs: &HashMap<String, Value>) -> String {
        let results = self.deterministic_layer.run_all(kwargs);
        if DeterministicLayer::all_passed(&results) {
            "deterministic_gates_passed".to_string()
        } else {
            format!(
                "deterministic_gates_failed:{}",
                DeterministicLayer::failed_gates(&results).join(",")
            )
        }
    }

    fn handle_test_failed(&self, context: &str, kwargs: &HashMap<String, Value>) -> String {
        let retry_count = kwargs
            .get("retry_count")
            .and_then(Value::as_u64)
            .unwrap_or(0) as usize;
        if retry_count >= self.max_fix_retries {
            return "max_retries_exceeded:escalating_to_human".to_string();
        }
        match self.agent_layer.run_gate("test_fixer", context, kwargs) {
            None => "no_test_fixer_agent_registered".to_string(),
            Some(Err(error)) => format!("fix_failed:{error}"),
            Some(Ok(result)) if result.decision == AgentDecision::FixAndRetry => {
                format!("fix_applied_retrying:attempt_{}", retry_count + 1)
            }
            Some(Ok(result)) if result.decision == AgentDecision::Approve => {
                "tests_passing_after_fix".to_string()
            }
            Some(Ok(result)) => format!("fix_failed:{}", result.summary),
        }
    }

    fn handle_prod_error_spike(&self, context: &str, kwargs: &HashMap<String, Value>) -> String {
        match self.agent_layer.run_gate("rollback_agent", context, kwargs) {
            None => "no_rollback_agent_registered:manual_intervention_required".to_string(),
            Some(Err(error)) => format!("rollback_deferred:{error}"),
            Some(Ok(result)) if result.decision == AgentDecision::Approve => {
                "rollback_initiated".to_string()
            }
            Some(Ok(result)) => format!("rollback_deferred:{}", result.summary),
        }
    }

    fn record_audit(
        &mut self,
        event: &PipelineEvent,
        result: String,
        metadata: HashMap<String, Value>,
    ) {
        self.audit_log.push(AuditEntry {
            entry_id: Uuid::new_v4().to_string(),
            event: event.as_str().to_string(),
            timestamp: now_seconds(),
            result,
            metadata: metadata.into_iter().collect(),
        });
    }
}

/// Configuration for an ACI/ACD pipeline instance.
#[derive(Clone, Debug, PartialEq)]
pub struct PipelineConfig {
    pub name: String,
    pub secret_key: Vec<u8>,
    pub model_id: String,
    pub require_human_approval: bool,
    pub max_fix_retries: usize,
}

impl PipelineConfig {
    pub fn new(name: impl Into<String>, secret_key: impl Into<Vec<u8>>) -> Self {
        Self {
            name: name.into(),
            secret_key: secret_key.into(),
            model_id: "maatproof-v1".to_string(),
            require_human_approval: true,
            max_fix_retries: 3,
        }
    }
}

/// Agentic CI Pipeline.
pub struct ACIPipeline {
    pub config: PipelineConfig,
    proof_builder: ProofBuilder,
    orchestrator: OrchestratingAgent,
}

impl ACIPipeline {
    pub fn new(config: PipelineConfig) -> Result<Self, String> {
        let proof_builder = ProofBuilder::new(config.secret_key.clone(), config.model_id.clone())?;
        let orchestrator = OrchestratingAgent::new(
            DeterministicLayer::new(),
            AgentLayer::new(),
            config.max_fix_retries,
        );
        Ok(Self {
            config,
            proof_builder,
            orchestrator,
        })
    }

    pub fn proof_builder(&self) -> ProofBuilder {
        self.proof_builder.clone()
    }

    pub fn orchestrator_mut(&mut self) -> &mut OrchestratingAgent {
        &mut self.orchestrator
    }

    pub fn run(&mut self, event: PipelineEvent, context: &str) -> Option<String> {
        self.orchestrator.emit(event, context, HashMap::new())
    }

    pub fn verify_proof(&self, proof: &ReasoningProof) -> bool {
        ProofVerifier::new(self.config.secret_key.clone()).verify(proof)
    }

    pub fn get_audit_log(&self) -> Vec<AuditEntry> {
        self.orchestrator.get_audit_log()
    }
}

/// Agent-Continuous Deployment Pipeline.
pub struct ACDPipeline {
    aci: ACIPipeline,
    deployment_proofs: Vec<ReasoningProof>,
}

impl ACDPipeline {
    pub fn new(config: PipelineConfig) -> Result<Self, String> {
        Ok(Self {
            aci: ACIPipeline::new(config)?,
            deployment_proofs: Vec::new(),
        })
    }

    pub fn request_deployment(
        &mut self,
        context: &str,
        environment: &str,
    ) -> Result<DeploymentDecision, String> {
        let mut chain = ReasoningChain::new(self.aci.proof_builder(), None);
        chain.step(
            format!("Deployment requested to {environment}: {context}"),
            format!(
                "Deterministic gates: all passed. Agent confidence: high. Target environment: {environment}."
            ),
            "Deterministic gates passed. Agent approves staging deployment. Production requires explicit human approval per CONSTITUTION.md.",
            None,
        );
        let proof = chain.seal(Some(value_map(&[
            ("environment", Value::String(environment.to_string())),
            ("context", Value::String(context.to_string())),
        ])))?;
        self.deployment_proofs.push(proof.clone());
        let needs_approval =
            environment.eq_ignore_ascii_case("production") && self.aci.config.require_human_approval;
        Ok(DeploymentDecision {
            approved: !needs_approval,
            requires_human_approval: needs_approval,
            proof,
            message: if needs_approval {
                "Production deployment requires human approval. Reasoning proof generated and awaiting sign-off. See CONSTITUTION.md §3.".to_string()
            } else {
                format!("Deployment to {environment} approved by agent.")
            },
        })
    }

    pub fn get_deployment_proofs(&self) -> Vec<ReasoningProof> {
        self.deployment_proofs.clone()
    }

    pub fn run(&mut self, event: PipelineEvent, context: &str) -> Option<String> {
        self.aci.run(event, context)
    }
}

/// ACD deployment request result.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DeploymentDecision {
    pub approved: bool,
    pub requires_human_approval: bool,
    pub proof: ReasoningProof,
    pub message: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    const EVIDENCE_SECRET: &[u8] = b"evidence-secret";
    const NOW: f64 = 1_700_000_100.0;

    fn validators() -> HashMap<String, Vec<u8>> {
        HashMap::from([
            ("validator-a".to_string(), b"validator-a-secret".to_vec()),
            ("validator-b".to_string(), b"validator-b-secret".to_vec()),
            ("validator-c".to_string(), b"validator-c-secret".to_vec()),
        ])
    }

    fn request(environment: &str) -> DeploymentRequest {
        DeploymentRequest {
            deployment_id: "deploy-123".to_string(),
            service: "checkout".to_string(),
            environment: environment.to_string(),
            commit_sha: "abc123".to_string(),
            artifact_hash: "sha256:artifact".to_string(),
            requested_by: "agent:planner".to_string(),
        }
    }

    fn policy(environment: &str) -> DeploymentPolicy {
        let mut policy = DeploymentPolicy::new(
            "checkout-prod",
            1,
            environment,
            vec![
                PolicyPredicate::new("test_passed", json!({"suite": "unit"})),
                PolicyPredicate::new(
                    "vuln_count",
                    json!({"severity": "critical", "operator": "<=", "threshold": 0}),
                ),
                PolicyPredicate::new("environment_matches", json!({"target": environment})),
                PolicyPredicate::new("rollback_defined", json!({"service": "checkout"})),
                PolicyPredicate::new("human_attested", json!({"role": "release-manager"})),
            ],
        );
        policy
            .freshness_seconds
            .insert("human_attestation".to_string(), 3600.0);
        policy
            .freshness_seconds
            .insert("scan_report".to_string(), 3600.0);
        policy
    }

    fn evidence(request: &DeploymentRequest, include_scan: bool) -> EvidenceBundle {
        let mut objects = vec![
            signed_evidence(
                "commit",
                "commit_snapshot",
                json!({"deployment_id": request.deployment_id, "commit_sha": request.commit_sha}),
                "git",
                NOW,
                EVIDENCE_SECRET,
                vec![],
            )
            .unwrap(),
            signed_evidence(
                "artifact",
                "build_artifact",
                json!({"deployment_id": request.deployment_id, "artifact_hash": request.artifact_hash}),
                "builder",
                NOW,
                EVIDENCE_SECRET,
                vec!["commit".to_string()],
            )
            .unwrap(),
            signed_evidence(
                "test",
                "test_result",
                json!({"deployment_id": request.deployment_id, "suite": "unit", "passed": true}),
                "pytest",
                NOW,
                EVIDENCE_SECRET,
                vec!["artifact".to_string()],
            )
            .unwrap(),
            signed_evidence(
                "env",
                "environment_descriptor",
                json!({"deployment_id": request.deployment_id, "environment": request.environment}),
                "cluster",
                NOW,
                EVIDENCE_SECRET,
                vec![],
            )
            .unwrap(),
            signed_evidence(
                "rollback",
                "rollback_spec",
                json!({"deployment_id": request.deployment_id, "service": request.service, "rollback_plan": "restore previous image"}),
                "deploy-plan",
                NOW,
                EVIDENCE_SECRET,
                vec![],
            )
            .unwrap(),
            signed_evidence(
                "human",
                "human_attestation",
                json!({"deployment_id": request.deployment_id, "role": "release-manager", "approved": true}),
                "approvals",
                NOW,
                EVIDENCE_SECRET,
                vec![],
            )
            .unwrap(),
        ];
        if include_scan {
            objects.push(
                signed_evidence(
                    "scan",
                    "scan_report",
                    json!({"deployment_id": request.deployment_id, "vulnerabilities": {"critical": 0, "high": 1}}),
                    "scanner",
                    NOW,
                    EVIDENCE_SECRET,
                    vec!["artifact".to_string()],
                )
                .unwrap(),
            );
        }
        EvidenceBundle::new(objects)
    }

    fn proof(request: &DeploymentRequest) -> ProofDerivation {
        ProofDerivation {
            final_conclusion: format!("deploy_authorized:{}", request.deployment_id),
            steps: vec![
                ProofStep::new("test-pass", "TEST_PASS", "test_passed:unit", vec!["test".to_string()], vec![]),
                ProofStep::new("scan-ok", "VULN_OK", "vuln_count:critical<=0", vec!["scan".to_string()], vec![]),
                ProofStep::new("env-ok", "ENVIRONMENT_MATCH", "environment_matches", vec!["env".to_string()], vec![]),
                ProofStep::new("rollback-ok", "ROLLBACK_READY", "rollback_defined", vec!["rollback".to_string()], vec![]),
                ProofStep::new("human-ok", "HUMAN_ATTESTED", "human_attested:release-manager", vec!["human".to_string()], vec![]),
                ProofStep::new(
                    "policy",
                    "POLICY_SATISFIED",
                    "policy_satisfied",
                    vec![],
                    vec![
                        "test-pass".to_string(),
                        "scan-ok".to_string(),
                        "env-ok".to_string(),
                        "rollback-ok".to_string(),
                        "human-ok".to_string(),
                    ],
                ),
                ProofStep::new(
                    "deploy",
                    "DEPLOY_AUTH",
                    format!("deploy_authorized:{}", request.deployment_id),
                    vec![],
                    vec!["policy".to_string()],
                ),
            ],
        }
    }

    fn certificate(include_scan: bool) -> DeploymentCertificate {
        let request = request("production");
        DeploymentCertificate {
            policy: policy("production"),
            evidence: evidence(&request, include_scan),
            proof: proof(&request),
            request,
            attestations: vec![],
        }
    }

    #[test]
    fn valid_certificate_requires_all_four_acceptance_terms() {
        let mut certificate = certificate(true);
        let validator_keys = validators();
        let validator_checker = CertificateChecker::new(EVIDENCE_SECRET.to_vec()).with_now(NOW);
        certificate.attestations =
            simulate_validators(&certificate, &validator_checker, &validator_keys, NOW, None);
        let checker = CertificateChecker::new(EVIDENCE_SECRET.to_vec())
            .with_validators(validator_keys)
            .with_now(NOW);
        let report = checker.accept(&certificate);
        assert!(report.accepted);
        assert!(report.wf_policy);
        assert!(report.auth_evidence);
        assert!(report.check_proof);
        assert!(report.quorum);
    }

    #[test]
    fn missing_scan_evidence_is_structured_rejection() {
        let certificate = certificate(false);
        let checker = CertificateChecker::new(EVIDENCE_SECRET.to_vec())
            .with_validators(validators())
            .with_now(NOW);
        let report = checker.accept(&certificate);
        let codes: BTreeSet<_> = report.failures.iter().map(|failure| failure.code.as_str()).collect();
        assert!(!report.accepted);
        assert!(codes.contains("EVIDENCE_REQUIRED_MISSING:scan_report"));
        assert!(codes.contains("VRP_EVIDENCE_REF_MISSING:scan-ok:scan"));
    }

    #[test]
    fn canonical_evidence_root_is_order_independent() {
        let certificate = certificate(true);
        let mut reversed = certificate.evidence.objects.clone();
        reversed.reverse();
        assert_eq!(certificate.evidence.root(), EvidenceBundle::new(reversed).root());
    }

    #[test]
    fn proof_builder_and_verifier_detect_tampering() {
        let builder = ProofBuilder::new(b"secret".to_vec(), "test-model").unwrap();
        let proof = builder
            .build(
                vec![ReasoningStep {
                    step_id: 0,
                    context: "ctx".to_string(),
                    reasoning: "rsn".to_string(),
                    conclusion: "ok".to_string(),
                    timestamp: 1_700_000_000.0,
                    step_hash: String::new(),
                }],
                Some("chain".to_string()),
                None,
            )
            .unwrap();
        assert!(ProofVerifier::new(b"secret".to_vec()).verify(&proof));
        let mut tampered = proof;
        tampered.steps[0].conclusion = "bad".to_string();
        assert!(!ProofVerifier::new(b"secret".to_vec()).verify(&tampered));
    }

    #[test]
    fn avm_trace_can_emit_signed_evidence_bundle() {
        let trace = DeploymentTrace {
            deployment_id: "deploy-123".to_string(),
            actions: vec![],
            tools: vec![ToolTrace {
                tool_name: "pytest".to_string(),
                output_type: "test_result".to_string(),
                output: value_map(&[
                    ("suite", Value::String("unit".to_string())),
                    ("passed", Value::Bool(true)),
                ]),
                timestamp: NOW,
                source: "avm".to_string(),
                dependencies: vec![],
            }],
        };
        let bundle = trace.to_evidence_bundle(EVIDENCE_SECRET).unwrap();
        assert_eq!(bundle.objects[0].evidence_type, "test_result");
        assert!(bundle.objects[0].verify(EVIDENCE_SECRET));
    }

    #[test]
    fn agent_result_surfaces_proof_metadata() {
        let builder = ProofBuilder::new(b"agent-secret".to_vec(), "agent-test").unwrap();
        let gate = AgentGate::new(
            "reviewer",
            |context, chain, _kwargs| {
                chain.step(context, "Looks fine.", "Approve.", Some(NOW));
                (AgentDecision::Approve, "All good.".to_string())
            },
            builder,
        );
        let result = gate.run("PR #1", &HashMap::new()).unwrap();
        assert_eq!(result.metadata.get("agent").and_then(Value::as_str), Some("reviewer"));
        assert_eq!(result.proof.metadata, result.metadata);
    }

    #[test]
    fn orchestrator_supports_custom_event_handlers() {
        let mut orchestrator =
            OrchestratingAgent::new(DeterministicLayer::new(), AgentLayer::new(), 3);
        orchestrator.on(PipelineEvent::StagingHealthy, |_context, _kwargs| {
            "staging_ok".to_string()
        });
        let result = orchestrator.emit(PipelineEvent::StagingHealthy, "ctx", HashMap::new());
        assert_eq!(result.as_deref(), Some("staging_ok"));
    }

    #[test]
    fn pipeline_request_deployment_produces_verifiable_proof() {
        let mut config = PipelineConfig::new("test-pipeline", b"pipeline-secret".to_vec());
        config.model_id = "test-v1".to_string();
        let mut pipeline = ACDPipeline::new(config.clone()).unwrap();
        let decision = pipeline
            .request_deployment("deploy v1.2", "production")
            .unwrap();
        assert!(!decision.approved);
        assert!(decision.requires_human_approval);
        assert!(decision.message.contains("CONSTITUTION"));
        assert!(ProofVerifier::new(config.secret_key).verify(&decision.proof));
    }
}

# Kubernetes Integration

## Overview

The MaatProof Kubernetes Admission Controller is a Rust-based validating webhook that intercepts `kubectl apply` operations and validates them against on-chain MaatProof deployment policies before allowing the Kubernetes API server to process them. Any deployment without a valid MaatProof finalization record is rejected at the cluster level.

**Implementation**: Rust (admission webhook server)  
**Type**: Kubernetes `ValidatingAdmissionWebhook`  
**Works on**: AKS (Azure), EKS (AWS), GKE (GCP)  

---

## Admission Flow

```mermaid
flowchart TD
    DEV["Developer / CI System\nkubectl apply -f deployment.yaml"]
    K8S["Kubernetes API Server"]
    WEBHOOK["MaatProof Admission Controller\n(Rust webhook server)"]
    CHAIN["MaatProof Chain"]
    IPFS["IPFS\n(trace retrieval)"]

    DEV -->|"kubectl apply"| K8S
    K8S -->|"AdmissionReview\n(webhook call)"| WEBHOOK

    WEBHOOK -->|"Extract image hash\nfrom deployment spec"| WEBHOOK
    WEBHOOK -->|"Query: is this artifact\nfinalized on-chain?"| CHAIN

    CHAIN -->|"Finalized block record\n(or: not found)"| WEBHOOK

    WEBHOOK -->|"Policy version\ncheck"| WEBHOOK

    alt Finalized + policy valid
        WEBHOOK -->|"AdmissionResponse: allowed=true"| K8S
        K8S -->|"Deploy proceeds"| DEV
    else Not finalized / policy mismatch
        WEBHOOK -->|"AdmissionResponse: allowed=false\nmessage: reason"| K8S
        K8S -->|"kubectl: error from server\nDeployment blocked by MaatProof"| DEV
    end
```

---

## Rust Admission Controller

```rust
use axum::{extract::Json, routing::post, Router};
use serde::{Deserialize, Serialize};
use tokio::net::TcpListener;

#[derive(Deserialize)]
struct AdmissionReview {
    request: AdmissionRequest,
}

#[derive(Deserialize)]
struct AdmissionRequest {
    uid:    String,
    object: serde_json::Value,  // Kubernetes resource object
}

#[derive(Serialize)]
struct AdmissionResponse {
    uid:     String,
    allowed: bool,
    status:  Option<AdmissionStatus>,
}

#[derive(Serialize)]
struct AdmissionStatus {
    code:    u16,
    message: String,
}

async fn validate(
    Json(review): Json<AdmissionReview>,
    chain_client: &MaatChainClient,
) -> Json<serde_json::Value> {
    let req = &review.request;

    // Extract container image hashes from the deployment spec
    let images = extract_images(&req.object);
    let mut allowed = true;
    let mut message = String::new();

    for image in &images {
        let image_hash = format!("sha256:{}", sha256_of_image(image));

        match chain_client.get_finalization_record(&image_hash).await {
            Some(record) => {
                // Verify the record is recent and policy version is current
                if !is_record_valid(&record) {
                    allowed = false;
                    message = format!(
                        "Image {} has an expired or invalid MaatProof record",
                        image
                    );
                    break;
                }
            }
            None => {
                allowed = false;
                message = format!(
                    "Image {} has no MaatProof finalization record. \
                     Submit a deployment proposal first.",
                    image
                );
                break;
            }
        }
    }

    let response = AdmissionResponse {
        uid: req.uid.clone(),
        allowed,
        status: if !allowed {
            Some(AdmissionStatus { code: 403, message })
        } else {
            None
        },
    };

    Json(serde_json::json!({
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": response,
    }))
}

#[tokio::main]
async fn main() {
    let chain_client = MaatChainClient::from_env();
    let app = Router::new()
        .route("/validate", post(|body| validate(body, &chain_client)));

    let listener = TcpListener::bind("0.0.0.0:8443").await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
```

---

## Kubernetes Deployment

### Webhook Configuration

```yaml
# maat-admission-webhook.yaml
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingWebhookConfiguration
metadata:
  name: maat-admission-controller
webhooks:
  - name: validate.deploy.maatproof.dev
    clientConfig:
      service:
        name: maat-admission-controller
        namespace: maat-system
        path: /validate
      caBundle: <base64-encoded-ca-cert>
    rules:
      - apiGroups: ["apps"]
        apiVersions: ["v1"]
        operations: ["CREATE", "UPDATE"]
        resources: ["deployments", "statefulsets", "daemonsets"]
    failurePolicy: Fail     # Block deployments if controller is unreachable
    admissionReviewVersions: ["v1"]
    sideEffects: None
    namespaceSelector:
      matchLabels:
        maat-enforced: "true"  # Only enforce in labeled namespaces
```

### Controller Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: maat-admission-controller
  namespace: maat-system
spec:
  replicas: 3   # HA: always run 3 replicas
  template:
    spec:
      containers:
        - name: controller
          image: maatproof/admission-controller:latest
          ports:
            - containerPort: 8443
          env:
            - name: MAAT_API_URL
              value: "https://api.maatproof.dev"
          volumeMounts:
            - name: tls-certs
              mountPath: /etc/tls
              readOnly: true
      volumes:
        - name: tls-certs
          secret:
            secretName: maat-admission-controller-tls
```

---

## Multi-Cloud Notes

| Cloud | Kubernetes | Notes |
|---|---|---|
| **Azure** | AKS | Use Azure Key Vault provider for TLS cert storage |
| **AWS** | EKS | Use AWS ACM / Secrets Manager for TLS cert storage |
| **GCP** | GKE | Use GCP Secret Manager for TLS cert storage |

The admission controller binary is cloud-agnostic — only the secret/cert storage backend differs per cloud.

---

## Namespace Enforcement

Admission control is opt-in per namespace via label:

```bash
# Enable MaatProof enforcement for the production namespace
kubectl label namespace production maat-enforced=true

# Verify enforcement
kubectl get namespace production --show-labels
```

This allows gradual rollout: enforce production first, then staging, then development.

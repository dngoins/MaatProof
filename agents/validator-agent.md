# Validator Agent — Specification

## Overview

Validator Agents are the attestation backbone of MaatProof. They receive deployment proposals, replay traces in sandboxed AVM instances, check policy compliance, submit attestation votes to PoD consensus, and earn $MAAT rewards for honest participation.

**Implementation**: Rust (AVM + consensus client) + Node.js (management API)  
**Staking requirement**: Minimum 100,000 $MAAT  
**Deployment**: Multi-cloud (AKS / EKS / GKE)  

---

## Responsibilities

| Responsibility | Description |
|---|---|
| **Receive Proposals** | Listen for deployment proposals from PoD consensus mempool |
| **Replay Traces** | Re-execute agent reasoning traces in WASM-sandboxed AVM |
| **Policy Check** | Query on-chain Deployment Contract and evaluate rules |
| **Vote** | Submit signed `FINALIZE` or `REJECT` vote within round timeout |
| **Earn Rewards** | Collect $MAAT block rewards for honest, timely attestation |
| **Stake Management** | Monitor stake balance; top up if approaching slash thresholds |

---

## Validator Lifecycle

```mermaid
stateDiagram-v2
    [*] --> UNREGISTERED

    UNREGISTERED --> REGISTERING : Stake 100,000+ $MAAT\n+ register DID + pubkey

    REGISTERING --> INACTIVE : Registration tx pending

    INACTIVE --> ACTIVE : Epoch boundary;\nstake confirmed on-chain

    ACTIVE --> RECEIVING : New deployment\nproposal received

    RECEIVING --> VERIFYING : Proposal distributed\nby round leader

    VERIFYING --> REPLAYING : Load trace into\nWASM sandbox

    REPLAYING --> POLICY_CHECKING : Replay successful\n(all outputs match)
    REPLAYING --> VOTE_REJECT : Replay failed\n(trace tampered)

    POLICY_CHECKING --> VOTE_FINALIZE : All policy rules pass
    POLICY_CHECKING --> VOTE_REJECT : Policy violation found

    VOTE_FINALIZE --> ACTIVE : Vote submitted;\nawaiting tally
    VOTE_REJECT --> ACTIVE : Vote submitted;\nawaiting tally

    ACTIVE --> SLASHED : Slash condition\nproven on-chain

    SLASHED --> ACTIVE : Re-stake above minimum\n(if not fully slashed)
    SLASHED --> INACTIVE : Stake fully slashed\nor below minimum

    INACTIVE --> UNREGISTERED : Voluntary exit\n(after unbonding)

    ACTIVE --> INACTIVE : Liveness failure\n(>10% missed rounds)
```

---

## Staking Requirements

| Requirement | Value |
|---|---|
| Minimum stake | 100,000 $MAAT |
| Unbonding period | 21 days |
| Liveness requirement | ≥90% round participation per epoch |
| Max validators per epoch | Protocol-governed (initial: 100) |

---

## Multi-Cloud Deployment

Validators should deploy across multiple cloud regions for resilience. Reference deployment configurations:

### Azure (AKS)

```yaml
# validator-deployment-aks.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: maat-validator
  namespace: maat
spec:
  replicas: 1
  selector:
    matchLabels:
      app: maat-validator
  template:
    spec:
      containers:
        - name: validator
          image: maatproof/validator:latest
          env:
            - name: MAAT_KEY_VAULT_URL
              value: "https://maat-validator.vault.azure.net"
            - name: MAAT_NETWORK
              value: "mainnet"
          resources:
            requests:
              cpu: "2"
              memory: "4Gi"
```

### AWS (EKS)

```yaml
# validator-deployment-eks.yaml
# Same structure; use AWS KMS for key storage
# env: MAAT_KMS_KEY_ID = arn:aws:kms:us-east-1:123456789:key/...
```

### GCP (GKE)

```yaml
# validator-deployment-gke.yaml
# Same structure; use GCP Cloud KMS for key storage
# env: MAAT_KMS_KEY_RING = projects/.../keyRings/maat-validator
```

---

## Rust Validator Core

```rust
use tokio::stream::StreamExt;

pub struct ValidatorNode {
    identity: ValidatorIdentity,
    avm: AvmSandbox,
    consensus_client: PoDConsensusClient,
    policy_client: DeployPolicyClient,
}

impl ValidatorNode {
    pub async fn run(&self) {
        let mut proposals = self.consensus_client.stream_proposals().await;

        while let Some(proposal) = proposals.next().await {
            let result = self.process_proposal(&proposal).await;

            let vote = ValidatorVote {
                round_id: proposal.round_id.clone(),
                validator_id: self.identity.did.clone(),
                decision: result,
                trace_hash: proposal.trace_hash.clone(),
                policy_result: self.last_policy_result.clone(),
                timestamp: Utc::now(),
                signature: self.identity.sign(&proposal.round_id),
            };

            self.consensus_client.submit_vote(vote).await
                .expect("Failed to submit vote");
        }
    }

    async fn process_proposal(&self, proposal: &DeploymentProposal) -> VoteDecision {
        let trace: DeploymentTrace =
            serde_json::from_slice(&proposal.trace_package)
            .expect("Invalid trace");

        let result = self.avm.verify(&trace, &self.policy_client).await;

        match result {
            VerificationResult::Complete { policy_result: PolicyResult::Pass, .. } =>
                VoteDecision::Finalize,
            VerificationResult::Complete { policy_result: PolicyResult::Fail { reason }, .. } =>
                VoteDecision::Reject { reason },
            VerificationResult::InvalidSignature =>
                VoteDecision::Reject { reason: "INVALID_SIGNATURE".into() },
            VerificationResult::ReplayMismatch { .. } =>
                VoteDecision::Reject { reason: "TRACE_TAMPERED".into() },
        }
    }
}
```

---

## Reward Collection

Rewards are credited to the validator's on-chain account at block finalization. Validators can claim rewards via:

```javascript
// Node.js management API
const rewards = await validatorManager.getPendingRewards(validatorDid);
const tx = await validatorManager.claimRewards(validatorDid);
console.log(`Claimed ${rewards.amount} $MAAT in tx ${tx.hash}`);
```

---

## Key Management

Validator signing keys must be stored in hardware-backed KMS:

| Cloud | Integration |
|---|---|
| Azure | Azure Key Vault (HSM tier); `azure-keyvault-keys` SDK |
| AWS | AWS KMS with CloudHSM; `aws-sdk-kms` |
| GCP | Cloud KMS with Cloud HSM; `google-cloud-kms` |

The validator Rust core uses a `SignerBackend` trait that abstracts the KMS provider — switching clouds requires only a configuration change.

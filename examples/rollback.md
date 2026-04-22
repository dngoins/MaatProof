# Example: Agent-Triggered Rollback

## Overview

This example demonstrates how MaatProof handles an agent-triggered rollback. The Orchestrator Agent detects a production error spike after a deployment, proposes a rollback, the rollback is verified and finalized on-chain, and the previous version is restored.

**Scenario**: `myapp:v2.4.1` was deployed successfully. Within 15 minutes, error rate spikes to 8% (threshold: 1%). Agent proposes rollback to `myapp:v2.4.0`.

---

## What Makes This Different from a Normal Deployment

A rollback is itself a **signed, on-chain deployment event** in MaatProof. The rollback:

1. References the finalized block of the version being restored (`myapp:v2.4.0`)
2. Records the reason (error metrics spike) in the trace
3. Is verified by validators like any other deployment
4. Produces its own finalized block (rollback receipt)

The rollback is **not an emergency bypass** — it is policy-governed. If the rollback policy requires human approval, that approval must be collected too.

---

## Node.js SDK: Rollback Submission

```javascript
const { MaatClient, MaatIdentity } = require('@maatproof/sdk');

const identity = MaatIdentity.fromKeyFile('./agent-key.json');
const client   = new MaatClient({ apiUrl: 'https://api.maatproof.dev', identity });

async function triggerRollback(failedDeploymentId, previousDeploymentId, metrics) {
  // Build rollback trace
  const traceBuilder = client.newTraceBuilder({
    policyRef:     process.env.MAAT_POLICY_REF,
    policyVersion: (await client.getPolicy(process.env.MAAT_POLICY_REF)).version,
    artifactHash:  await client.getDeployment(previousDeploymentId).artifact_hash,
    environment:   'production',
    deployType:    'ROLLBACK',
  });

  // Record the metric observations that triggered rollback
  traceBuilder.recordToolCall('get_production_metrics', {
    input: { deployment_id: failedDeploymentId, window_minutes: 15 },
    output: {
      error_rate:      metrics.errorRate,      // 0.08 (8%)
      p99_latency_ms:  metrics.p99Latency,     // 890
      threshold_error: 0.01,                   // 1%
      threshold_p99:   500,
    },
  });

  // Record the rollback decision
  traceBuilder.recordDecision({
    input: {
      context:      `error_rate=${metrics.errorRate} exceeds threshold=0.01`,
      failed_deploy: failedDeploymentId,
      rollback_to:   previousDeploymentId,
    },
    output: {
      decision:  'EXECUTE_ROLLBACK',
      reason:    'ERROR_RATE_SPIKE',
      confidence: 1.0,
    },
  });

  const trace = traceBuilder.build();

  // Submit rollback deployment
  const rollback = await client.submitDeployment(trace);
  console.log('Rollback proposal submitted:', rollback.deployment_id);

  // Wait for finalization
  const result = await client.pollDeployment(rollback.deployment_id);
  if (result.status === 'FINALIZED') {
    console.log('✅ Rollback FINALIZED at block', result.block_height);
    // Proceed to execute rollback in Kubernetes / cloud
    await executeKubernetesRollback(previousDeploymentId);
  }
}
```

---

## Sequence Diagram

```mermaid
sequenceDiagram
    participant Monitor as Production Monitor
    participant Agent as Orchestrator Agent
    participant API as MaatProof API
    participant AVM
    participant V1 as Validator 1
    participant V2 as Validator 2
    participant V3 as Validator 3
    participant Chain as MaatProof Chain
    participant K8s as Kubernetes

    Monitor->>Agent: ALERT: error_rate=8% (threshold: 1%)\ndeployment: myapp:v2.4.1

    Agent->>Agent: Decision: EXECUTE_ROLLBACK\nto myapp:v2.4.0\n(prev finalized block: 1042200)

    Agent->>Agent: Build rollback trace\n(records metrics + decision)
    Agent->>API: POST /deployments (rollback trace)
    API-->>Agent: { deployment_id: "dep-rollback-001" }

    API->>AVM: Forward rollback proposal
    AVM->>AVM: Replay trace in sandbox\n(verify metric recording is authentic)
    AVM->>Chain: Verify previous deployment\n(myapp:v2.4.0 block 1042200)
    Chain-->>AVM: Previous deployment valid
    AVM->>AVM: Evaluate rollback policy\n(no friday deploys, stake check)
    AVM->>AVM: Emit AvmAttestation (PASS)

    par Validator consensus
        AVM->>V1: Rollback proposal
        AVM->>V2: Rollback proposal
        AVM->>V3: Rollback proposal
        V1->>V1: Verify + vote FINALIZE
        V2->>V2: Verify + vote FINALIZE
        V3->>V3: Verify + vote FINALIZE
    end

    V1->>Chain: Quorum reached → Write rollback block 1042350
    Chain-->>Agent: Rollback FINALIZED (block 1042350)

    Agent->>K8s: kubectl rollout undo deployment/myapp
    K8s-->>Agent: Rollout to myapp:v2.4.0 complete

    Agent->>Monitor: Rollback executed; monitoring resumed
    Monitor->>Monitor: error_rate=0.2% ✅ (within threshold)
```

---

## Rollback Block Record

```json
{
  "block_height": 1042350,
  "deploy_type": "ROLLBACK",
  "artifact_hash": "sha256:prev-version-hash-a3f8b2c1...",
  "trace_hash": "sha256:rollback-trace-hash-def456...",
  "policy_ref": "0xDeployPolicyContractAddress",
  "policy_version": 3,
  "agent_id": "did:maat:agent:xyz789abc",
  "deploy_environment": "production",
  "rollback_from_block": 1042301,
  "rollback_reason": "ERROR_RATE_SPIKE",
  "validator_signatures": [
    { "validator": "did:maat:validator:v1", "sig": "ed25519:..." },
    { "validator": "did:maat:validator:v2", "sig": "ed25519:..." },
    { "validator": "did:maat:validator:v3", "sig": "ed25519:..." }
  ],
  "timestamp": "2025-01-15T14:55:00Z"
}
```

---

## Key Properties of MaatProof Rollbacks

| Property | Value |
|---|---|
| **Auditable** | Rollback reason and metrics recorded in trace + on-chain |
| **Policy-governed** | Same Deployment Contract rules apply to rollbacks |
| **Non-repudiable** | Agent signed the rollback decision; validators attested it |
| **Linked to original** | `rollback_from_block` links rollback record to the failed deploy |
| **Fast** | No additional human approval if rollback policy is `require_human_approval: false` |

# Proof-of-Deploy (PoD) Consensus

## Overview

Proof-of-Deploy (PoD) is MaatProof's purpose-built consensus mechanism for verifying AI agent deployments. Unlike Proof-of-Work (energy expenditure) or Proof-of-Stake (capital weight), PoD validators perform **useful work**: they replay agent reasoning traces in sandboxed AVMs and certify that a deployment is policy-compliant before it reaches production.

---

## Validator Role

Validators are staked network participants who:

1. Receive deployment proposals (trace + evidence package)
2. Replay the agent's reasoning trace in a sandboxed AVM instance
3. Evaluate the trace against the referenced Deployment Contract
4. Vote to **finalize** (approve) or **reject** the deployment
5. Earn $MAAT rewards for honest, timely attestation
6. Risk $MAAT slash for malicious or negligent attestation

---

## Round Lifecycle

Each deployment proposal progresses through a consensus round:

| Phase | Description |
|---|---|
| **Propose** | Agent submits signed deployment request + trace evidence to the mempool |
| **Distribute** | Round leader distributes proposal to the validator set |
| **Verify** | Each validator replays the trace in a local sandboxed AVM |
| **Policy Check** | Validator evaluates trace output against Deployment Contract rules |
| **Vote** | Validator submits signed vote: `FINALIZE` or `REJECT` with reason |
| **Tally** | Round leader tallies votes; 2/3 supermajority required |
| **Finalize/Reject** | Block is written (finalize) or proposal is discarded with rejection record |

### Sequence Diagram

```mermaid
sequenceDiagram
    participant Agent
    participant Mempool
    participant Leader as Round Leader
    participant V1 as Validator 1
    participant V2 as Validator 2
    participant V3 as Validator 3
    participant Chain as MaatProof Chain

    Agent->>Mempool: Submit signed deployment request + trace
    Mempool->>Leader: Forward proposal
    Leader->>V1: Distribute proposal package
    Leader->>V2: Distribute proposal package
    Leader->>V3: Distribute proposal package

    par Parallel verification
        V1->>V1: Replay trace in sandboxed AVM
        V1->>V1: Evaluate policy rules
        V2->>V2: Replay trace in sandboxed AVM
        V2->>V2: Evaluate policy rules
        V3->>V3: Replay trace in sandboxed AVM
        V3->>V3: Evaluate policy rules
    end

    V1->>Leader: Signed vote: FINALIZE
    V2->>Leader: Signed vote: FINALIZE
    V3->>Leader: Signed vote: FINALIZE

    Leader->>Leader: Tally votes (2/3 supermajority)

    alt Supermajority FINALIZE
        Leader->>Chain: Write finalized block\n(artifact hash, trace hash, policy ref,\nagent ID, validator sigs, timestamp)
        Chain->>Agent: Deployment approved — release to production
    else Supermajority REJECT
        Leader->>Chain: Write rejection record
        Chain->>Agent: Deployment rejected — reason included
    end
```

---

## Finalized Block Contents

Each finalized block stores the following as a Rust struct (serialized to JSON-LD on-chain):

```rust
/// Finalized deployment block written to the MaatProof chain.
#[derive(Serialize, Deserialize)]
pub struct MaatBlock {
    pub block_height:           u64,
    pub artifact_hash:          [u8; 32],        // SHA-256 of deployment artifact
    pub reasoning_root:         [u8; 32],        // VRP Merkle root
    pub trace_hash:             [u8; 32],        // SHA-256 of full JSON-LD trace
    pub policy_ref:             [u8; 20],        // On-chain DeployPolicy address
    pub policy_version:         u64,
    pub agent_id:               String,          // did:maat:agent:<hex>
    pub prompt_bundle_hash:     [u8; 32],        // DRE PromptBundle content address
    pub committee_cert_hash:    [u8; 32],        // DRE CommitteeCertificate hash
    pub ada_authorization:      AdaAuthorization, // ADA 7-condition record
    pub validator_signatures:   Vec<ValidatorSig>,
    pub timestamp:              u64,             // Unix milliseconds
    pub deploy_environment:     String,
    pub human_approval_ref:     Option<[u8; 32]>, // Present only if policy requires it
    pub runtime_guard_hash:     [u8; 32],        // Rollback manifest hash
}

#[derive(Serialize, Deserialize)]
pub struct ValidatorSig {
    pub validator_did: String,
    pub signature:     [u8; 64],  // Ed25519 over block_hash
    pub stake_weight:  u64,
}
```

The `human_approval_ref` field is **optional** — present only when the Deployment Contract
declares a `require_human_approval` policy rule. When ADA is the sole authority, the
`ada_authorization` record carries full accountability.

---

## Economic Model

### Validator Rewards

Validators earn $MAAT for every finalized block in which their attestation is included. Reward formula:

```
block_reward = BASE_BLOCK_REWARD * (validator_stake / total_validator_stake)
```

Rewards are distributed at block finalization. Validators who are offline or miss votes receive no reward for that round.

### Slashing Conditions

| Condition | Slash Amount |
|---|---|
| Double-vote (equivocation) | 100% of validator stake |
| Attesting a provably invalid trace | 50% of validator stake |
| Colluding to approve policy-violating deployment | 100% of validator stake |
| Chronic liveness failure (>10% missed rounds) | 5% of validator stake |

Slashed funds are distributed: 50% burned (deflationary), 25% to the whistleblower/reporter, 25% to the DAO treasury.

### Staking Requirements

| Role | Minimum Stake |
|---|---|
| Validator | 100,000 $MAAT |
| Agent (basic deploy) | 1,000 $MAAT |
| Agent (production deploy) | 10,000 $MAAT |

---

## Quorum & Finality

- **Validator set size**: configurable per deployment environment (minimum 4 validators)
- **Quorum**: 2/3 supermajority of active validators must vote `FINALIZE`
- **Finality**: Byzantine fault tolerant — tolerates up to 1/3 malicious validators
- **Round timeout**: 30 seconds per phase; proposal discarded if timeout exceeded
- **Deterministic finality**: once 2/3 supermajority is reached, the block is final — no forks

---

## Policy Enforcement

Validators do not make subjective deployment decisions. They verify objective, deterministic policy rules encoded in Deployment Contracts:

1. **Trace replay matches recorded output** — deterministic AVM re-execution
2. **All policy rules evaluate to `true`** — checked against the on-chain contract
3. **Agent identity is valid and sufficiently staked** — verified against on-chain identity
4. **ADA authorization present** — signed `AdaAuthorization` with all 7 conditions verified; OR signed `HumanApproval` if the policy requires it
5. **VRP reasoning root verified** — Merkle root recomputed and matches block

A single failed check causes the validator to vote `REJECT`.

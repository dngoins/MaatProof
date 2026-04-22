# Proof-of-Deploy Consensus — Technical Specification

## Overview

This document specifies the PoD consensus protocol in detail: validator set management, round lifecycle state machine, quorum computation, finality guarantees, and economic incentives.

**Implementation language**: Rust (consensus engine)  
**Transport**: gRPC  
**Finality**: Deterministic (BFT)  
**Quorum**: 2/3 supermajority by stake weight  

---

## Validator Set

### Validator Registration

Validators register on-chain by:
1. Staking a minimum of 100,000 $MAAT
2. Registering their node DID and Ed25519 public key
3. Passing a liveness check (must be online and responsive)

Validator set is updated at the start of each **epoch** (every 1000 blocks). Validators that go offline mid-epoch are marked as `INACTIVE` and excluded from quorum computation.

### Stake-Weighted Quorum

Quorum is computed by stake weight, not validator count:

```
finalize_threshold = total_active_stake * 2 / 3 + 1

finalize if: sum(stake[v] for v in FINALIZE_votes) >= finalize_threshold
reject    if: sum(stake[v] for v in REJECT_votes)   >= finalize_threshold
```

If neither threshold is reached within the round timeout, the proposal is discarded (not rejected — the agent may resubmit).

---

## Round Lifecycle

Each consensus round processes one deployment proposal.

### State Diagram

```mermaid
stateDiagram-v2
    [*] --> IDLE

    IDLE --> PROPOSED : Agent submits deployment request + trace
    PROPOSED --> DISTRIBUTING : Round leader selected (VRF)
    DISTRIBUTING --> VERIFYING : Proposal distributed to all validators
    VERIFYING --> VOTING : Validators complete trace replay + policy check
    VOTING --> TALLYING : Round timeout or all votes received
    TALLYING --> FINALIZED : FINALIZE supermajority reached
    TALLYING --> REJECTED : REJECT supermajority reached
    TALLYING --> DISCARDED : Timeout / no supermajority
    FINALIZED --> [*]
    REJECTED --> [*]
    DISCARDED --> [*]

    VERIFYING --> VOTING : Individual validator submits vote
    note right of VERIFYING : Validators run in parallel\nTimeout: 20s
    note right of VOTING : Timeout: 10s after first vote
```

### Round Phases

| Phase | Duration | Description |
|---|---|---|
| `PROPOSED` | — | Agent submits signed trace package to mempool |
| `DISTRIBUTING` | 2s | Round leader distributes to all active validators |
| `VERIFYING` | 20s | Validators replay trace + evaluate policy in WASM sandbox |
| `VOTING` | 10s | Validators submit signed votes |
| `TALLYING` | 2s | Leader computes stake-weighted quorum |
| `FINALIZED/REJECTED` | — | Terminal state; block written or rejection recorded |

Total round duration: ~34 seconds worst case.

---

## Vote Structure

```rust
#[derive(Serialize, Deserialize, Clone, Debug)]
pub enum VoteDecision {
    Finalize,
    Reject { reason: String },
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ValidatorVote {
    pub round_id: String,           // proposal ID
    pub validator_id: String,       // DID
    pub decision: VoteDecision,
    pub trace_hash: String,         // must match proposal trace hash
    pub policy_result: PolicyResult,
    pub timestamp: DateTime<Utc>,
    pub signature: String,          // Ed25519 over vote contents
}
```

---

## Round Leader Selection

The round leader is selected deterministically using a Verifiable Random Function (VRF):

```
leader_seed = hash(epoch_seed || block_height || proposal_id)
leader      = validators[leader_seed % len(validators)]
              weighted by stake
```

This prevents leader manipulation while keeping selection verifiable by all participants.

---

## Finality Guarantees

MaatProof provides **deterministic finality** (no forks):

- Once a block is finalized, it cannot be reverted
- Byzantine fault tolerance: system tolerates up to `f < n/3` malicious validators (by stake weight)
- A malicious actor controlling >2/3 of staked validator weight could approve invalid deployments — but would be subject to 100% slash, making this economically irrational if the slash value exceeds the attacker's gain

---

## Validator Rewards

Rewards are distributed at block finalization:

```rust
pub fn compute_validator_reward(
    base_block_reward: u128,   // in $MAAT (wei)
    validator_stake: u128,
    total_active_stake: u128,
    participation_rate: f64,   // 0.0 - 1.0, recent round participation
) -> u128 {
    let stake_share = validator_stake as f64 / total_active_stake as f64;
    let reward = base_block_reward as f64 * stake_share * participation_rate;
    reward as u128
}
```

Validators who vote on the minority side (REJECT when block is finalized, or FINALIZE when rejected) receive no reward for that round.

---

## Slashing Integration

Slashing events are triggered by the `Slashing.sol` contract and processed by the consensus engine:

| Event | Trigger | Slash |
|---|---|---|
| Double-vote | Validator submits two different votes for same round | 100% stake |
| Invalid attestation | Validator finalizes a trace later proven invalid | 50% stake |
| Collusion | ≥2/3 validators approve provably-invalid deployment | 100% each |
| Liveness failure | >10% missed rounds in current epoch | 5% stake |

---

## gRPC API

```protobuf
service PoDConsensus {
  rpc SubmitProposal (DeploymentProposal) returns (ProposalAck);
  rpc SubmitVote     (ValidatorVote)      returns (VoteAck);
  rpc GetRoundStatus (RoundStatusRequest) returns (RoundStatus);
  rpc GetFinalizedBlock (BlockRequest)    returns (FinalizedBlock);
  rpc StreamRounds   (StreamRequest)      returns (stream RoundEvent);
}

message DeploymentProposal {
  string proposal_id    = 1;
  string agent_id       = 2;
  bytes  trace_package  = 3;  // serialized DeploymentTrace
  string policy_ref     = 4;
  uint32 policy_version = 5;
  string signature      = 6;
}
```

---

## Mempool Management

The MaatProof mempool holds pending deployment proposals awaiting consensus.

### Mempool Properties

| Property | Value |
|---|---|
| Max pending proposals | 10,000 |
| Proposal TTL | 5 minutes (evicted if not included in a round) |
| Ordering | Priority queue: stake-weighted + deploy-environment weight |
| Deduplication | trace_id uniqueness enforced; duplicates dropped |
| Max proposal size | 10 MB (full trace JSON-LD) |

### Priority Ordering

```
proposal_priority = (agent_stake_weight × env_weight) + time_in_mempool_bonus
```

| Environment | env_weight |
|---|---|
| Development | 0.1 |
| Staging | 1.0 |
| Production | 10.0 |

Production deployments are prioritized in consensus rounds. Time-in-mempool bonus (+0.1 per minute) prevents starvation of low-stake agents.

---

## Block Structure

Each MaatProof finalized block has the following structure:

```rust
pub struct MaatBlock {
    pub header:       BlockHeader,
    pub body:         BlockBody,
    pub finalization: BlockFinalization,
}

pub struct BlockHeader {
    pub block_height:    u64,
    pub prev_block_hash: [u8; 32],
    pub merkle_root:     [u8; 32],    // Merkle root of deployments
    pub timestamp:       i64,          // Unix timestamp (UTC)
    pub validator_set_hash: [u8; 32], // Hash of active validator set
    pub chain_id:        u32,
}

pub struct BlockBody {
    pub deployments:     Vec<FinalizedDeployment>,
    pub slashings:       Vec<SlashRecord>,
    pub key_rotations:   Vec<KeyRotationRecord>,
    pub human_approvals: Vec<HumanApprovalRecord>,
}

pub struct BlockFinalization {
    pub round_id:             String,
    pub leader_id:            String,    // DID of round leader
    pub validator_signatures: Vec<ValidatorSig>,
    pub quorum_weight:        u128,     // total stake weight that voted FINALIZE
    pub total_weight:         u128,
}
```

### Block Hash Computation

```
block_hash = sha256(
  block_height ‖ prev_block_hash ‖ merkle_root ‖ timestamp ‖ chain_id
)
```

---

## Chain Sync Protocol

New validators joining the network must sync the full chain history:

### Sync Modes

| Mode | Description | Use Case |
|---|---|---|
| **Full sync** | Download and verify all blocks from genesis | New validators |
| **Fast sync** | Download state snapshot at a trusted checkpoint; verify headers | Quick validator onboarding |
| **Light sync** | Download block headers only; verify proofs on demand | API nodes, auditors |

### Fast Sync Flow

```mermaid
sequenceDiagram
    participant NewNode as New Validator
    participant Peers as Existing Peers
    participant Chain as Chain State

    NewNode->>Peers: Request latest checkpoint (block hash + height)
    Peers-->>NewNode: Checkpoint: block 500,000 hash=0xabc...
    NewNode->>Peers: Download state snapshot at block 500,000
    Peers-->>NewNode: State snapshot (IPFS CID)
    NewNode->>NewNode: Verify snapshot hash matches checkpoint
    NewNode->>Peers: Sync block headers from 500,000 to current
    Peers-->>NewNode: Headers (batch of 1,000)
    NewNode->>NewNode: Verify each header's prev_block_hash chain
    NewNode->>NewNode: Join validator set; participate in next round
```

---

## Fork Handling

MaatProof uses BFT consensus with deterministic finality. **Forks do not occur** under normal operation. However, network partition scenarios are handled:

| Scenario | Behavior |
|---|---|
| Network partition < 1/3 validators isolated | Remaining 2/3 continue producing blocks; isolated validators halt |
| Network partition ≥ 1/3 validators isolated | Chain halts — no block can achieve 2/3 supermajority |
| Partition resolves | Isolated validators sync from the canonical chain; their missed rounds are counted for liveness slashing |
| Byzantine leader | Round times out (30s); VRF selects new leader; proposal is re-added to mempool |
```

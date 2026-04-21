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

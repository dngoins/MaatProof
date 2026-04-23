# MaatProof Roadmap

## Overview

MaatProof is developed under the assumption that **full ACD is the operating model from
the beginning**. The roadmap sequences the proof primitives needed to make autonomous
deployment safe — it does not treat autonomous production deploy as a speculative endpoint.

Sequence: canonical bundles → DRE normalization → VRP checkers → validator replay →
runtime guards → staking/slashing → ecosystem integrations → optional ZK upgrades

---

## Phase Timeline

```mermaid
gantt
    title MaatProof Development Roadmap
    dateFormat  YYYY-MM
    axisFormat  %b %Y

    section Phase 1 Genesis
    PromptBundle + EvidenceBundle (Rust)  :p1a, 2025-01, 3M
    AVM v0.1 trace recording (Rust/WASM) :p1b, 2025-01, 3M
    Basic Deployment Contracts (Solidity) :p1c, 2025-02, 2M
    Node.js SDK alpha                     :p1d, 2025-02, 2M

    section Phase 2 Attestation
    DRE: committee execution (Rust)      :p2a, 2025-03, 3M
    VRP: reasoning records + Merkle DAG  :p2b, 2025-04, 3M
    PoD validator replay consensus       :p2c, 2025-04, 3M
    $MAAT testnet token                  :p2d, 2025-05, 2M

    section Phase 3 Integration
    ADA: 7-condition authorization (Rust):p3a, 2025-06, 2M
    Runtime Guard + rollback proofs      :p3b, 2025-07, 2M
    GitHub App (Node.js)                 :p3c, 2025-07, 2M
    Kubernetes admission controller      :p3d, 2025-08, 2M

    section Phase 4 Mainnet
    Staking + slashing live (Rust+Sol)   :p4a, 2025-10, 2M
    $MAAT mainnet launch                 :p4b, 2025-10, 1M
    Dispute resolution + appeals         :p4c, 2025-11, 2M
    DAO governance launch                :p4d, 2025-11, 2M

    section Phase 5 Protocol
    ZK-proof trace verification (zkLLM)  :p5a, 2026-01, 4M
    Checker registry (on-chain, Solidity):p5b, 2026-02, 2M
    Cross-chain trace anchoring          :p5c, 2026-04, 3M
    Formal verification of contracts     :p5d, 2026-03, 3M

    section Phase 6 Ecosystem
    Deployment Contract marketplace      :p6a, 2026-07, 3M
    Multi-chain bridges                  :p6b, 2026-08, 4M
    Enterprise support tier              :p6c, 2026-09, 3M
    Ecosystem grants program             :p6d, 2026-10, 6M
```

---

## Phase Detail

### Phase 1: Genesis

**Goal**: Canonical proof primitives — PromptBundle, EvidenceBundle, AVM trace recording.

| Deliverable | Tech | Description |
|---|---|---|
| PromptBundle (content-addressed) | Rust | Canonical serialization of all deployment context |
| EvidenceBundle | Rust | Content-addressed artifact collection |
| AVM v0.1 | Rust / WASM | Trace recording and basic WASM sandbox replay |
| Basic Deployment Contracts | Solidity | Core policy rules on testnet |
| SDK Alpha | Node.js | Submit deployment requests; read attestations |

### Phase 2: Attestation

**Goal**: DRE + VRP + validator replay consensus operational on testnet.

| Deliverable | Tech | Description |
|---|---|---|
| DRE | Rust | N-of-M committee execution, DecisionTuple normalization, convergence |
| VRP | Rust | Typed reasoning records, admissible/informational split, Merkleized DAG |
| PoD Consensus | Rust / gRPC | Validator replay, stake-weighted quorum, dispute path |
| $MAAT Testnet | Solidity | Testnet token with staking and reward mechanics |

### Phase 3: Integration

**Goal**: ADA live; runtime guards; mainstream CI/CD integrations.

| Deliverable | Tech | Description |
|---|---|---|
| ADA | Rust | 7-condition autonomous production authorization |
| Runtime Guard | Rust / Node.js | Metric monitoring, auto-rollback proofs |
| GitHub App | Node.js | Push/PR events → MaatProof proposals; status back to PR |
| Kubernetes Controller | Rust | Admission webhook enforcing on-chain policy |

### Phase 4: Mainnet

**Goal**: Economic accountability live — staking, slashing, governance.

| Deliverable | Tech | Description |
|---|---|---|
| Staking + Slashing | Rust / Solidity | Live economic consequences for agents and validators |
| $MAAT Mainnet | Solidity | Token generation event; listing; staking live |
| Dispute Resolution | Rust / Solidity | Appeal path; governance vote for contested slashes |
| DAO Governance | Solidity | Token-weighted governance for protocol parameters |

### Phase 5: Protocol

**Goal**: ZK-provable reasoning — path to full ACD without a DRE committee.

| Deliverable | Tech | Description |
|---|---|---|
| ZK Trace Verification | Rust / zkSNARK | zkLLM-style proofs for reasoning package verification |
| Checker Registry | Solidity | On-chain registry of pinned WASM checker versions |
| Cross-Chain Anchoring | Rust / Solidity | Anchor MaatProof block hashes to Ethereum/other L1s |
| Formal Verification | Certora / Halmos | Verify core Solidity contracts |

### Phase 6: Ecosystem

**Goal**: Self-sustaining ecosystem with marketplace and multi-chain support.

| Deliverable | Tech | Description |
|---|---|---|
| Contract Marketplace | Node.js | Community Deployment Contract templates and library |
| Multi-Chain Bridges | Rust / Solidity | Bridge MaatProof attestations to EVM chains |
| Enterprise Support | — | SLA-backed support for enterprise deployments |
| Ecosystem Grants | DAO | DAO-funded grants for protocol tooling and integrations |

---

## Milestone Dependencies

```mermaid
flowchart LR
    G["Phase 1\nGenesis\nTestnet + AVM v0.1"]
    A["Phase 2\nAttestation\nPoD + $MAAT testnet"]
    I["Phase 3\nIntegration\nGitHub + GitLab + K8s"]
    M["Phase 4\nMainnet\n$MAAT + DAO"]
    P["Phase 5\nProtocol\nAVM v1.0 + ZK"]
    E["Phase 6\nEcosystem\nMarketplace + Bridges"]

    G --> A --> I --> M --> P --> E
```

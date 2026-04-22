# MaatProof Roadmap

## Overview

MaatProof development is organized into six phases, from initial testnet through a mature protocol ecosystem. Each phase builds on the previous, with clear milestones and deliverables.

---

## Phase Timeline

```mermaid
gantt
    title MaatProof Development Roadmap
    dateFormat  YYYY-MM
    axisFormat  %b %Y

    section Phase 1 Genesis
    Testnet launch (devnet)         :p1a, 2025-01, 2M
    AVM v0.1 (Rust, basic trace)    :p1b, 2025-01, 3M
    Basic Deployment Contracts      :p1c, 2025-02, 2M
    Developer docs + SDK alpha      :p1d, 2025-02, 2M

    section Phase 2 Attestation
    PoD consensus implementation    :p2a, 2025-03, 3M
    Validator network (permissioned):p2b, 2025-04, 3M
    $MAAT testnet token             :p2c, 2025-04, 2M
    Slashing (testnet)              :p2d, 2025-05, 2M

    section Phase 3 Integration
    GitHub App (Node.js)            :p3a, 2025-06, 2M
    GitLab CI bridge                :p3b, 2025-07, 2M
    Claude / Copilot adapters       :p3c, 2025-07, 3M
    Kubernetes admission controller :p3d, 2025-08, 2M

    section Phase 4 Mainnet
    $MAAT mainnet launch            :p4a, 2025-10, 1M
    Slashing live on mainnet        :p4b, 2025-10, 2M
    DAO governance launch           :p4c, 2025-11, 2M
    Public validator onboarding     :p4d, 2025-10, 3M

    section Phase 5 Protocol
    AVM v1.0 (full policy engine)   :p5a, 2026-01, 3M
    ZK-proof trace verification     :p5b, 2026-02, 4M
    Cross-chain trace anchoring     :p5c, 2026-04, 3M
    Formal verification of contracts:p5d, 2026-03, 3M

    section Phase 6 Ecosystem
    Deployment Contract marketplace :p6a, 2026-07, 3M
    Multi-chain bridges             :p6b, 2026-08, 4M
    Enterprise support tier         :p6c, 2026-09, 3M
    Ecosystem grants program        :p6d, 2026-10, 6M
```

---

## Phase Detail

### Phase 1: Genesis

**Goal**: Working testnet with AVM and basic deployment contracts.

| Deliverable | Description |
|---|---|
| Testnet (devnet) | Single-region testnet with 4 permissioned validators |
| AVM v0.1 | Rust-based trace recording and basic replay |
| Basic Deployment Contracts | Solidity contracts with core policy rules |
| SDK Alpha | Node.js SDK for submitting deployment requests |
| Developer docs | Core docs: overview, AVM, contracts |

### Phase 2: Attestation

**Goal**: Full PoD consensus with validator network and testnet economics.

| Deliverable | Description |
|---|---|
| PoD Consensus | Full round lifecycle: propose → verify → vote → finalize |
| Validator Network | Permissioned validator set (10-20 validators) |
| $MAAT Testnet | Testnet token with staking and reward mechanics |
| Slashing (testnet) | Basic slash conditions on testnet |

### Phase 3: Integration

**Goal**: Mainstream CI/CD systems can use MaatProof without changing workflows.

| Deliverable | Description |
|---|---|
| GitHub App | Push/PR events → MaatProof proposals; status back to PR |
| GitLab CI Bridge | Pipeline triggers → MaatProof; status reporting |
| Claude/Copilot Adapters | AI agent adapters for Anthropic Claude and GitHub Copilot |
| Kubernetes Admission Controller | kubectl-level policy enforcement |

### Phase 4: Mainnet

**Goal**: Production-ready protocol with live economics.

| Deliverable | Description |
|---|---|
| $MAAT Mainnet | Token generation event; listing; staking live |
| Slashing (mainnet) | Economic consequences for real deployments |
| DAO Governance | Token-weighted governance for protocol parameters |
| Public Validators | Permissionless validator onboarding |

### Phase 5: Protocol

**Goal**: AVM v1.0 with ZK-proof trace verification.

| Deliverable | Description |
|---|---|
| AVM v1.0 | Full policy engine, all rule types, improved performance |
| ZK-Proof Traces | zkLLM-style proofs for trace verification (privacy-preserving) |
| Cross-Chain Anchoring | Anchor MaatProof block hashes to Ethereum/other L1s |
| Formal Verification | Certora/Halmos verification of core contracts |

### Phase 6: Ecosystem

**Goal**: Self-sustaining ecosystem with marketplace and multi-chain support.

| Deliverable | Description |
|---|---|
| Contract Marketplace | Community Deployment Contract templates and library |
| Multi-Chain Bridges | Bridge MaatProof attestations to EVM chains |
| Enterprise Support | SLA-backed support for enterprise deployments |
| Ecosystem Grants | DAO-funded grants for protocol tooling and integrations |

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

# References & Related Work

MaatProof is grounded in a body of academic and industry research spanning supply-chain security, verifiable computation, consensus protocols, and AI governance. This document maps the 20 primary references from the whitepaper (*Proof-of-Deploy: A Layer 1 Blockchain for Verifiable Agent-Continuous Integration and Deployment*, Dwight Goins, FAU Computer Science) to their corresponding implementation components.

---

## Table 1 — Reference-to-Implementation Mapping

| # | Reference | Whitepaper Section | MaatProof Component |
|---|---|---|---|
| [1] | Nakamoto, S. (2008). *Bitcoin: A Peer-to-Peer Electronic Cash System* | §3.8, §6 | PoD consensus: same economic-incentive design; slashing is MaatProof's analogue of Nakamoto-style honest-majority assumption |
| [2] | Buterin, V. (2013). *Ethereum White Paper* | §2, §3.2, §6 | Deployment Contracts (Solidity, EVM-compatible); the "Ethereum for ACI/ACD" analogy |
| [3] | Szabo, N. (1997). *Formalizing and Securing Relationships on Public Networks* | §3.2 | Deployment Contracts as smart contracts encoding natural-language policy |
| [4] | López-Alt, A. et al. (2012). *On-the-Fly Multiparty Computation on the Cloud* | §3.4, §3.6 | DRE N-of-M committee — theoretical basis for multi-party computation for consensus |
| [5] | Castro, M., Liskov, B. (1999). *Practical Byzantine Fault Tolerance* | §3.6, §3.8 | PoD validator consensus: BFT with 2/3 supermajority quorum |
| [6] | Lamport, L. et al. (1982). *The Byzantine Generals Problem* | §3.6 | Formal grounding for validator committee fault-tolerance in §3.6 |
| [7] | Kwon, J., Buchman, E. (2019). *Cosmos: A Network of Distributed Ledgers* | §3.8 | Tendermint-style finality; inspiration for two-round PoD commit |
| [8] | Casper the Friendly Finality Gadget (Buterin & Griffith, 2017) | §3.8 | Slashing design (equivocation → 100% slash) analogous to Casper slashing conditions |
| [9] | Back, A. et al. (2014). *Enabling Blockchain Innovations with Pegged Sidechains* | §3.2, §4 | Deployment Contract anchoring and cross-chain bridge for $MAAT/ETH |
| [10] | in-toto: A Framework to Secure the Software Supply Chain (Torres et al., 2019) | §7.1 | Supply-chain provenance inspiration for AVM trace recording and artifact hash anchoring |
| [11] | Sigstore (Newman et al., 2022). *Sigstore: Software Signing for Everyone* | §7.1 | Artifact signing model; MaatProof extends to include reasoning-trace signing |
| [12] | CHAINIAC: Proactive Software-Update Transparency (Nikitin et al., 2017) | §7.1 | Transparent, append-only log for deployment records; basis for MaatProof's immutable block design |
| [13] | Necula, G. (1997). *Proof-Carrying Code* | §3.3 | AVM proof-carrying execution: agents carry proofs of policy compliance with their deployment requests |
| [14] | Proof-Carrying Code Completions (LLM-era, internal refs) | §3.3 | AVM trace packages as proof-carrying completions — the direct LLM-era extension of [13] |
| [15] | Reproducible Builds (Lamb & Zacchiroli, 2022) | §3.3, §3.8 | AVM deterministic replay relies on reproducible build guarantees; artifact hash is reproducibility proof |
| [16] | Kang, H. et al. (2022). *ZKLLM: Zero-Knowledge Proofs for LLM Inference* | §3.3, §3.5, §6 | Future upgrade path: ZK proofs of LLM inference to replace N-of-M committee model |
| [17] | Sun, Z. et al. (2024). *zkLLM: Zero Knowledge Proofs for Large Language Models* | §3.3, §6 | Same ZK-LLM upgrade path; enables dropping the committee model in favor of a single verified proof |
| [18] | Truebit: A Scalable Verification Solution for Blockchains (Teutsch & Reitwiessner, 2017) | §3.8 | PoD economic model: off-chain verifiable computation with on-chain dispute resolution — direct analogue to Truebit's referee mechanism |
| [19] | NIST AI Risk Management Framework (NIST AI 100-1, 2023) | §1.3, §7.4 | Regulatory compliance: Govern/Map/Measure/Manage functions mapped to MaatProof mechanisms (see `docs/07-regulatory-compliance.md`) |
| [20] | EU Artificial Intelligence Act (Regulation (EU) 2024/1689) | §1.3, §7.4 | Human oversight requirement for high-risk AI mapped to ADA 7-condition gate + optional `require_human_approval` policy rule |

---

## Supply-Chain Security Context (§7.1)

MaatProof's position relative to existing supply-chain work:

| System | What it verifies | What MaatProof adds |
|---|---|---|
| **in-toto** [10] | Build artifact provenance (who built what) | Reasoning provenance (why an agent decided to deploy) |
| **Sigstore** [11] | Artifact signing (cryptographic identity of signer) | Agent DID + stake-weighted identity; reasoning trace signing |
| **CHAINIAC** [12] | Transparent software-update log | Deployment-decision log with validator attestation + slashing |
| **Reproducible Builds** [15] | Bit-for-bit build reproducibility | Deterministic reasoning reproducibility (DRE N-of-M committee) |

---

## Verifiable Computation Context (§3.3, §3.8)

| System | Off-chain computation | On-chain verification |
|---|---|---|
| **TrueBit** [18] | Merkle-tree computation trace | Referee challenge game |
| **ZK-SNARKs / ZK-STARKs** | Arbitrary computation | Succinct proof verification |
| **zkLLM** [16,17] | LLM inference | ZK proof of model output |
| **MaatProof AVM** | Agent reasoning trace | VRP Merkle root + validator replay |
| **MaatProof DRE** | N-of-M model committee | CommitteeCertificate + two-layer PoD |

> **Note on ZK upgrade path**: The whitepaper explicitly positions zkLLM [16,17] as a future upgrade. When cryptographically verifiable, deterministic LLM inference becomes practical, the DRE committee model can be replaced by a single ZK proof — at which point the protocol's trust model becomes purely cryptographic rather than economic. This is the research frontier MaatProof is designed to grow into.

---

## AI Governance Context (§7.4)

| Framework | Requirement | MaatProof Mechanism |
|---|---|---|
| NIST AI RMF [19] | Govern / Map / Measure / Manage | Deployment Contracts, Agent DID, AVM trace, ADA gate |
| EU AI Act [20] | Human oversight, traceability, transparency | ADA 7-condition gate, optional human approval policy rule, VRP, immutable blocks |

See `docs/07-regulatory-compliance.md` for the full regulatory compliance mapping.

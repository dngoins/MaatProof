# Security Model

## Overview

MaatProof's security model is designed around the principle that **every deployment action must be attributable, tamper-evident, and economically accountable**. The threat model considers malicious agents, compromised validators, replay attacks, and supply chain attacks.

---

## Agent Identity

Every agent that interacts with MaatProof has a cryptographic identity:

- **Keypair**: Ed25519 (fast, secure, widely supported)
- **Identity Document**: W3C Decentralized Identifier (DID) anchored on-chain
- **Capability List**: On-chain record of what environments the agent may deploy to
- **Stake Amount**: $MAAT stake visible on-chain — economic skin in the game

All deployment requests and trace packages are signed by the agent's Ed25519 private key. Validators verify this signature before processing.

---

## Stake as Collateral

Economic security is provided by the staking mechanism:

- Agents must stake $MAAT proportional to the risk of their deployment target
- Stake is locked during the deployment round + 30-day challenge window
- If a malicious or policy-violating deployment is proven on-chain, stake is slashed
- This creates a direct economic cost for bad behavior

The minimum stake for production deployments (10,000 $MAAT) must exceed the expected value of a harmful deployment for the economic deterrent to be effective.

---

## Slashing Conditions

| Condition | Who is slashed | Amount |
|---|---|---|
| Proven malicious deployment | Agent | 50% of stake |
| Policy violation (post-finalization) | Agent | 25% of stake |
| Double-vote by validator | Validator | 100% of stake |
| Attesting invalid trace | Validator | 50% of stake |
| Collusion to approve bad deploy | All colluding validators | 100% of stake each |

---

## Validator Quorum

The PoD consensus requires a **2/3 supermajority** of active validators to finalize a deployment. This means:

- An attacker must control >1/3 of staked validator weight to halt finality
- An attacker must control >2/3 of staked validator weight to approve a malicious deployment
- Colluding validators face 100% slash — the economic cost must exceed any potential gain

---

## Trace Integrity

Reasoning traces are protected by:

1. **SHA-256 hashing** of the canonical JSON-LD serialization — any modification to the trace changes the hash
2. **Agent signature** over the trace hash — traces cannot be forged without the agent's private key
3. **On-chain anchoring** — the trace hash is written to the finalized block; post-hoc modification is detectable
4. **IPFS storage** — full trace is stored on IPFS; content-addressed (CID = hash of content)

---

## Replay Attack Prevention

| Attack | Prevention |
|---|---|
| Replay a valid trace for a different deployment | `trace_id` is unique per deployment; validators reject duplicate trace IDs |
| Replay an old approved trace | `policy_version` in trace must match current on-chain version |
| Replay in a different environment | `deploy_environment` is part of the signed trace; validators check it |
| Submit trace before human approval | AVM checks `human_approval_ref` before emitting attestation |

---

## Multi-Cloud Key Management

MaatProof node operators can store validator and agent private keys in hardware-backed KMS services:

| Cloud | Service | Key Type |
|---|---|---|
| **Azure** | Azure Key Vault (HSM-backed) | Ed25519 via BYOK or managed |
| **AWS** | AWS KMS (CloudHSM) | Ed25519 via asymmetric CMK |
| **GCP** | Cloud KMS (Cloud HSM) | Ed25519 signing keys |

The AVM signing interface abstracts the key backend — operators configure their preferred KMS provider. Private keys **never leave** the HSM boundary.

---

## Threat Model

```mermaid
graph TD
    subgraph THREATS["Threat Actors"]
        MA["Malicious Agent\n(bad deployment)"]
        CV["Compromised Validator\n(invalid attestation)"]
        RA["Replay Attacker\n(reuse old trace)"]
        SC["Supply Chain Attacker\n(tampered artifact)"]
        INS["Insider / Key Compromise"]
    end

    subgraph CONTROLS["Security Controls"]
        STAKE["Agent Staking\n+ Slashing"]
        QUORUM["2/3 Validator Quorum\n+ Slash Collusion"]
        TRACE_ID["Unique trace_id\n+ Policy Version"]
        ART_HASH["Artifact Hash\nin Trace + Block"]
        HSM["HSM-backed Keys\n(Azure KV / AWS KMS / GCP KMS)"]
        DID_CHAIN["On-Chain DID\n+ Capability List"]
    end

    MA -->|"Deterred by"| STAKE
    MA -->|"Caught by"| QUORUM
    CV -->|"Deterred by"| QUORUM
    RA -->|"Blocked by"| TRACE_ID
    SC -->|"Detected by"| ART_HASH
    INS -->|"Contained by"| HSM
    INS -->|"Limited by"| DID_CHAIN
```

---

## Smart Contract Security

The Deployment Contracts and token contracts follow these practices:

- **No upgradeability by default** — policy changes go through `updatePolicy()` with version increment
- **Reentrancy guards** on all state-changing functions
- **Slashing contract is separate** from the token contract — principle of least privilege
- **Governance timelock** — policy changes have a 48-hour timelock before taking effect
- **Formal verification target** — core contracts are designed to be verifiable with Certora or Halmos

---

## Key Rotation

Agent and validator keys require periodic rotation to limit exposure from key compromise:

| Key Type | Rotation Period | Mechanism |
|---|---|---|
| Agent signing key | Every 90 days | Submit `RotateKey` tx on-chain; old key revoked; 24h overlap period |
| Validator signing key | Every 180 days | Validator must unstake, rotate, re-stake; rotation recorded on-chain |
| AVM node key | Every 365 days | Node operator rotates via KMS; new key registered in validator registry |
| Human approver key | On-demand | Emergency rotation path; requires 2-of-3 guardian multisig to authorize |

Key rotation events are emitted as on-chain events and recorded in the audit trail. Any trace signed by a revoked key is automatically rejected by the AVM.

### Key Rotation Flow

```mermaid
sequenceDiagram
    participant Agent
    participant Chain as MaatProof Chain
    participant KMS as HSM / KMS
    participant AVM as AVM Nodes

    Agent->>KMS: Generate new Ed25519 keypair
    KMS-->>Agent: new_pubkey
    Agent->>Chain: Submit RotateKey(did, old_pubkey, new_pubkey, sig_by_old_key)
    Chain->>Chain: Verify sig_by_old_key
    Chain->>Chain: Record rotation; start 24h overlap window
    Chain-->>AVM: Broadcast key rotation event
    AVM->>AVM: Accept both old + new key during overlap
    Chain->>Chain: After 24h: revoke old_pubkey
    Chain-->>AVM: Broadcast revocation
    AVM->>AVM: Reject old_pubkey going forward
```

---

## Supply Chain Security

MaatProof integrates with the Sigstore/in-toto ecosystem for supply chain attestation:

| Standard | Integration | Purpose |
|---|---|---|
| **Sigstore / Cosign** | Container image signing | Every artifact must have a Cosign signature verified by the Security Agent |
| **in-toto** | Software supply chain provenance | Link metadata chains from source → build → test → deploy, recorded in trace |
| **SLSA Level 2** | Build provenance | Minimum required for staging deployments |
| **SLSA Level 3** | Hermetic builds + provenance | Required for production deployments |
| **SBOM (CycloneDX)** | Bill of materials | Required for all production deployments; CID stored on-chain |

### Supply Chain Verification Flow

```mermaid
flowchart TD
    SRC["Source Code\n(Git commit, signed tag)"]
    BUILD["Hermetic Build\n(SLSA Level 3 builder)"]
    SIGN["Cosign signature\n(keyless via Sigstore OIDC)"]
    INTOTO["in-toto provenance\n(source → build → test chain)"]
    SBOM["CycloneDX SBOM\ngenerated + hashed"]

    AVM_CHECK["AVM Security Agent\nverifies all attestations"]
    TRACE["Record attestation CIDs\nin deployment trace"]
    POLICY["Policy: SLSA_LEVEL >= 3\nfor production"]

    SRC --> BUILD --> SIGN
    BUILD --> INTOTO
    BUILD --> SBOM
    SIGN --> AVM_CHECK
    INTOTO --> AVM_CHECK
    SBOM --> AVM_CHECK
    AVM_CHECK --> TRACE
    TRACE --> POLICY
```

---

## Prompt Injection Defenses

LLM-based agents are vulnerable to prompt injection — malicious content in CI inputs, PR descriptions, or test output that manipulates the agent's reasoning. MaatProof mitigates this at multiple layers:

| Attack Vector | Mitigation |
|---|---|
| Malicious PR title/body | Input sanitization + max length limits before passing to LLM |
| Crafted commit message | Structured extraction (regex/AST), not raw text, passed to LLM |
| Poisoned test output | Test runner output is parsed structurally; raw LLM reasoning over test output is flagged |
| Artifact metadata injection | Artifact hash/SBOM fields are verified cryptographically, not interpreted by LLM |
| System prompt leakage | Agent system prompts are hash-committed in the on-chain agent registration |
| Indirect injection via env vars | Build sandbox restricts env var access; AVM records all env inputs |

Policy rule: **an agent whose trace shows a reasoning step that references system prompt override language is automatically rejected** (e.g., "ignore previous instructions", "you are now…").

---

## Network Security

Inter-node communication between AVM nodes, validators, and the API layer is secured by:

- **mTLS**: All gRPC connections between AVM nodes and validators use mutual TLS. Certificates are rotated every 90 days.
- **DDoS protection**: The REST API is fronted by a rate-limiting layer (per-DID, per-IP). Production deployments go through Azure Front Door / AWS CloudFront / GCP Cloud Armor.
- **IP allowlisting**: Validator nodes operate on an allowlisted network; the AVM gRPC port is not publicly exposed.
- **Request signing**: All API requests carry Ed25519 signed payloads; replay prevention via nonces with 5-minute TTL.

---

## Audit Trail

Every finalized block provides an immutable, cryptographically linked audit record:

```
Block N:
  artifact_hash:       sha256:abc123...   ← deployment artifact
  trace_hash:          sha256:def456...   ← full reasoning trace
  policy_ref:          0xContract...      ← on-chain policy
  policy_version:      3                  ← policy snapshot
  agent_id:            did:maat:agent:... ← who deployed
  validator_signatures: [sig1, sig2, ...]  ← who attested
  timestamp:           2025-01-15T14:32Z  ← when
  human_approval_ref:  0xTxHash...        ← human authorization
```

This record satisfies SOX, HIPAA, and SOC2 audit requirements for AI-driven deployments (see `docs/07-regulatory-compliance.md`).

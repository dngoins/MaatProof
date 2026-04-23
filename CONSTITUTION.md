# CONSTITUTION.md — MaatProof Pipeline Constitution

> *"The day LLMs have cryptographically verifiable, deterministic reasoning is
> the day you can drop the pipeline entirely."*

This document defines the invariants that govern the MaatProof ACI/ACD
pipeline.  It is the policy layer above the code — readable by humans and
enforceable by agents.

---

## §1 — Purpose

MaatProof implements the **hybrid ACI/ACD model**: an orchestrating agent
coordinates above a deterministic trust anchor.  The agent orchestrates; the
pipeline executes with signed receipts.

---

## §2 — The Deterministic Layer (Trust Anchor)

The following gates **must always run** and **cannot be bypassed** by any
agent, regardless of context:

| Gate | Rationale |
|------|-----------|
| **Lint** | Style correctness is objective; no LLM context changes it. |
| **Compile** | Code either compiles or it doesn't. |
| **Security scan** | CVEs are facts, not opinions. No agent may decide a CVE is acceptable. |
| **Artifact signing** | Every deployable artifact must be content-addressed and signed. |
| **Compliance gates** | SOC2, HIPAA, and other regulatory requirements are non-negotiable. |
| **Reproducible build** | The same source must always produce the same artifact. |

An agent **may not** short-circuit, skip, or override any gate in the
deterministic layer.  Doing so is a constitutional violation.

---

## §3 — Human Approval is a Policy Primitive

**Human approval is a policy-configurable gate, not a universal protocol mandate.**

The protocol default is the **Autonomous Deployment Authority (ADA)**: the agent proposes,
cryptographic proof authorizes, the chain records, and the runtime guard can reverse.
See [`specs/ada-spec.md`](specs/ada-spec.md) for the full 7-condition authorization model.

Human approval is available as a policy primitive for teams that need it:

```solidity
// Opt into human approval via Deployment Contract rule (not protocol mandate)
rule require_human_approval: stage == PRODUCTION && serviceClass == "CRITICAL";
```

When a `require_human_approval` rule is declared, the Human Approval Agent is invoked
as one of the ADA policy gates. Regulated workloads (SOX, HIPAA, SOC2) should declare
this rule; standard workloads may rely on ADA alone.

The agent may **not**:
- Self-authorize a deployment that fails ADA conditions.
- Override a `require_human_approval` policy gate declared in the Deployment Contract.
- Bypass the deterministic layer gates defined in §2.

---

## §4 — Cryptographic Proof Requirement

Every agent-layer decision **must** produce a
[`ReasoningProof`](maatproof/proof.py) — a signed, hash-chained artifact that
records:

1. The exact input context.
2. Every step of the reasoning chain.
3. The conclusion reached.
4. An HMAC-SHA256 signature over the chain root hash.

This answers the audit question *"Why did this deploy at 2 am?"* with a
deterministic, cryptographically verifiable answer rather than a stale log
entry.

---

## §5 — Agent Authority Limits

| Action | Permitted | Notes |
|--------|-----------|-------|
| Fix failing tests | ✅ | With proof; max 3 retries before human escalation |
| Write new tests | ✅ | With proof |
| Code review | ✅ | With proof |
| Deploy to staging | ✅ | With proof |
| Deploy to production | ❌ | Human approval required (§3) |
| Override deterministic gate | ❌ | Constitutional violation (§2) |
| Decide CVE acceptability | ❌ | Security gates are non-negotiable (§2) |
| Rollback production | ✅ | With proof; human notified immediately |

---

## §6 — Runaway Agent Prevention

To prevent infinite fix-retry loops:

- **`max_fix_retries`** defaults to `3`.  After the limit is exceeded the
  pipeline escalates to a human rather than continuing to loop.
- Each retry attempt is recorded in the audit log with its full reasoning
  proof, so the human reviewer can see exactly what the agent tried.

---

## §7 — Audit Trail

The orchestrator maintains an append-only audit log.  Every event emission and
its result is recorded as an [`AuditEntry`](maatproof/orchestrator.py) with:

- A unique entry ID.
- The event name.
- A POSIX timestamp.
- The result string.
- Any metadata forwarded with the event.

The audit log is the source of truth for compliance reviews.

---

## §8 — ADA Is the Default; Full ACD Is the Trajectory

The **Autonomous Deployment Authority (ADA)** is already the protocol default.
Production deployments are authorized by cryptographic proof — not by human approval —
when all 7 ADA conditions are satisfied (DRE quorum, VRP checkers, validator consensus,
risk score, security clearance, and runtime guard declaration).

Full ACD — dropping the deterministic deterministic layer as well — becomes possible when:

1. **LLMs have cryptographically verifiable, deterministic reasoning**: the DRE + VRP
   produce ZK-provable reasoning packages without requiring a multi-model committee.
   (See roadmap Phase 5: ZK trace verification.)

2. **Economic accountability fully replaces procedural control**: slashing conditions and
   rollback proofs provide sufficient accountability without any deterministic pre-checks.

Until condition 1 holds, the hybrid model (ADA above a deterministic trust anchor) is
the responsible default. The DRE committee and validator consensus fill the gap.

---

*Last updated: 2026-04-22*

---

## §9 — Spec-First Rule

No implementation work may begin without a user story and acceptance criteria.

- Every feature must have a GitHub Issue with a clear user story in the format: *"As a [role], I want [goal], so that [benefit]."*
- Each issue must list measurable acceptance criteria before any code is written.
- PRs that lack a linked issue with acceptance criteria must not be merged.

---

## §10 — Agents Draft, Humans Approve

AI agents generate code and documentation; humans review before merge.

- Agents may create branches, write code, open PRs, and post review scores.
- Agents may **not** approve their own PRs or merge any PR.
- Every PR requires at least one human approval before merge.
- Agent-generated artifacts must be clearly attributed (commit trailer, PR comment, or label).

---

## §11 — Small, Reversible Changes

All changes must be atomic and independently revertable.

- One function per PR — do not bundle unrelated logic changes.
- One pipeline conversion per PR — each integration is its own unit of work.
- If a PR touches more than one domain, split it.
- Every merged PR must be safely revertable without cascading failures.

---

## §12 — Traceability

Every generated artifact must map back to its origin and forward to its verification.

- Each artifact must trace to: a **user story**, **acceptance criteria**, and **tests**.
- PRs must reference the originating issue number (`Part of #N`, `Closes #N`).
- Test cases must reference the acceptance criteria they verify.
- Documentation must reference the artifacts it describes.

---

## §13 — Naming Conventions

Consistent naming across all project artifacts.

- **Azure Functions:** `Func-{Domain}-{Action}` (e.g., `Func-Inventory-Sync`)
- **Bicep files:** `{domain}-{resource}.bicep` (e.g., `inventory-function-app.bicep`)
- **GitHub Actions workflows:** `{purpose}-{scope}.yml` (e.g., `planner-agent-trigger.yml`, `pr-review-agent.yml`)
- **Branch names:** `{type}/{short-description}` (e.g., `feat/inventory-sync`, `fix/auth-token-refresh`)
- **Issues:** `[{topic}] {Deliverable}` (e.g., `[Inventory] Unit Tests`)
- **PR titles:** `{type}: {short description}` (e.g., `feat: add inventory sync function`)

### §13.1 — Cloud Resource Naming Conventions

<!-- Addresses EDGE-IaC-029, EDGE-IaC-030, EDGE-IaC-031, EDGE-IaC-032, EDGE-IaC-033 -->

All cloud infrastructure resources follow the pattern:

```
{env}-{component}-{resource-type}[-{descriptor}]
```

Where:
- `{env}` is `dev`, `stg`, or `prod` (3-char prefix — **required on all resources**)
- `{component}` is the module/service name (e.g., `dre`, `avm`, `pod`)
- `{resource-type}` is the abbreviated resource type (e.g., `kv`, `st`, `aks`)
- `{descriptor}` is optional, ≤ 8 chars, lowercase alphanumeric, for disambiguation

**Rules:**
- Environment prefix is **mandatory** on all cloud resources. Resources without an
  environment prefix are rejected by policy enforcement.
- Azure Storage Account names must be globally unique, lowercase alphanumeric, no hyphens,
  3–24 chars. Use `{env}{component}st{5-char-suffix}` (e.g., `proddresta7f3a`).
- Resource names must use only ASCII lowercase alphanumeric and hyphens (no Unicode,
  no emoji, no slashes). Branch names used in ephemeral environments must be sanitised
  before use in resource names (strip non-`[a-z0-9-]`, truncate to 8 chars, append
  4-char hash).
- Each environment (`dev`, `stg`, `prod`) must be deployed to a separate cloud
  subscription/account to prevent cross-environment naming collisions.
- Full details and per-resource type limits: see `specs/dre-infra-spec.md` §5.

---

## §14 — Definition of Done

A work item is "done" only when **all** of the following are true:

- [ ] User story and acceptance criteria exist in the linked issue
- [ ] Implementation matches acceptance criteria
- [ ] Unit tests written and passing
- [ ] Integration tests written and passing (where applicable)
- [ ] CI pipeline passes (lint, compile, security scan)
- [ ] PR review agent scores all dimensions ≥ 7
- [ ] At least one human reviewer has approved the PR
- [ ] Documentation updated (README, architecture docs, inline comments)
- [ ] Configuration defined for all target environments
- [ ] No unresolved review comments remain
- [ ] PR merged via squash merge with clean commit message

---

## §15 — Label Taxonomy

GitHub labels serve as the routing mechanism for agent and human workflows.

| Label | Purpose |
|-------|---------|
| `role:ba` | Owned by Business Analyst |
| `role:architect` | Owned by Architect |
| `role:developer` | Owned by Developer |
| `role:qa` | Owned by QA |
| `role:release` | Owned by Release Manager |
| `agent:planner` | Triggers Planner Agent |
| `use-case:outbound` | Outbound integration pattern |
| `use-case:inbound` | Inbound integration pattern |
| `review:passed` | PR passed automated review |
| `loop:complete` | All child issues for a tracking issue are closed |

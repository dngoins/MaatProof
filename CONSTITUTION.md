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

## §3 — Autonomous Deployment Authority

**Production deployments are authorized by cryptographic proof, not human approval.**

When the Deterministic Reasoning Engine (DRE) produces a consensus-backed,
logic-verified, validator-attested deployment decision, the agent is authorized
to deploy autonomously. No human is required in the loop.

The deployment authority is granted when ALL of the following are true:

1. All deterministic gates pass (§2).
2. The DRE reaches strong consensus (>= 80% model agreement).
3. Every reasoning step passes the LogicVerifier (non-LLM validation).
4. At least 3/5 independent validators attest to the proof's validity.
5. The risk score is within the acceptable threshold for the target environment.

The agent **must**:
- Produce a `DeterministicProof` with prompt hash, consensus ratio, and response hash.
- Obtain validator attestations before production deployment.
- Monitor production metrics for 15 minutes post-deploy.
- Auto-rollback if metrics degrade beyond thresholds (with a signed rollback proof).
- Stake $MAAT proportional to the deployment risk score.

The agent **must not**:
- Deploy if any deterministic gate fails (§2 — non-negotiable).
- Deploy without DRE consensus (reasoning must be reproducible).
- Deploy if the LogicVerifier rejects any reasoning step.
- Suppress rollback to protect its stake.

See [Autonomous Deployment Authority spec](specs/autonomous-deployment-authority.md)
for the full decision matrix and rollback protocol.

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

## §8 — Full ACD: The Current Model

MaatProof implements **Agent-Continuous Deployment** — the agent is the primary
workflow.  The deterministic layer acts as a trust anchor, not the primary
decision-maker.  Three systems work together to make this possible:

1. **Deterministic Reasoning Engine (DRE)**: Produces reproducible,
   consensus-backed reasoning by running the same canonical prompt on N
   independent models and requiring M-of-N agreement.
   See [DRE spec](specs/deterministic-reasoning-engine.md).

2. **Verifiable Reasoning Protocol (VRP)**: Every reasoning step is structured
   as premises + inference rule + conclusion.  A non-LLM LogicVerifier checks
   that each conclusion follows from its premises.  Independent validators
   replay and attest to the proof.
   See [VRP spec](specs/verifiable-reasoning-protocol.md).

3. **Autonomous Deployment Authority (ADA)**: Replaces human approval with a
   multi-signal scoring system.  When deterministic gates pass, DRE consensus
   is strong, logic verification succeeds, and validators attest, the agent
   deploys autonomously with a 15-minute auto-rollback window.
   See [ADA spec](specs/autonomous-deployment-authority.md).

The motto is no longer aspirational — it is the operating model:

> ***"The day LLMs have cryptographically verifiable, deterministic reasoning
> is the day you can drop the pipeline entirely."***

That day is now.  The pipeline is replaced by proofs.

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

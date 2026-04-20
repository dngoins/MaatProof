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

## §3 — Human Approval Invariant

**Human approval is always required before a production deployment.**

This is the single invariant that must never be removed from the pipeline, even
as agent capabilities improve.  The reason is not technical capability — agents
can decide — but **accountability**: a human must be in the chain so that there
is a named party responsible for every production change.

The agent may:
- Request a deployment and provide its signed reasoning proof.
- Explain *why* it believes the deployment is safe.
- Present the full audit trail of deterministic gate results.

The agent may **not**:
- Approve its own production deployment request.
- Bypass this requirement under any circumstance, including emergency fixes.

Emergency fixes follow the same path with an accelerated human-approval SLA.

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

## §8 — Future: Full ACD

Full Agent-Continuous Deployment — dropping the deterministic pipeline
entirely — becomes possible when both conditions are met:

1. **LLMs have cryptographically verifiable, deterministic reasoning**: the
   same prompt always produces the same reasoning chain, and that chain can be
   independently verified without trusting the LLM provider.

2. **The human-approval invariant is encoded in law or regulation** for the
   relevant compliance domain, making the constitutional requirement
   self-enforcing rather than policy-enforcing.

Until both conditions hold, the hybrid model described in this constitution is
the responsible default.

---

*Last updated: 2026-04-20*

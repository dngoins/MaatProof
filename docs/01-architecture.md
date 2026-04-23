# Architecture

## High-Level Flow

1. Agent proposes deployment
2. AVM executes reasoning trace
3. Validators replay and verify
4. Consensus finalizes deployment
5. Production system unlocks deploy

## Agentic AI Loop Architecture

The repository uses a label-driven agentic loop where GitHub Issues flow through a sequence of AI agents, each gated by status labels.

```mermaid
flowchart TD
    BACKLOG["📋 Nightly Backlog Cron\n(7am UTC weekdays)"] -->|creates issues| PLANNER
    MANUAL["👤 Human labels issue\nagent:planner"] --> PLANNER

    PLANNER["🗂️ Planner Agent\nDecomposes into 9 child issues"] -->|agent:spec-edge-test| SPEC
    SPEC["🔍 Spec Edge Case Tester\nGenerates 100 scenarios\nRequires ≥90% coverage"] -->|spec:passed| COST
    COST["💰 Cost Estimator\nAzure vs AWS vs GCP\nDORA metrics"] -->|cost:estimated| DEV

    DEV["⚡ Development Agent\n4 concurrent branches"]
    DEV --> CS["Claude Sonnet"]
    DEV --> CO["Claude Opus"]
    DEV --> G53["GPT 5.3 Codex"]
    DEV --> G54["GPT 5.4"]

    CS & CO & G53 & G54 -->|dev:complete| JUDGE
    JUDGE["🏆 Judging Agent\nBig O · Quality · Cost\nPerformance · Security"] -->|judge:complete| HUMAN
    HUMAN["👤 Human Selects Winner\nMerges best branch"]

    HUMAN -->|PR merged| REVIEW
    REVIEW["📝 PR Review Agent\n10-dimension scoring"] -->|review:passed| QA
    QA["🧪 QA Testing Agent\n10 comparison dimensions"] -->|qa:passed| DOCS
    DOCS["📖 Documenter Agent\nUpdates all public docs"] -->|docs:updated| RELEASE
    RELEASE["🚀 Release Agent\nSemver tag + GitHub Release"] -->|loop:complete| DONE["✅ Done"]

    ORCH["🤖 Orchestrator\nMonitors everything\nMax 15 retries"] -.->|re-triggers stalled agents| PLANNER
    ORCH -.-> DEV
    ORCH -.-> REVIEW
```

## Label-Driven State Machine

Each agent adds a status label when it completes, which gates the next agent:

```
agent:planner → agent:spec-edge-test → spec:passed
→ agent:cost-estimator → cost:estimated
→ agent:developer → dev:complete
→ agent:judge → judge:complete
→ (human merges) → review:passed
→ qa:passed → agent:documenter → docs:updated
→ agent:release → loop:complete
```

## Components

### 1. Agent
- Generates reasoning trace
- Signs identity
- Stakes $MAAT

### 2. AVM
- Executes trace
- Produces deterministic output
- Validates against policy

### 3. Validators
- Re-run trace
- Vote on validity

### 4. Chain
- Stores:
  - Trace hash
  - Artifact hash
  - Policy reference
  - Signatures

### 5. Production Gate
- Only deploys if chain approves

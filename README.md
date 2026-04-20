# MaatProof

> *"The day LLMs have cryptographically verifiable, deterministic reasoning is
> the day you can drop the pipeline entirely."*

MaatProof is a Python framework that implements the **ACI/ACD hybrid pipeline** —
an architecture where an orchestrating agent coordinates above a deterministic
trust anchor, and every agent decision is backed by a
cryptographically-signed reasoning proof.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    ORCHESTRATING AGENT                   │
│  (monitors, decides, fixes, coordinates sub-agents)     │
└────────────────┬───────────────────────────────┬────────┘
                 │                               │
    ┌────────────▼────────────┐    ┌─────────────▼──────────┐
    │  DETERMINISTIC LAYER    │    │   AGENT LAYER           │
    │  (trust anchor)         │    │  (lives above CI)       │
    │                         │    │                         │
    │  • Lint (never wrong)   │    │  • Fix failing tests    │
    │  • Compile              │    │  • Write new tests      │
    │  • Security scan        │    │  • Code review          │
    │  • Artifact signing     │    │  • Deployment decisions │
    │  • Compliance gates     │    │  • Rollback reasoning   │
    │  • Reproducible build   │    │  • Issue triage         │
    └─────────────────────────┘    └─────────────────────────┘
```

**ACI** (Agentic AI Continuous Integration) — what we have today: agents
augment CI, fixing failures and reviewing code, while the deterministic
pipeline remains the primary workflow.

**ACD** (Agent-Continuous Deployment) — the target state: the orchestrating
agent is the primary workflow, with the deterministic layer as a trust anchor.
Every deployment decision is backed by a cryptographic proof.

---

## The Cryptographic Proof

The key innovation is the `ReasoningProof` — a signed, hash-chained artifact
that answers *"Why did this deploy at 2 am?"* with a deterministic,
cryptographically verifiable answer.

Each proof records:
1. The exact input context.
2. Every step of the reasoning chain (linked via SHA-256 hash chain).
3. The conclusion reached.
4. An HMAC-SHA256 signature over the chain root hash.

```python
from maatproof import ProofBuilder, ProofVerifier, ReasoningChain

SECRET = b"your-shared-secret"

# Build a reasoning proof
proof = (
    ReasoningChain(
        builder=ProofBuilder(secret_key=SECRET, model_id="gpt-deterministic-v1")
    )
    .step(
        context="PR #42: tests failing in test_auth.py line 15",
        reasoning="The mock return value changed in the latest fixture update.",
        conclusion="Updating the mock will fix the failure.",
    )
    .step(
        context="Fix applied; re-running the test suite.",
        reasoning="All 47 tests pass; coverage unchanged at 94%.",
        conclusion="Safe to merge.",
    )
    .seal(metadata={"pr": 42, "author": "agent"})
)

# Verify it independently
assert ProofVerifier(secret_key=SECRET).verify(proof)

# Serialize for audit storage
import json
print(json.dumps(proof.to_dict(), indent=2))
```

---

## The Hybrid Pipeline

```python
from maatproof import (
    ACDPipeline, PipelineConfig, PipelineEvent,
    DeterministicGate, AgentGate, AgentDecision,
)
from maatproof.chain import ReasoningChain

config = PipelineConfig(
    name="my-pipeline",
    secret_key=b"your-shared-secret",
    model_id="gpt-deterministic-v1",
    require_human_approval=True,   # constitutional invariant
    max_fix_retries=3,
)

pipeline = ACDPipeline(config=config)

# Register deterministic gates (cannot be bypassed by agents)
pipeline.deterministic_layer.register(
    DeterministicGate(name="lint", check_fn=lambda **kw: (True, "clean"))
)
pipeline.deterministic_layer.register(
    DeterministicGate(name="security", check_fn=lambda **kw: (True, "no CVEs"))
)

# Register an agent gate (produces a signed reasoning proof)
def fix_test(context: str, chain: ReasoningChain, **kw):
    chain.step(
        context=context,
        reasoning="Identified root cause: fixture mock mismatch.",
        conclusion="Applied targeted fix to test_auth.py.",
    )
    return AgentDecision.FIX_AND_RETRY, "Mock fixture updated."

pipeline.agent_layer.register(
    AgentGate(
        name="test_fixer",
        reasoning_fn=fix_test,
        proof_builder=pipeline.proof_builder,
    )
)

# Run the pipeline
pipeline.run(PipelineEvent.CODE_PUSHED, context="PR #42 pushed")
pipeline.run(PipelineEvent.TEST_FAILED, context="test_auth.py:15", retry_count=0)

# Production deployment always requires human approval
result = pipeline.request_deployment(context="deploy v1.2", environment="production")
print(result["message"])  # "Production deployment requires human approval..."
print(pipeline.verify_proof(result["proof"]))  # True
```

---

## The One Invariant

**Human approval is always required before a production deployment.**

Not because agents can't decide — but because accountability requires a human
in the chain.  See [`CONSTITUTION.md`](CONSTITUTION.md) for the full policy.

---

## Installation

```bash
pip install maatproof          # production
pip install "maatproof[dev]"   # + pytest for development
```

## Running Tests

```bash
pytest tests/ -v
```

## Project Structure

```
maatproof/
├── proof.py              # ReasoningStep, ReasoningProof, ProofBuilder, ProofVerifier
├── chain.py              # ReasoningChain fluent builder
├── orchestrator.py       # OrchestratingAgent, PipelineEvent, AuditEntry
├── pipeline.py           # ACIPipeline, ACDPipeline, PipelineConfig
├── exceptions.py         # Custom exceptions
└── layers/
    ├── deterministic.py  # DeterministicLayer, DeterministicGate, GateResult
    └── agent.py          # AgentLayer, AgentGate, AgentResult, AgentDecision
tests/
├── test_proof.py
├── test_chain.py
├── test_deterministic.py
├── test_agent.py
├── test_orchestrator.py
└── test_pipeline.py
CONSTITUTION.md           # Pipeline policy document
```

---

## License

CC0 1.0 Universal — see [`LICENSE`](LICENSE).

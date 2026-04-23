# DRE Integration Test Specification

**Version**: 1.0
**Status**: Draft
**Issue**: #134 — [Deterministic Reasoning Engine (DRE)] Integration Tests
**Part of**: #28
**Blocked by**: #131 (Unit Tests), #122 (Configuration)
**Related Specs**:
- `specs/deterministic-reasoning-engine.md` — DRE data models
- `specs/dre-spec.md` — DRE architecture
- `specs/dre-config-spec.md` — DRE configuration
- `specs/llm-provider-spec.md` — LLM provider abstraction
- `specs/proof-chain-spec.md` — Proof chain and verification
- `docs/06-security-model.md` — Security model

<!-- Addresses EDGE-INT-009, EDGE-INT-010, EDGE-INT-024, EDGE-INT-030, EDGE-INT-031,
     EDGE-INT-033, EDGE-INT-042, EDGE-INT-045 (issue #134) -->

---

## Overview

This specification defines the integration test suite for the Deterministic Reasoning Engine
(DRE). Integration tests cover the **end-to-end DRE pipeline**: from raw prompt input through
canonical serialization, multi-model execution, response normalization, consensus evaluation,
and final `DeterministicProof` generation. Tests must confirm full reproducibility.

The integration tests are distinct from unit tests (#131) in that they:
1. Execute the **full pipeline** (not individual components in isolation).
2. Use **stubbed model endpoints** that simulate realistic multi-model committee behaviour.
3. Verify **cross-component contracts** (e.g., that a `CanonicalPrompt.prompt_hash` flows
   correctly into the final `DeterministicProof`).
4. Assert **independent verification** (third-party verifier can replay without internal state).

---

## §1 — Test Directory Structure

<!-- Addresses EDGE-INT-024 -->

```
tests/
├── __init__.py
├── test_proof.py              # unit tests (issue #131)
├── test_deterministic.py      # unit tests (issue #131)
├── test_chain.py              # unit tests (issue #131)
├── test_orchestrator.py       # unit tests (issue #131)
├── integration/
│   ├── __init__.py
│   ├── conftest.py            # shared fixtures: stub models, DRE executor
│   ├── test_dre_e2e.py        # end-to-end reproducibility tests
│   ├── test_dre_consensus.py  # STRONG / MAJORITY / NO-CONSENSUS scenarios
│   ├── test_dre_verification.py  # third-party verification tests
│   └── stubs/
│       ├── __init__.py
│       └── stub_llm.py        # StubLLMProvider implementation (see §3)
```

**Naming convention**: Integration test files are prefixed `test_dre_` and live in
`tests/integration/`. They are discovered by pytest via the standard `test_*.py` pattern.

**pytest.ini / pyproject.toml configuration**:

```ini
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "integration: end-to-end DRE integration tests (requires stub models)",
    "unit: unit tests for individual components",
    "slow: tests that take > 5 seconds",
]
```

> **Rule**: All integration tests MUST be decorated with `@pytest.mark.integration`.
> CI runs all tests (`pytest tests/ -v`); developers can filter with
> `pytest tests/ -m integration -v`.

---

## §2 — pytest-asyncio Configuration

<!-- Addresses EDGE-INT-030, EDGE-INT-042 -->

### §2.1 asyncio_mode

The DRE multi-model executor uses `asyncio` for parallel model execution. Integration
tests that test concurrent behaviour MUST be `async def` test functions.

```ini
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

With `asyncio_mode = "auto"`, pytest-asyncio automatically handles `async def` test
functions without requiring an explicit `@pytest.mark.asyncio` decorator on every test.

### §2.2 Event Loop Scope and Test Isolation

<!-- Addresses EDGE-INT-042 -->

**Each test function runs in its own event loop.** The `asyncio_mode = "auto"` setting
creates a new event loop per test by default (scope `"function"`), ensuring that:

1. One test's lingering coroutines do not contaminate another test's execution.
2. Partially-awaited tasks from a failed test do not bleed into the next test.
3. The determinism property — same prompt produces same proof — is testable across
   independent event loop instances.

```python
# conftest.py — explicit event loop scope (function scope is the safe default)
import pytest

@pytest.fixture(scope="function")
def event_loop():
    """Create a new event loop for each test function."""
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
```

> **Rule**: Integration tests MUST NOT use `scope="session"` for the event loop.
> Session-scoped event loops can mask ordering-dependent bugs in async tests.

### §2.3 Required Dependencies

```
pytest>=7.4
pytest-asyncio>=0.23
```

These are recorded in `requirements-test.txt` (or `pyproject.toml [project.optional-dependencies]`).

---

## §3 — Stub LLM Provider Contract

<!-- Addresses EDGE-INT-010, EDGE-INT-033, EDGE-INT-045 -->

### §3.1 Purpose

Integration tests MUST NOT depend on live LLM APIs in CI. Instead, they use a
`StubLLMProvider` that returns pre-configured, deterministic responses. The stub:

1. Accepts the same interface as the real `LLMProvider` (`complete()` method).
2. Returns a fixed, pre-configured response based on the `model_id`.
3. Respects the `DeterminismParams` contract — raises `ValueError` if
   `params.temperature != 0.0` or `params.top_p != 1.0`.
4. Records every call (for assertion in tests).

### §3.2 StubLLMProvider Implementation

```python
# tests/integration/stubs/stub_llm.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List
import hashlib


@dataclass
class StubCall:
    """Records one call to the stub provider."""
    model_id: str
    prompt_content: bytes  # CanonicalPrompt.content
    seed: int


@dataclass
class StubLLMProvider:
    """
    Deterministic stub LLM provider for integration tests.

    Configure ``responses`` as a dict mapping model_id → fixed response text.
    The same model_id always returns the same response (simulates temp=0 behaviour).

    <!-- Addresses EDGE-INT-010, EDGE-INT-045 -->

    Usage::

        stubs = {
            "anthropic/claude-3-5-haiku@20241022": "APPROVE: risk score 850",
            "openai/gpt-4o@2024-08-06":            "APPROVE: risk score 840",
            "mistral/mistral-large@2407":           "APPROVE: risk score 860",
        }
        provider = StubLLMProvider(responses=stubs)
        response = await provider.complete(canonical_prompt, determinism_params)
    """
    responses: Dict[str, str]   # model_id → fixed response text
    calls: List[StubCall] = field(default_factory=list)
    timeout_models: set = field(default_factory=set)  # model_ids that simulate timeout

    async def complete(
        self,
        model_id: str,
        prompt_content: bytes,
        determinism_params,
    ) -> str:
        """Return a fixed response. Raises TimeoutError for models in timeout_models.

        Args:
            model_id:          Canonical model identifier.
            prompt_content:    CanonicalPrompt.content bytes.
            determinism_params: DeterminismParams (validated; must have temp=0.0).

        Raises:
            ValueError:   If determinism_params violates the determinism contract.
            TimeoutError: If model_id is in self.timeout_models.
            KeyError:     If model_id is not in self.responses.
        """
        # Enforce determinism contract (EDGE-INT-038)
        if determinism_params.temperature != 0.0:
            raise ValueError(
                f"StubLLMProvider: temperature must be 0.0, got "
                f"{determinism_params.temperature}"
            )
        if determinism_params.top_p != 1.0:
            raise ValueError(
                f"StubLLMProvider: top_p must be 1.0, got {determinism_params.top_p}"
            )

        # Record the call (for test assertions)
        self.calls.append(StubCall(
            model_id=model_id,
            prompt_content=prompt_content,
            seed=determinism_params.seed,
        ))

        # Simulate timeout
        if model_id in self.timeout_models:
            raise TimeoutError(f"StubLLMProvider: {model_id} simulated timeout")

        if model_id not in self.responses:
            raise KeyError(
                f"StubLLMProvider: no response configured for {model_id!r}. "
                f"Available: {list(self.responses.keys())}"
            )

        return self.responses[model_id]
```

### §3.3 Standard Stub Sets for Required Scenarios

The following stub response sets MUST be defined in `conftest.py` to support the three
required consensus scenarios (acceptance criteria item 3):

```python
# tests/integration/conftest.py
import pytest
from .stubs.stub_llm import StubLLMProvider

MODEL_A = "anthropic/claude-3-5-haiku@20241022"
MODEL_B = "openai/gpt-4o@2024-08-06"
MODEL_C = "mistral/mistral-large@2407"
MODEL_D = "anthropic/claude-3-7-sonnet@20250219"
MODEL_E = "openai/gpt-4o-mini@2024-07-18"

APPROVE_RESPONSE   = "APPROVE: all policy gates pass, risk score 850, rollout=canary"
REJECT_RESPONSE    = "REJECT: CVE-2024-0001 critical severity found"
DIVERGENT_RESPONSE = "ESCALATE: insufficient evidence for decision"

@pytest.fixture
def stub_strong_consensus():
    """5/5 models agree → STRONG (ratio = 1.0)."""
    return StubLLMProvider(responses={
        MODEL_A: APPROVE_RESPONSE,
        MODEL_B: APPROVE_RESPONSE,
        MODEL_C: APPROVE_RESPONSE,
        MODEL_D: APPROVE_RESPONSE,
        MODEL_E: APPROVE_RESPONSE,
    })

@pytest.fixture
def stub_majority_consensus():
    """3/5 models agree → MAJORITY (ratio = 0.60)."""
    return StubLLMProvider(responses={
        MODEL_A: APPROVE_RESPONSE,
        MODEL_B: APPROVE_RESPONSE,
        MODEL_C: APPROVE_RESPONSE,
        MODEL_D: REJECT_RESPONSE,
        MODEL_E: DIVERGENT_RESPONSE,
    })

@pytest.fixture
def stub_no_consensus():
    """All 5 models produce different outputs → NONE (ratio = 0.20)."""
    return StubLLMProvider(responses={
        MODEL_A: "APPROVE: risk 850",
        MODEL_B: "APPROVE: risk 780",   # different text → different hash
        MODEL_C: "REJECT: cve found",
        MODEL_D: "ESCALATE: need review",
        MODEL_E: "REJECT: test coverage insufficient",
    })

@pytest.fixture
def stub_all_timeout():
    """All models time out → NONE (agreements=0, total=3)."""
    return StubLLMProvider(
        responses={MODEL_A: "", MODEL_B: "", MODEL_C: ""},
        timeout_models={MODEL_A, MODEL_B, MODEL_C},
    )
```

> **Rule**: All stub responses are plain text strings, not structured JSON.
> The normalizer and consensus engine operate on the string representation.

### §3.4 Stub vs. Real Model Tests

| Environment | Model Calls | API Keys Required | Test Marker |
|---|---|---|---|
| CI (GitHub Actions) | Stub only | None | `@pytest.mark.integration` |
| Local dev (no keys) | Stub only | None | `@pytest.mark.integration` |
| Local dev (with keys) | Real OR stub | Via `.env` | `@pytest.mark.integration @pytest.mark.real_models` |

Stub tests and real-model tests share the same test body via parametrize or
fixture injection. The CI workflow NEVER requires real API keys for the
integration test suite.

---

## §4 — CI/CD Integration

<!-- Addresses EDGE-INT-009, EDGE-INT-024, EDGE-INT-031 -->

### §4.1 Workflow Trigger

Integration tests run as part of the CI/CD workflow defined in issue #127
(`dre-ci.yml`). They trigger on:
- `push` to any `feat/**`, `fix/**`, `chore/**` branch
- `pull_request` targeting `main`

### §4.2 CI Job Definition

```yaml
# .github/workflows/dre-ci.yml (excerpt)
jobs:
  dre-integration-tests:
    name: DRE Integration Tests
    runs-on: ubuntu-latest
    timeout-minutes: 10          # acceptance criteria: < 10 min
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt -r requirements-test.txt

      - name: Run unit and integration tests
        run: python -m pytest tests/ -v
        # No API keys needed — stubs used in CI

      - name: DRE Determinism Smoke Test
        # Acceptance criteria: same canonical prompt executed twice → identical proof hashes
        run: python -m pytest tests/integration/test_dre_e2e.py::test_e2e_reproducibility -v
        # This step is intentionally run SEPARATELY so CI fails fast on the core
        # determinism invariant before running the full suite.
```

> **Rule**: The CI job MUST NOT accept real LLM API keys as required inputs.
> All integration tests in CI use the stub provider. Real-model tests are
> opt-in via the `real_models` marker and are skipped if API keys are absent.

### §4.3 Determinism Smoke Test Step

<!-- Addresses EDGE-INT-031 -->

The CI workflow MUST include a dedicated `DRE Determinism Smoke Test` step
(separate from the full `pytest` run) that:

1. Invokes the DRE pipeline with a fixed, well-known prompt.
2. Runs the pipeline **twice** in the same process invocation.
3. Asserts that `proof1.prompt_hash == proof2.prompt_hash`.
4. Asserts that `proof1.response_hash == proof2.response_hash`.
5. Asserts that `abs(proof1.consensus_ratio - proof2.consensus_ratio) <= 1e-9`.
6. Asserts that `proof1.model_ids == proof2.model_ids`.
7. Asserts that `proof1.proof_id != proof2.proof_id` (unique per execution).

The smoke test target is `tests/integration/test_dre_e2e.py::test_e2e_reproducibility`.

### §4.4 Secret Handling in CI

<!-- Addresses EDGE-INT-033 -->

If integration tests are extended to support real model calls in CI:
- API keys are stored as **GitHub Actions Secrets**.
- They are injected as environment variables: `DRE_CI_ANTHROPIC_API_KEY`, etc.
- The workflow step that uses them MUST include `env:` injection (not `echo`).
- Test output MUST NOT print the API key value; the `StubLLMProvider` call
  recorder logs only `model_id` and `seed`, never the API key.

```yaml
      - name: Run real-model integration tests (optional, key-gated)
        if: ${{ secrets.DRE_CI_ANTHROPIC_API_KEY != '' }}
        run: python -m pytest tests/integration/ -m real_models -v
        env:
          DRE_CI_ANTHROPIC_API_KEY: ${{ secrets.DRE_CI_ANTHROPIC_API_KEY }}
          DRE_CI_OPENAI_API_KEY:    ${{ secrets.DRE_CI_OPENAI_API_KEY }}
          DRE_CI_MISTRAL_API_KEY:   ${{ secrets.DRE_CI_MISTRAL_API_KEY }}
```

---

## §5 — Required Integration Test Cases

This section maps acceptance criteria from issue #134 to concrete test functions.

### §5.1 End-to-End Reproducibility Test

**Acceptance Criteria Item 1**: Same prompt executed twice → identical `DeterministicProof`
(same `prompt_hash`, `consensus_ratio`, `response_hash`, `model_ids`).

```python
# tests/integration/test_dre_e2e.py
import pytest
from dre.models import DeterministicProof

@pytest.mark.integration
async def test_e2e_reproducibility(dre_executor, stub_strong_consensus):
    """Same prompt executed twice produces identical DeterministicProof fields.

    <!-- Addresses EDGE-INT-029 (issue #134) -->
    """
    prompt = "Evaluate deployment of version 2.1.0 to production."

    proof1: DeterministicProof = await dre_executor.execute(
        prompt, stub_strong_consensus
    )
    proof2: DeterministicProof = await dre_executor.execute(
        prompt, stub_strong_consensus
    )

    # Core reproducibility invariants (acceptance criteria item 1)
    assert proof1.prompt_hash      == proof2.prompt_hash,      "prompt_hash must be identical"
    assert proof1.response_hash    == proof2.response_hash,    "response_hash must be identical"
    assert proof1.model_ids        == proof2.model_ids,        "model_ids must be identical"
    assert abs(proof1.consensus_ratio - proof2.consensus_ratio) <= 1e-9, \
        "consensus_ratio must be identical (within float tolerance)"

    # proof_id MUST differ (EDGE-INT-040: unique per execution)
    assert proof1.proof_id != proof2.proof_id, "proof_id must be unique per execution"
```

### §5.2 Third-Party Verification Test

**Acceptance Criteria Item 2**: Third-party verifier replays canonical prompt →
same consensus hash without internal state.

```python
@pytest.mark.integration
async def test_third_party_verification(dre_executor, stub_strong_consensus):
    """Third-party verifier reconstructs proof fields from public data only.

    <!-- Addresses EDGE-INT-004, EDGE-INT-025 (issue #134) -->
    """
    prompt = "Deploy service-mesh v3.2.0 to staging."

    proof: DeterministicProof = await dre_executor.execute(
        prompt, stub_strong_consensus
    )

    # Extract only the public verification package (no internal state)
    from tests.integration.verifier import ThirdPartyVerifier
    verifier = ThirdPartyVerifier(stub_strong_consensus)
    result = verifier.verify(
        prompt_str   = prompt,
        model_ids    = proof.model_ids,
        seed         = proof.determinism_params_seed,
        expected_proof = proof.to_dict(),
    )

    assert result.prompt_hash_match    is True
    assert result.response_hash_match  is True
    assert result.consensus_ratio_match is True
    assert result.model_ids_match      is True
```

### §5.3 Consensus Scenario Tests

**Acceptance Criteria Item 3**: Tests covering STRONG, MAJORITY, and NO-CONSENSUS.

```python
@pytest.mark.integration
async def test_strong_consensus(dre_executor, stub_strong_consensus):
    """5/5 agreement → STRONG consensus (ratio=1.0)."""
    from dre.models import ConsensusClassification
    proof = await dre_executor.execute("Deploy.", stub_strong_consensus)
    assert proof.consensus_ratio >= 0.80
    # Verify classification is STRONG
    from dre.models import classify_ratio
    assert classify_ratio(proof.consensus_ratio) == ConsensusClassification.STRONG


@pytest.mark.integration
async def test_majority_consensus(dre_executor, stub_majority_consensus):
    """3/5 agreement → MAJORITY consensus (ratio=0.60)."""
    from dre.models import ConsensusClassification, classify_ratio
    proof = await dre_executor.execute("Deploy.", stub_majority_consensus)
    assert 0.60 <= proof.consensus_ratio < 0.80
    assert classify_ratio(proof.consensus_ratio) == ConsensusClassification.MAJORITY


@pytest.mark.integration
async def test_no_consensus(dre_executor, stub_no_consensus):
    """All models disagree → NO-CONSENSUS; response_hash = SHA-256(b'')."""
    import hashlib
    from dre.models import ConsensusClassification, classify_ratio
    SHA256_EMPTY = hashlib.sha256(b"").hexdigest()

    proof = await dre_executor.execute("Deploy.", stub_no_consensus)
    assert proof.consensus_ratio < 0.40
    assert classify_ratio(proof.consensus_ratio) == ConsensusClassification.NONE
    assert proof.response_hash == SHA256_EMPTY, \
        "NONE consensus must produce response_hash=SHA-256(b'')"
```

### §5.4 All-Committee-Timeout Test

```python
@pytest.mark.integration
async def test_all_committee_timeout(dre_executor, stub_all_timeout):
    """All models time out → NONE consensus; proof valid; deployment blocked.

    <!-- Addresses EDGE-INT-002 (issue #134) -->
    """
    import hashlib
    from dre.models import ConsensusClassification
    SHA256_EMPTY = hashlib.sha256(b"").hexdigest()

    proof = await dre_executor.execute("Deploy.", stub_all_timeout)

    # No exception raised — valid proof with NONE consensus
    assert proof.consensus_ratio == 0.0
    assert proof.response_hash == SHA256_EMPTY
    # total = N queried (denominator policy EDGE-029)
    assert proof.agreements == 0
    assert proof.total == len(stub_all_timeout.responses)  # = N queried
```

---

## §6 — Edge Case Test Coverage Mapping

<!-- Cross-reference to issue #134 scenarios -->

| EDGE-INT ID | Test Function | File | Status |
|---|---|---|---|
| EDGE-INT-001 | `test_unicode_normalization` | `test_dre_e2e.py` | Required |
| EDGE-INT-002 | `test_all_committee_timeout` | `test_dre_e2e.py` | Required — see §5.4 |
| EDGE-INT-003 | `test_strong_consensus_boundary` | `test_dre_consensus.py` | Required |
| EDGE-INT-004 | `test_third_party_verification` | `test_dre_verification.py` | Required — see §5.2 |
| EDGE-INT-006 | `test_code_response_normalization` | `test_dre_e2e.py` | Required |
| EDGE-INT-007 | `test_majority_consensus` | `test_dre_consensus.py` | Required — see §5.3 |
| EDGE-INT-008 | `test_no_consensus` | `test_dre_consensus.py` | Required — see §5.3 |
| EDGE-INT-010 | `StubLLMProvider` | `stubs/stub_llm.py` | Required — see §3 |
| EDGE-INT-014 | `test_concurrent_runs` | `test_dre_e2e.py` | Required |
| EDGE-INT-015 | `test_partial_timeout_denominator` | `test_dre_consensus.py` | Required |
| EDGE-INT-022 | `test_weak_consensus_boundary` | `test_dre_consensus.py` | Required |
| EDGE-INT-023 | `test_code_ast_normalization` | `test_dre_e2e.py` | Required |
| EDGE-INT-029 | `test_e2e_reproducibility` | `test_dre_e2e.py` | Required — see §5.1 |
| EDGE-INT-030 | `asyncio_mode = "auto"` | `pyproject.toml` | Required — see §2.1 |
| EDGE-INT-031 | Determinism smoke test CI step | `.github/workflows/dre-ci.yml` | Required — see §4.3 |
| EDGE-INT-033 | GitHub Secrets injection | `.github/workflows/dre-ci.yml` | Required — see §4.4 |
| EDGE-INT-040 | `proof1.proof_id != proof2.proof_id` | `test_dre_e2e.py` | Required — see §5.1 |
| EDGE-INT-041 | `test_no_consensus response_hash` | `test_dre_consensus.py` | Required — see §5.3 |
| EDGE-INT-042 | `event_loop` fixture scope=function | `conftest.py` | Required — see §2.2 |
| EDGE-INT-045 | `stub_strong/majority/no_consensus` | `conftest.py` | Required — see §3.3 |

---

## §7 — Documentation Requirements

<!-- Addresses EDGE-INT-043 (issue #134) -->

The "Documentation updated" acceptance criterion (issue #134 item 6) is satisfied when
ALL of the following are updated before the PR is merged:

| Document | Required Update |
|---|---|
| `docs/03-agent-virtual-machine.md` | Add §DRE Integration Tests section referencing this spec |
| `README.md` | Add instructions for running integration tests: `pytest tests/integration/ -v` |
| `specs/dre-integration-test-spec.md` | This document (created per issue #134) |
| `specs/deterministic-reasoning-engine.md` | Add §10 Third-Party Verification Package (done in this update) |
| PR description | Include test run output showing STRONG, MAJORITY, NO-CONSENSUS scenarios passing |

---

## §8 — Validation Rules Summary

| Scenario | EDGE-INT ID | Spec Section | Status |
|---|---|---|---|
| E2E reproducibility: same prompt → same proof hashes | EDGE-INT-029 | §5.1 | ✅ Specified |
| Third-party verification without internal state | EDGE-INT-004, EDGE-INT-025 | §5.2, §10 | ✅ Specified |
| STRONG consensus test | EDGE-INT-007 | §5.3 | ✅ Specified |
| MAJORITY consensus test | EDGE-INT-007 | §5.3 | ✅ Specified |
| NO-CONSENSUS test + response_hash = SHA-256(b"") | EDGE-INT-008, EDGE-INT-041 | §5.3 | ✅ Specified |
| All models timeout → NONE; no exception raised | EDGE-INT-002 | §5.4 | ✅ Specified |
| CI workflow runs without manual intervention | EDGE-INT-009, EDGE-INT-031 | §4.2, §4.3 | ✅ Specified |
| Stub LLM provider contract | EDGE-INT-010 | §3.2 | ✅ Specified |
| pytest-asyncio mode=auto | EDGE-INT-030 | §2.1 | ✅ Specified |
| Event loop isolation per test function | EDGE-INT-042 | §2.2 | ✅ Specified |
| CI secrets never echoed in logs | EDGE-INT-033 | §4.4 | ✅ Specified |
| Min 3-model stub committee | EDGE-INT-045 | §3.3 | ✅ Specified |
| AST comparison for code responses | EDGE-INT-006, EDGE-INT-023 | §3.3 (DRE spec) | ✅ Specified |
| Documentation update checklist | EDGE-INT-043 | §7 | ✅ Specified |

---

## References

- Issue #134 — [DRE] Integration Tests
- Issue #131 — [DRE] Unit Tests
- Issue #127 — [DRE] CI/CD Workflow
- Issue #122 — [DRE] Configuration
- Issue #28  — Deterministic Reasoning Engine (parent)
- `specs/deterministic-reasoning-engine.md` — Data models (EDGE-001 through EDGE-040)
- `specs/dre-config-spec.md` — Configuration validation
- `specs/dre-spec.md` — DRE architecture (Stages 1-5)
- `specs/proof-chain-spec.md` — ProofBuilder and ProofVerifier
- `specs/vrp-cicd-spec.md` — VRP CI/CD (structural reference for CI pattern)

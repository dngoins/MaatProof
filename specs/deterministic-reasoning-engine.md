# Deterministic Reasoning Engine (DRE) — Data Models Specification

<!-- Addresses EDGE-001 through EDGE-040 (issue #30) -->
<!-- Addresses EDGE-INT-002, EDGE-INT-006, EDGE-INT-023, EDGE-INT-025 (issue #134) -->

## Overview

The Deterministic Reasoning Engine (DRE) is the Python component responsible for
producing cryptographically verifiable, consensus-based reasoning proofs.
It extends the core `ReasoningProof` primitive (defined in `maatproof/proof.py`)
with multi-model consensus semantics: the same canonical prompt is sent to **N**
independent LLM models, their outputs are normalised, M-of-N agreement is
measured, and the result is encoded in a `DeterministicProof`.

**Language**: Python 3.11+  
**Serialisation**: `dataclasses` + `json` (primary); Pydantic v2 optional  
**Hashing**: `hashlib.sha256` — SHA-256 exclusively  
**Unicode**: `unicodedata.normalize("NFC", …)` — NFC form exclusively  
**Module**: `dre.models` — all public types importable from this path  

---

## Module Structure

<!-- Addresses EDGE-034 -->

```
dre/
├── __init__.py
└── models.py          # All data model types live here
```

All four primary types plus supporting enums/helpers are importable from a single
path:

```python
from dre.models import (
    CanonicalPrompt,
    ConsensusClassification,
    ConsensusResult,
    DeterminismParams,
    DeterministicProof,
    ModelResponse,
)
```

The `dre` package is a sibling of `maatproof` in the project root.

---

## Data Models

### 3.1 `DeterminismParams`

<!-- Addresses EDGE-012, EDGE-013, EDGE-014, EDGE-038 -->

Captures the exact LLM sampling parameters that must be enforced to guarantee
deterministic (or near-deterministic) output.

```python
@dataclass
class DeterminismParams:
    """LLM sampling parameters enforced for deterministic mode.

    Attributes:
        temperature: Must be exactly 0.0. Any other value violates the
            determinism contract and the model will raise ``ValueError``.
        seed:        Non-negative integer seed passed to the LLM provider.
            Required (not optional). Valid range: [0, 2**32 - 1].
        top_p:       Must be exactly 1.0. Setting top_p < 1.0 enables nucleus
            sampling and can alter output distribution even at temperature=0.
    """
    temperature: float  # must be exactly 0.0
    seed: int           # required; range [0, 2**32 - 1]
    top_p: float        # must be exactly 1.0

    def __post_init__(self) -> None:
        if self.temperature != 0.0:
            raise ValueError(
                f"DeterminismParams.temperature must be 0.0, got {self.temperature}"
            )
        if not (0 <= self.seed <= 2**32 - 1):
            raise ValueError(
                f"DeterminismParams.seed must be in [0, 2**32-1], got {self.seed}"
            )
        if self.top_p != 1.0:
            raise ValueError(
                f"DeterminismParams.top_p must be 1.0, got {self.top_p}"
            )
```

**Validation rules:**

| Field | Constraint | Error |
|-------|-----------|-------|
| `temperature` | Must equal `0.0` exactly | `ValueError` |
| `seed` | Integer in `[0, 2^32-1]`; `None` is **not** allowed | `ValueError` |
| `top_p` | Must equal `1.0` exactly | `ValueError` |

---

### 3.2 `CanonicalPrompt`

<!-- Addresses EDGE-006, EDGE-007, EDGE-008, EDGE-022, EDGE-030, EDGE-035, EDGE-039 -->

Encapsulates a prompt that has been normalised to a canonical byte form.
Two prompts that are semantically identical (same text, modulo Unicode form and
whitespace) must produce **identical** `prompt_hash` values, regardless of which
thread or process constructs them.

```python
@dataclass
class CanonicalPrompt:
    """A normalised, content-addressed prompt.

    The normalisation pipeline is:
      1. Decode input to str (UTF-8, strict).
      2. Apply NFC Unicode normalisation (``unicodedata.normalize("NFC", s)``).
      3. Strip ASCII control characters (codepoints < 0x20, except \\t, \\n, \\r).
      4. Encode back to bytes (UTF-8).
      5. Compute SHA-256 hex digest.

    ``unicodedata.normalize`` is thread-safe in CPython and PyPy. Two concurrent
    calls with the same input always return the same result.

    Attributes:
        content:               Normalised UTF-8 bytes of the prompt.
        prompt_hash:           SHA-256 hex digest (64 lowercase hex chars) of
            ``content``.
        normalization_metadata: Dict recording the normalisation steps applied.
            Required keys: ``unicode_form`` (``"NFC"``), ``encoding``
            (``"utf-8"``), ``control_chars_stripped`` (bool).
    """
    content: bytes
    prompt_hash: str          # 64 hex chars; SHA-256 of content
    normalization_metadata: dict

    # Maximum permitted prompt size. Prompts exceeding this limit are rejected
    # at construction time to prevent unbounded memory usage and LLM token
    # overflow.  (See EDGE-008.)
    MAX_CONTENT_BYTES: ClassVar[int] = 32_768  # 32 KiB

    def __post_init__(self) -> None:
        # Size guard (EDGE-008)
        if len(self.content) > self.MAX_CONTENT_BYTES:
            raise ValueError(
                f"CanonicalPrompt.content exceeds maximum size of "
                f"{self.MAX_CONTENT_BYTES} bytes (got {len(self.content)})"
            )
        # Hash format guard (EDGE-001, EDGE-016)
        if not _is_sha256_hex(self.prompt_hash):
            raise ValueError(
                f"CanonicalPrompt.prompt_hash must be a 64-char SHA-256 hex "
                f"digest, got {self.prompt_hash!r}"
            )
        # Integrity check
        expected = hashlib.sha256(self.content).hexdigest()
        if self.prompt_hash != expected:
            raise ValueError(
                "CanonicalPrompt.prompt_hash does not match SHA-256(content)"
            )
        # Metadata keys guard
        required_keys = {"unicode_form", "encoding", "control_chars_stripped"}
        if not required_keys.issubset(self.normalization_metadata):
            raise ValueError(
                f"normalization_metadata must contain keys {required_keys}"
            )

    @classmethod
    def from_str(cls, prompt: str) -> "CanonicalPrompt":
        """Build a CanonicalPrompt from a raw string.

        Applies the full normalisation pipeline and computes the hash.
        An empty string is valid; its hash is the well-known SHA-256 of zero
        bytes (``e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855``).
        (See EDGE-007, EDGE-035.)

        The prompt is treated as an opaque string — if it happens to be valid
        JSON, it is NOT re-parsed or re-serialised.  Key sorting applies only
        to the surrounding trace JSON, not to prompt content.  (See EDGE-022.)

        Note on prompt injection: ``CanonicalPrompt`` records the prompt
        faithfully after Unicode normalisation. Injection-pattern detection and
        input sanitisation beyond control-character stripping are the
        responsibility of the AVM layer (``specs/avm-spec.md``
        §Prompt Injection Mitigations).  (See EDGE-026.)
        """
        import unicodedata
        import re
        # Step 1 + 2: NFC normalisation
        normalised = unicodedata.normalize("NFC", prompt)
        # Step 3: strip ASCII control chars (keep tab, newline, carriage return)
        normalised = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", normalised)
        content = normalised.encode("utf-8")
        if len(content) > cls.MAX_CONTENT_BYTES:
            raise ValueError(
                f"Prompt exceeds {cls.MAX_CONTENT_BYTES}-byte limit after "
                f"normalisation ({len(content)} bytes)"
            )
        prompt_hash = hashlib.sha256(content).hexdigest()
        metadata = {
            "unicode_form": "NFC",
            "encoding": "utf-8",
            "control_chars_stripped": True,
        }
        return cls(content=content, prompt_hash=prompt_hash,
                   normalization_metadata=metadata)
```

**Normalisation contract:**

| Step | Rule |
|------|------|
| Unicode form | NFC via `unicodedata.normalize("NFC", s)` — eliminates EDGE-006 |
| Control characters | Strip codepoints `< 0x20` except `\t` (0x09), `\n` (0x0A), `\r` (0x0D) — eliminates EDGE-030 |
| Encoding | UTF-8 strictly |
| Prompt content | Treated as opaque string — JSON prompts are **not** re-parsed — eliminates EDGE-022 |
| Max size | 32,768 bytes post-normalisation; raises `ValueError` if exceeded — eliminates EDGE-008 |
| Empty input | Valid — SHA-256(b"") = `e3b0c442...` — eliminates EDGE-007, EDGE-035 |
| Thread safety | `unicodedata.normalize` is reentrant in CPython and PyPy; no locking needed — eliminates EDGE-039 |

---

### 3.3 `ModelResponse`

<!-- Addresses EDGE-013, EDGE-015, EDGE-018, EDGE-025, EDGE-033 -->

Captures the complete output of a single LLM call under deterministic params.

```python
@dataclass
class ModelResponse:
    """Output from a single LLM model under deterministic sampling params.

    Attributes:
        model_id:          Canonical model identifier in the format
            ``{provider}/{model-name}@{version}``
            (e.g. ``"anthropic/claude-3-7-sonnet@20250219"``).
            Must match the format defined in ``specs/llm-provider-spec.md``
            §Model ID Canonical Format.
        raw_output:        The verbatim response text from the model.
        normalized_output: The response after whitespace normalisation and,
            for code outputs, canonicalised formatting.  Must be semantically
            equivalent to ``raw_output`` — normalisation may not change
            meaning, only formatting.  (See EDGE-018.)
        determinism_params: The exact sampling params used; temperature must
            be 0.0, seed must be set, top_p must be 1.0.
        response_hash:     SHA-256 hex digest of
            ``normalized_output.encode("utf-8")``.  An empty normalised output
            produces hash ``e3b0c44298fc1c149afbf4c8996fb924...``.
            (See EDGE-025.)
    """
    model_id: str
    raw_output: str
    normalized_output: str
    determinism_params: DeterminismParams
    response_hash: str   # SHA-256 of normalized_output bytes; 64 hex chars

    _MODEL_ID_PATTERN: ClassVar[re.Pattern] = re.compile(
        r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+$"
    )

    def __post_init__(self) -> None:
        # Model ID format validation (EDGE-020, EDGE-033)
        if not self._MODEL_ID_PATTERN.match(self.model_id):
            raise ValueError(
                f"ModelResponse.model_id must follow '{{provider}}/{{model}}@{{version}}' "
                f"format, got {self.model_id!r}"
            )
        # response_hash format (EDGE-016)
        if not _is_sha256_hex(self.response_hash):
            raise ValueError(
                "ModelResponse.response_hash must be a 64-char SHA-256 hex digest"
            )
        # response_hash integrity (EDGE-015)
        expected = hashlib.sha256(self.normalized_output.encode("utf-8")).hexdigest()
        if self.response_hash != expected:
            raise ValueError(
                "ModelResponse.response_hash does not match "
                "SHA-256(normalized_output)"
            )
```

**Normalisation scope (EDGE-018):**

The `normalized_output` field represents only *formatting* normalisation:

| Allowed | Not Allowed |
|---------|------------|
| Strip leading/trailing whitespace | Change variable names or logic |
| Collapse multiple blank lines to one | Add or remove code statements |
| Canonicalise line endings (`\r\n` → `\n`) | Alter string literals |
| Remove trailing whitespace per line | Re-order JSON keys in generated code |

Implementations **must** verify that `raw_output` and `normalized_output` are
semantically equivalent before constructing a `ModelResponse`.

#### AST Comparison for Code Responses

<!-- Addresses EDGE-INT-006, EDGE-INT-023 (issue #134) -->

When a model response contains code (Python, JSON, YAML, or other structured
text), the equivalence check between `raw_output` and `normalized_output` is
performed using **AST-level comparison**, not string equality. Two code snippets
are considered semantically equivalent for normalization purposes if and only if
their parsed abstract syntax trees are identical under Python's `ast.dump()`.

**Rules for Python code normalization:**

| Transform | Allowed | Rationale |
|-----------|---------|-----------|
| Strip trailing whitespace on each line | ✅ | Pure formatting |
| Normalise `\r\n` → `\n` | ✅ | Platform-independent |
| Remove blank lines between top-level statements (max 2 blank lines preserved) | ✅ | PEP 8 formatting |
| Change indentation character (`\t` ↔ spaces) | ❌ | Python-semantic; `IndentationError` risk |
| Re-order function parameters | ❌ | Semantic change |
| Rename variables or identifiers | ❌ | Semantic change |
| Add/remove `pass` statements, imports | ❌ | Semantic change |

**Implementation contract:**

```python
import ast

def assert_code_semantically_equivalent(raw: str, normalised: str) -> None:
    """Raise ValueError if normalised code is not semantically equivalent to raw.

    Uses ast.dump() for Python code. For non-Python text, falls back to
    string equality after whitespace normalization.

    Raises:
        ValueError: If AST parse fails or ASTs differ.
    """
    try:
        raw_ast = ast.dump(ast.parse(raw))
        norm_ast = ast.dump(ast.parse(normalised))
    except SyntaxError:
        # Not valid Python — fall back to whitespace-normalised string comparison
        raw_ws = " ".join(raw.split())
        norm_ws = " ".join(normalised.split())
        if raw_ws != norm_ws:
            raise ValueError(
                "normalized_output is not semantically equivalent to raw_output "
                "(non-Python text: whitespace-normalised strings differ)"
            )
        return
    if raw_ast != norm_ast:
        raise ValueError(
            "normalized_output is not semantically equivalent to raw_output "
            "(Python ASTs differ)"
        )
```

**Scope note**: The `response_hash` is always computed from `normalized_output`
(post-formatting normalization), never from the AST dump. The AST check is a
**construction-time guard** to prevent accidentally recording a semantically
different normalised output, not a new hashing input.

**Non-code responses**: For plain-text responses (no code blocks), semantic
equivalence is verified by whitespace-normalised string comparison (join on
single space). The full AST pipeline is only invoked when the response contains
a fenced code block (` ```python `) or when the normaliser detects a
`SyntaxError` on the raw output.

---

### 3.4 `ConsensusClassification` (Enum)

<!-- Addresses EDGE-003, EDGE-009, EDGE-037 -->

```python
class ConsensusClassification(str, Enum):
    """Classification of the M-of-N agreement level.

    Boundaries are **inclusive on the upper threshold**:

        STRONG   : ratio >= 0.80
        MAJORITY : 0.60 <= ratio < 0.80
        WEAK     : 0.40 <= ratio < 0.60
        NONE     : ratio  < 0.40

    The function ``classify_ratio(ratio)`` (below) is the canonical mapping.
    All implementations must use it; do not inline the boundary logic.
    """
    STRONG   = "STRONG"
    MAJORITY = "MAJORITY"
    WEAK     = "WEAK"
    NONE     = "NONE"


def classify_ratio(ratio: float) -> ConsensusClassification:
    """Map a consensus ratio in [0.0, 1.0] to a ConsensusClassification.

    The mapping is exhaustive and covers every value in [0.0, 1.0].
    Raises ValueError for ratios outside [0.0, 1.0].

    Boundary examples (EDGE-009, EDGE-024):
      classify_ratio(0.80) -> STRONG    (inclusive lower bound of STRONG)
      classify_ratio(0.799) -> MAJORITY
      classify_ratio(0.60) -> MAJORITY  (inclusive lower bound of MAJORITY)
      classify_ratio(0.599) -> WEAK
      classify_ratio(0.40) -> WEAK      (inclusive lower bound of WEAK)
      classify_ratio(0.399) -> NONE
      classify_ratio(0.0)  -> NONE
      classify_ratio(1.0)  -> STRONG
    """
    if not (0.0 <= ratio <= 1.0):
        raise ValueError(f"consensus_ratio must be in [0.0, 1.0], got {ratio}")
    if ratio >= 0.80:
        return ConsensusClassification.STRONG
    if ratio >= 0.60:
        return ConsensusClassification.MAJORITY
    if ratio >= 0.40:
        return ConsensusClassification.WEAK
    return ConsensusClassification.NONE
```

**Boundary table (EDGE-009):**

| Ratio | Classification |
|-------|---------------|
| 1.0 | STRONG |
| 0.80 | STRONG |
| 0.799… | MAJORITY |
| 0.60 | MAJORITY |
| 0.599… | WEAK |
| 0.40 | WEAK |
| 0.399… | NONE |
| 0.0 | NONE |

---

### 3.5 `ConsensusResult`

<!-- Addresses EDGE-003, EDGE-010, EDGE-011, EDGE-019, EDGE-024, EDGE-029, EDGE-032, EDGE-037 -->

```python
@dataclass
class ConsensusResult:
    """Outcome of M-of-N multi-model consensus.

    Attributes:
        agreements:      Number of models whose normalised output agreed with
            the majority response.  Must satisfy ``0 <= agreements <= total``.
        total:           Total number of models **queried** (not just those
            that responded).  Models that timed out or errored count toward
            ``total`` but not toward ``agreements``.  Must be >= 1.
            (See EDGE-029 for why total = queried, not responded.)
        consensus_ratio: Computed as ``agreements / total``.  Always in
            ``[0.0, 1.0]``.  Stored for convenience; recomputed from
            ``agreements`` and ``total`` on deserialisation to prevent forgery.
            (See EDGE-002, EDGE-003, EDGE-023.)
        classification:  Derived from ``consensus_ratio`` via
            ``classify_ratio()``.  Always consistent with ``consensus_ratio``.
            (See EDGE-003, EDGE-037.)
    """
    agreements: int
    total: int
    consensus_ratio: float
    classification: ConsensusClassification

    def __post_init__(self) -> None:
        # Guard: total must be at least 1 (EDGE-011)
        if self.total < 1:
            raise ValueError(
                f"ConsensusResult.total must be >= 1, got {self.total}"
            )
        # Guard: agreements in [0, total] (EDGE-019)
        if not (0 <= self.agreements <= self.total):
            raise ValueError(
                f"ConsensusResult.agreements must be in [0, total={self.total}], "
                f"got {self.agreements}"
            )
        # Recompute and verify ratio (EDGE-002, EDGE-003)
        expected_ratio = self.agreements / self.total
        if abs(self.consensus_ratio - expected_ratio) > 1e-9:
            raise ValueError(
                f"ConsensusResult.consensus_ratio {self.consensus_ratio!r} does not "
                f"match agreements/total = {expected_ratio!r}"
            )
        # Recompute and verify classification (EDGE-003, EDGE-037)
        expected_cls = classify_ratio(self.consensus_ratio)
        if self.classification != expected_cls:
            raise ValueError(
                f"ConsensusResult.classification {self.classification!r} is "
                f"inconsistent with consensus_ratio {self.consensus_ratio!r}; "
                f"expected {expected_cls!r}"
            )

    @classmethod
    def build(cls, agreements: int, total: int) -> "ConsensusResult":
        """Construct a ConsensusResult, computing ratio and classification.

        Args:
            agreements: Models that agreed with the majority output.
            total:      Total models queried (including non-responders).

        Raises:
            ValueError: If invariants are violated.
        """
        ratio = agreements / total  # ZeroDivisionError propagates if total=0
        return cls(
            agreements=agreements,
            total=total,
            consensus_ratio=ratio,
            classification=classify_ratio(ratio),
        )
```

**Denominator policy (EDGE-029):**

The denominator in `consensus_ratio` is always the number of models **queried**,
not the number that responded. Rationale: using responders-only as the denominator
would allow an attacker to make partial consensus look stronger by timing out
uncooperative models.

**Minimum N policy (EDGE-010):**

`total` must be ≥ 1 (validated in `__post_init__`). However, callers querying a
single model (`total = 1`) receive a valid `ConsensusResult` — the classification
will be STRONG if the single model responds (`1/1 = 1.0`). **Operators** are
advised to set `total >= 3` for meaningful multi-model consensus (see
`specs/llm-provider-spec.md` §Multi-Model Consensus — "two or more providers
must agree").

---

### 3.6 `DeterministicProof`

<!-- Addresses EDGE-001, EDGE-002, EDGE-004, EDGE-005, EDGE-015, EDGE-016, EDGE-017, EDGE-020, EDGE-023, EDGE-028, EDGE-031, EDGE-033, EDGE-040 -->

Extends `ReasoningProof` (from `maatproof/proof.py`) with DRE-specific consensus
fields. All base-class invariants (`root_hash`, `signature`, `steps`) continue to
apply.

```python
@dataclass
class DeterministicProof(ReasoningProof):
    """A ReasoningProof enriched with multi-model consensus metadata.

    Inherits from ``maatproof.proof.ReasoningProof``:
        proof_id, model_id, chain_id, steps, root_hash, signature,
        created_at, metadata.

    Additional fields:
        prompt_hash:      SHA-256 hex digest (64 chars) of the
            ``CanonicalPrompt.content`` used to query all models.
            Must match a ``CanonicalPrompt`` object that can be independently
            reconstructed from the prompt bytes stored in the trace.
        consensus_ratio:  The ``ConsensusResult.consensus_ratio`` value.
            Must be in ``[0.0, 1.0]``.  Always recomputed from the
            ``ConsensusResult`` — never accepted from untrusted input alone.
        response_hash:    SHA-256 hex digest of the **consensus** normalised
            output.  For multi-model consensus, this is the SHA-256 of the
            majority response's ``normalized_output``.  If no majority exists,
            this is SHA-256(b"") and ``ConsensusResult.classification`` is NONE.
            (See EDGE-040.)
        model_ids:        Non-empty list of canonical model identifiers
            (``{provider}/{model}@{version}``) for all models that were queried.
            No duplicates.  Each entry must pass ``ModelResponse`` model-ID
            format validation.  (See EDGE-004, EDGE-005, EDGE-020, EDGE-028.)

    Metadata shadowing (EDGE-031):
        The inherited ``metadata`` dict must NOT contain keys ``prompt_hash``,
        ``consensus_ratio``, ``response_hash``, or ``model_ids``.  These are
        always read from their dedicated fields.  The ``__post_init__`` method
        enforces this.
    """
    prompt_hash: str       # 64 hex chars; SHA-256 of CanonicalPrompt.content
    consensus_ratio: float # [0.0, 1.0]; from ConsensusResult
    response_hash: str     # 64 hex chars; SHA-256 of majority normalized_output
    model_ids: list        # List[str]; non-empty; no duplicates; canonical format

    _MODEL_ID_PATTERN: ClassVar[re.Pattern] = re.compile(
        r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+$"
    )
    _RESERVED_METADATA_KEYS: ClassVar[frozenset] = frozenset(
        {"prompt_hash", "consensus_ratio", "response_hash", "model_ids"}
    )

    def __post_init__(self) -> None:
        # prompt_hash validation (EDGE-001, EDGE-016)
        if not _is_sha256_hex(self.prompt_hash):
            raise ValueError(
                "DeterministicProof.prompt_hash must be a 64-char SHA-256 hex digest"
            )
        # consensus_ratio range (EDGE-002)
        if not (0.0 <= self.consensus_ratio <= 1.0):
            raise ValueError(
                f"DeterministicProof.consensus_ratio must be in [0.0, 1.0], "
                f"got {self.consensus_ratio}"
            )
        # response_hash validation (EDGE-015, EDGE-016)
        if not _is_sha256_hex(self.response_hash):
            raise ValueError(
                "DeterministicProof.response_hash must be a 64-char SHA-256 hex digest"
            )
        # model_ids non-empty (EDGE-005)
        if not self.model_ids:
            raise ValueError("DeterministicProof.model_ids must not be empty")
        # model_ids: no None elements (EDGE-028)
        for i, mid in enumerate(self.model_ids):
            if mid is None:
                raise ValueError(
                    f"DeterministicProof.model_ids[{i}] is None; "
                    "all entries must be non-None strings"
                )
            # model_ids: canonical format (EDGE-020, EDGE-033)
            if not self._MODEL_ID_PATTERN.match(str(mid)):
                raise ValueError(
                    f"DeterministicProof.model_ids[{i}] {mid!r} does not follow "
                    "'{provider}/{model}@{version}' format"
                )
        # model_ids: no duplicates (EDGE-004)
        if len(self.model_ids) != len(set(self.model_ids)):
            dupes = [m for m in self.model_ids if self.model_ids.count(m) > 1]
            raise ValueError(
                f"DeterministicProof.model_ids contains duplicates: {dupes}"
            )
        # metadata shadowing guard (EDGE-031)
        shadow = self._RESERVED_METADATA_KEYS & set(self.metadata.keys())
        if shadow:
            raise ValueError(
                f"DeterministicProof.metadata must not contain reserved DRE "
                f"field keys: {shadow}"
            )
```

**Multi-model `response_hash` computation (EDGE-040):**

When N models are queried, the `response_hash` in `DeterministicProof` is the
SHA-256 of the **majority-vote normalised output** — i.e., the `normalized_output`
from the `ModelResponse` that represents the agreed-upon answer:

```
majority_response = most_common(model_responses, key=lambda r: r.normalized_output)
response_hash     = SHA-256(majority_response.normalized_output.encode("utf-8"))
```

If there is no majority (all responses differ, `ConsensusClassification.NONE`),
`response_hash` is set to `SHA-256(b"")` =
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

**Serialisation round-trip and float precision (EDGE-023):**

`consensus_ratio` is stored as a Python `float` (IEEE 754 double). To prevent
precision drift in `to_dict()` / `from_dict()` round-trips, implementations
must round-trip the ratio through the `agreements` and `total` integers from the
`ConsensusResult` and recompute rather than trusting the stored float. The
`__post_init__` tolerance of `1e-9` accommodates floating-point arithmetic while
rejecting forged values.

---

## 4. Hash Computation Reference

<!-- Addresses EDGE-016 -->

All hashes in the DRE layer use **SHA-256** exclusively. No other algorithm
(MD5, SHA-1, SHA-512) is acceptable. Implementations must validate digest length:

```python
def _is_sha256_hex(value: str) -> bool:
    """Return True iff value is a 64-character lowercase hex string."""
    if not isinstance(value, str):
        return False
    if len(value) != 64:
        return False
    try:
        int(value, 16)
        return True
    except ValueError:
        return False
```

---

## 5. Validation Rules Summary

<!-- Addresses EDGE-001 through EDGE-040 -->

| Model | Field | Constraint | EDGE scenario |
|-------|-------|-----------|---------------|
| `DeterminismParams` | `temperature` | `== 0.0` | EDGE-012 |
| `DeterminismParams` | `seed` | Integer in `[0, 2^32-1]`; required | EDGE-013, EDGE-038 |
| `DeterminismParams` | `top_p` | `== 1.0` | EDGE-014 |
| `CanonicalPrompt` | `content` | `len <= 32768`; bytes | EDGE-008 |
| `CanonicalPrompt` | `prompt_hash` | 64 hex chars; SHA-256 of content | EDGE-001, EDGE-016 |
| `CanonicalPrompt` | normalization | NFC + control-char strip | EDGE-006, EDGE-030 |
| `ModelResponse` | `model_id` | `{provider}/{model}@{version}` regex | EDGE-020, EDGE-033 |
| `ModelResponse` | `response_hash` | SHA-256 of `normalized_output` | EDGE-015 |
| `ModelResponse` | `normalized_output` | Formatting-only diff from `raw_output` | EDGE-018 |
| `ConsensusResult` | `total` | `>= 1` | EDGE-011 |
| `ConsensusResult` | `agreements` | `0 <= agreements <= total` | EDGE-019 |
| `ConsensusResult` | `consensus_ratio` | `== agreements / total` (±1e-9) | EDGE-002, EDGE-003 |
| `ConsensusResult` | `classification` | Derived from `classify_ratio(ratio)` | EDGE-003, EDGE-037 |
| `DeterministicProof` | `prompt_hash` | 64 hex chars; SHA-256 | EDGE-001, EDGE-016 |
| `DeterministicProof` | `consensus_ratio` | `[0.0, 1.0]` | EDGE-002 |
| `DeterministicProof` | `response_hash` | 64 hex chars; majority response SHA-256 | EDGE-015, EDGE-040 |
| `DeterministicProof` | `model_ids` | Non-empty; no duplicates; no None; canonical format | EDGE-004, EDGE-005, EDGE-028 |
| `DeterministicProof` | `metadata` | Must not shadow DRE fields | EDGE-031 |

---

## 6. Security Considerations

### 6.1 Replay Attack Prevention

<!-- Addresses EDGE-027 -->

`DeterministicProof` inherits `proof_id` (a UUID v4) from `ReasoningProof`.
Each proof constructed by `ProofBuilder` receives a unique `proof_id`; the same
`proof_id` must never be accepted twice by the AVM or consensus layer.

At the DRE data-model level, replay prevention is limited to uniqueness of
`proof_id`. **Full replay prevention** — including environment binding,
nonce freshness checks, and chain-ID scoping — is enforced by the trace
verification layer (`specs/trace-verification-spec.md` §Replay Procedure and
`docs/06-security-model.md` §Replay Attack Prevention).

```mermaid
flowchart LR
    DRE["DeterministicProof\n(unique proof_id)"]
    AVM["AVM Layer\n(trace_id uniqueness check)"]
    SEC["Security Model\n(environment + chain_id binding)"]
    DRE --> AVM --> SEC
```

### 6.2 Cross-Environment Proof Reuse

<!-- Addresses EDGE-036 -->

`DeterministicProof` does **not** embed a `deploy_environment` field. Environment
binding is the responsibility of the enclosing `DeploymentTrace`
(`deploy_environment` field in `specs/trace-verification-spec.md`). A
`DeterministicProof` extracted from one trace and embedded in another is
detectable because the parent trace's `deploy_environment` and `policy_version`
fields will not match — the AVM's policy evaluator will reject the submission.

For defence-in-depth, operators **should** include `deploy_environment` in the
`metadata` dict of `DeterministicProof` so that auditors can confirm environment
intent at the proof level without having to resolve the parent trace.

### 6.3 `model_ids` Forgery

<!-- Addresses EDGE-004, EDGE-005 -->

A `DeterministicProof.model_ids` list that includes models that never participated
in the actual LLM calls would inflate apparent consensus breadth. Countermeasures:

1. **At build time**: The DRE executor constructs `model_ids` from the set of
   `ModelResponse.model_id` values returned by actual LLM calls — it is never
   passed in from untrusted external input.
2. **At verification time**: Validators re-execute the DRE consensus step using
   the models listed in `model_ids` and verify the `response_hash` matches.

### 6.4 Prompt Injection Responsibility

<!-- Addresses EDGE-026 -->

`CanonicalPrompt` performs **normalisation** only — it does not detect or
reject adversarial payloads. Injection-pattern detection (`"ignore all previous
instructions"`, etc.) is enforced at the AVM layer before the prompt reaches
any LLM. See `specs/avm-spec.md` §Prompt Injection Mitigations.

### 6.5 Metadata Field Shadowing

<!-- Addresses EDGE-031 -->

`DeterministicProof.__post_init__` rejects any `metadata` dict that contains
keys `prompt_hash`, `consensus_ratio`, `response_hash`, or `model_ids`. Consumer
code must always read these values from the dedicated fields, never from
`metadata`. This invariant is enforced at construction time.

### 6.6 All-Committee-Timeout Behavior

<!-- Addresses EDGE-INT-002 (issue #134) -->

When **all N committee members** time out or fail to respond, the DRE pipeline
MUST produce a `ConsensusResult` with `agreements = 0` and `total = N` (the
number of models **queried**, per the denominator policy in §3.5). This yields
`consensus_ratio = 0.0` → `ConsensusClassification.NONE`.

The resulting `DeterministicProof` is constructed with:
- `consensus_ratio = 0.0`
- `classification = NONE`
- `response_hash = SHA-256(b"") = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- `model_ids` = the list of models that were **queried** (not only those that
  responded), so that the proof is auditable.

The proof is **valid** (passes `ProofVerifier.verify()`) but the `NONE`
classification prevents deployment authorization under the ADA 7-condition gate
(condition 2: DRE quorum satisfied). The proof is persisted to the audit trail
as evidence of the timeout event.

**Error vs. valid-proof distinction**: The DRE executor MUST NOT raise an
unhandled exception when all models time out. A `DeterministicProof` with
`ConsensusClassification.NONE` is the correct response. Callers that require
a minimum consensus level (e.g., `MAJORITY`) are responsible for checking the
`classification` field and escalating to human review accordingly.

```
All N timeouts → ConsensusResult.build(agreements=0, total=N)
               → ratio=0.0 → classification=NONE
               → DeterministicProof(response_hash=SHA-256(b""), ...)
               → proof persisted; deployment BLOCKED (ADA condition 2 fails)
```

---

## 7. Edge Case Handling Reference

<!-- Summarises all EDGE scenarios and their resolution -->

| EDGE ID | Scenario | Resolution |
|---------|----------|-----------|
| EDGE-001 | Empty `prompt_hash` | `__post_init__` rejects non-64-char value |
| EDGE-002 | `consensus_ratio > 1.0` | `__post_init__` rejects value outside `[0.0, 1.0]` |
| EDGE-003 | Contradictory ratio/classification | `ConsensusResult.__post_init__` recomputes both and rejects inconsistency |
| EDGE-004 | Duplicate `model_ids` | `DeterministicProof.__post_init__` rejects duplicates |
| EDGE-005 | Empty `model_ids` | `DeterministicProof.__post_init__` rejects empty list |
| EDGE-006 | Non-NFC Unicode in prompt | `CanonicalPrompt.from_str()` applies NFC before hashing |
| EDGE-007 | Empty prompt string | Valid; `prompt_hash = SHA-256(b"") = e3b0c44...` |
| EDGE-008 | 500 KB prompt | Rejected at 32,768 bytes; `ValueError` raised |
| EDGE-009 | Boundary at 0.40/0.60/0.80 | `classify_ratio` uses inclusive lower bounds; see table in §3.4 |
| EDGE-010 | N=1 (single model) | Valid but classified as STRONG (1/1 = 1.0); operators advised to use N≥3 |
| EDGE-011 | `total = 0` (div/zero) | `ConsensusResult.__post_init__` rejects `total < 1` |
| EDGE-012 | `temperature ≠ 0.0` | `DeterminismParams.__post_init__` raises `ValueError` |
| EDGE-013 | `seed = None` | `DeterminismParams` requires non-None integer |
| EDGE-014 | `top_p ≠ 1.0` | `DeterminismParams.__post_init__` raises `ValueError` |
| EDGE-015 | `response_hash` mismatch | `ModelResponse.__post_init__` recomputes and validates |
| EDGE-016 | MD5 instead of SHA-256 | `_is_sha256_hex` rejects non-64-char digests |
| EDGE-017 | Broken Python inheritance | Spec defines `DeterministicProof(ReasoningProof)` pattern with `__post_init__` |
| EDGE-018 | Normalization changes semantics | Spec limits normalisation to formatting-only changes |
| EDGE-019 | `agreements > total` | `ConsensusResult.__post_init__` rejects `agreements > total` |
| EDGE-020 | Non-canonical `model_id` | Regex validation in `ModelResponse` and `DeterministicProof` |
| EDGE-021 | Concurrent sort | `unicodedata.normalize` is reentrant; no locking required |
| EDGE-022 | JSON string as prompt | Prompt treated as opaque string; not re-parsed |
| EDGE-023 | Float precision in round-trip | Recompute from `agreements/total`; tolerance `1e-9` |
| EDGE-024 | 2/3 = 67% → MAJORITY | `classify_ratio(0.667)` → MAJORITY (≥0.60) |
| EDGE-025 | Empty `raw_output` | `response_hash = SHA-256(b"") = e3b0c44...`; valid |
| EDGE-026 | Injection in prompt | `CanonicalPrompt` stores faithfully; AVM detects injection |
| EDGE-027 | DRE-level replay | `proof_id` uniqueness from `ReasoningProof`; AVM enforces deduplication |
| EDGE-028 | `None` in `model_ids` | `DeterministicProof.__post_init__` rejects `None` entries |
| EDGE-029 | Partial model response | Denominator = queried count (prevents attacker timeout manipulation) |
| EDGE-030 | Control chars in prompt | `CanonicalPrompt.from_str()` strips control chars |
| EDGE-031 | Metadata field shadowing | `__post_init__` rejects reserved keys in `metadata` |
| EDGE-032 | M/N irrecoverability | `ConsensusResult` stores both `agreements` and `total` as integers |
| EDGE-033 | `model_id` format mismatch | Regex validation enforced in both `ModelResponse` and `DeterministicProof` |
| EDGE-034 | Missing `dre.models` module | §2 Module Structure defines `dre/models.py` |
| EDGE-035 | Zero-byte `content` | Same as EDGE-007; SHA-256(b"") is valid |
| EDGE-036 | Cross-environment replay | `deploy_environment` in enclosing trace; guidance to add to `metadata` |
| EDGE-037 | Enum exhaustiveness | `classify_ratio` covers entire `[0.0, 1.0]` range with no gaps |
| EDGE-038 | Out-of-range seed | `DeterminismParams` validates seed in `[0, 2^32-1]` |
| EDGE-039 | Thread-safe normalisation | `unicodedata.normalize` is reentrant in CPython/PyPy |
| EDGE-040 | Multi-model `response_hash` | SHA-256 of majority `normalized_output`; SHA-256(b"") if NONE |

---

## 8. Serialisation Contract

All four models implement `to_dict()` → `from_dict()` round-trip serialisation.
The invariant is: `from_dict(to_dict(obj))` produces an object that is equal
to the original under `==`.

Key constraints:
- `consensus_ratio` is serialised as a JSON float; on deserialisation it is
  **recomputed** from `agreements / total` and validated (±1e-9 tolerance).
- `model_ids` is serialised as a JSON array; on deserialisation duplicates and
  `None` entries are re-validated.
- `classification` is serialised as the string value of `ConsensusClassification`
  and re-derived from `consensus_ratio` on deserialisation.

---

## 9. Flow Diagram — DRE Proof Construction

```mermaid
flowchart TD
    INPUT["Raw prompt string\n+ N model configs"]
    CANON["CanonicalPrompt.from_str()\nNFC + control-char strip\nSHA-256 → prompt_hash"]
    PARAMS["DeterminismParams\ntemp=0.0, seed=K, top_p=1.0"]
    LLM["Query N models concurrently\n(asyncio)"]
    RESP["N × ModelResponse\nnormalized_output + response_hash"]
    CONSENSUS["ConsensusResult.build()\nM = agreements, N = queried\nratio + classification"]
    MAJORITY["Majority normalized_output\n→ response_hash"]
    PROOF["DeterministicProof\n(extends ReasoningProof)\nAll fields validated"]

    INPUT --> CANON
    INPUT --> PARAMS
    PARAMS --> LLM
    CANON --> LLM
    LLM --> RESP
    RESP --> CONSENSUS
    RESP --> MAJORITY
    CONSENSUS --> PROOF
    MAJORITY --> PROOF
    CANON --> PROOF
```

---

## 10. DRE Component Naming Reference

<!-- Addresses EDGE-D004 — maps issue #137 component names to spec terms -->

Issue #137 uses five component names that map to the following spec types and stages:

| Documentation Name | Spec Term | Type / Stage | Key Spec Section |
|---|---|---|---|
| **Canonical Prompt Serializer** | `CanonicalPrompt` + `CanonicalPrompt.from_str()` | Stage 1 — Canonical Prompt Construction | §3.2 |
| **Multi-Model Executor** | `CommitteeConfig` + parallel `LlmProvider.complete()` calls | Stage 2 — Committee Execution | `specs/dre-spec.md` §Stage 2 |
| **Response Normalizer** | `ModelResponse.normalized_output` + `response_hash` | Stage 3 — Normalization | §3.3 |
| **Consensus Engine** | `ConsensusResult.build()` + `classify_ratio()` | Stage 4 — Convergence | §3.4–3.5 |
| **DeterministicProof** | `DeterministicProof` (extends `ReasoningProof`) | Stage 5 — Certification | §3.6 |

### Component Interaction Flow

```mermaid
flowchart LR
    RAW["Raw prompt\n+ model configs"]
    SER["Canonical Prompt\nSerializer\n(CanonicalPrompt.from_str)"]
    EXE["Multi-Model\nExecutor\n(N × LlmProvider)"]
    NORM["Response\nNormalizer\n(ModelResponse)"]
    CON["Consensus\nEngine\n(ConsensusResult.build)"]
    PROOF["DeterministicProof\n(extends ReasoningProof)"]

    RAW --> SER --> EXE --> NORM --> CON --> PROOF
    SER -.->|"prompt_hash"| PROOF
    CON -.->|"consensus_ratio\nclassification"| PROOF
    NORM -.->|"response_hash\nmodel_ids"| PROOF
```

> **Note on "sorted keys"**: The Canonical Prompt Serializer serialises the
> *surrounding JSON trace* (PromptBundle, DeploymentTrace) with
> lexicographically sorted keys — see `specs/dre-spec.md §Canonicalization Rules`.
> The **prompt content itself is treated as an opaque string** and is NOT
> re-parsed or re-serialised; NFC normalisation is applied but JSON keys inside
> the prompt are not sorted.  See §3.2 (EDGE-022).

> **Note on "min 3 models"**: The requirement "min 3 models" applies to `uat`
> and `prod` environments.  In `dev`, a single-model committee (`total = 1`) is
> valid for local testing, though the consensus result will always be STRONG
> (1/1 = 1.0).  See `specs/dre-config-spec.md §1`.

---

## 11. Quick-Start Usage Example

<!-- Addresses EDGE-D003 — quick-start DeterministicProof construction example -->

The following example shows the minimum code to construct a `DeterministicProof`
end-to-end in the Python DRE layer.  This is the reference pattern that the
README and architecture documentation should use.

```python
import hashlib
from dre.models import (
    CanonicalPrompt,
    ConsensusResult,
    DeterminismParams,
    DeterministicProof,
    ModelResponse,
)
from maatproof.proof import ReasoningStep

# ── Step 1: Canonical Prompt Serializer ─────────────────────────────────────
raw_prompt = "Should we deploy PR #42 to production? Tests: 87% coverage, 0 CVEs."
canonical = CanonicalPrompt.from_str(raw_prompt)
# canonical.prompt_hash is the SHA-256 hex digest of the NFC-normalised UTF-8 bytes

# ── Step 2: DeterminismParams — enforce temp=0, fixed seed, top_p=1.0 ────────
params = DeterminismParams(temperature=0.0, seed=314159, top_p=1.0)

# ── Step 3: Multi-Model Executor (simulated) ─────────────────────────────────
# In production this calls N LlmProvider instances in parallel.
# Each provider must support deterministic mode (temp=0, fixed seed).
model_outputs = {
    "anthropic/claude-3-7-sonnet@20250219": "APPROVE — coverage meets threshold, no CVEs.",
    "openai/gpt-4o@2024-08-06":             "APPROVE — coverage meets threshold, no CVEs.",
    "mistral/mistral-large@2407":           "APPROVE — 87% coverage, clean security scan.",
}

# ── Step 4: Response Normalizer — strip formatting variants ──────────────────
def _norm(text: str) -> str:
    """Strip trailing whitespace and collapse blank lines (formatting only)."""
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()

responses = [
    ModelResponse(
        model_id=mid,
        raw_output=raw,
        normalized_output=_norm(raw),
        determinism_params=params,
        response_hash=hashlib.sha256(_norm(raw).encode()).hexdigest(),
    )
    for mid, raw in model_outputs.items()
]

# ── Step 5: Consensus Engine — M-of-N agreement ───────────────────────────────
from collections import Counter
majority_output = Counter(r.normalized_output for r in responses).most_common(1)[0][0]
agreements = sum(1 for r in responses if r.normalized_output == majority_output)
result = ConsensusResult.build(agreements=agreements, total=len(responses))
# result.classification → ConsensusClassification.STRONG  (3/3 = 1.0 ≥ 0.80)

# ── Step 6: DeterministicProof construction ───────────────────────────────────
response_hash = hashlib.sha256(majority_output.encode()).hexdigest()

from maatproof.proof import ProofBuilder
builder = ProofBuilder(secret_key=b"operator-hmac-key", model_id="dre-committee")
proof = builder.build(steps=[
    ReasoningStep(
        step_id=0,
        context=f"Prompt hash: {canonical.prompt_hash[:16]}…",
        reasoning=f"3-of-3 models agreed ({result.classification.value})",
        conclusion="APPROVE",
        timestamp=1700000000.0,
    )
])

det_proof = DeterministicProof(
    # ── Inherited from ReasoningProof ──────────────────────────────────────────
    proof_id=proof.proof_id,
    model_id=proof.model_id,
    chain_id="deploy-chain-001",
    steps=proof.steps,
    root_hash=proof.root_hash,
    signature=proof.signature,
    created_at=proof.created_at,
    metadata={},                  # must NOT contain DRE reserved keys
    # ── DRE-specific fields ───────────────────────────────────────────────────
    prompt_hash=canonical.prompt_hash,
    consensus_ratio=result.consensus_ratio,
    response_hash=response_hash,
    model_ids=[r.model_id for r in responses],
)

print(f"proof_id:        {det_proof.proof_id}")
print(f"prompt_hash:     {det_proof.prompt_hash}")
print(f"consensus_ratio: {det_proof.consensus_ratio:.4f}")   # 1.0
print(f"classification:  {result.classification.value}")     # STRONG
print(f"response_hash:   {det_proof.response_hash}")
print(f"model_ids:       {det_proof.model_ids}")
```

**Inherited fields from `ReasoningProof`** (from `maatproof/proof.py`):

| Field | Type | Description |
|---|---|---|
| `proof_id` | UUID v4 string | Unique proof identifier (replay deduplication) |
| `model_id` | string | Logical model identifier for the proof builder |
| `chain_id` | string | Scopes this proof to a deployment chain |
| `steps` | list[ReasoningStep] | Hash-chained reasoning steps |
| `root_hash` | SHA-256 hex | Merkle root hash of the step chain |
| `signature` | HMAC-SHA256 hex | HMAC over `root_hash` using operator secret |
| `created_at` | float | Unix timestamp |
| `metadata` | dict | Arbitrary metadata; must NOT contain DRE reserved keys |

**DRE-specific additional fields** (defined in this spec):

| Field | Type | Description |
|---|---|---|
| `prompt_hash` | SHA-256 hex | Hash of `CanonicalPrompt.content` |
| `consensus_ratio` | float [0.0, 1.0] | `agreements / total`; recomputed on deserialisation |
| `response_hash` | SHA-256 hex | SHA-256 of majority `normalized_output`; `SHA-256(b"")` if NONE |
| `model_ids` | list[str] | Non-empty; no duplicates; canonical `{provider}/{model}@{version}` |

---

## 12. Independent Verification Procedure

<!-- Addresses EDGE-D009, EDGE-D010, EDGE-D011, EDGE-D046 -->

An independent party (auditor, validator, or external reviewer) can verify a
`DeterministicProof` at two levels:

### 12.1 Structural Verification (no LLM access required)

Structural verification confirms that all hash fields are internally consistent
and that the proof was not tampered with after construction.  This can be
performed by anyone with the proof JSON.

```python
import hashlib
from dre.models import DeterministicProof, ConsensusResult, classify_ratio

def structural_verify(proof_dict: dict) -> dict:
    """
    Structural (offline) verification of a DeterministicProof.
    Does NOT require re-running any LLM.  Returns a result dict.

    <!-- Addresses EDGE-D009, EDGE-D046 -->
    """
    results = {}

    # 1. Reconstruct the object — __post_init__ validates all field invariants
    try:
        proof = DeterministicProof(**proof_dict)
        results["construction"] = "PASS"
    except (ValueError, TypeError) as e:
        results["construction"] = f"FAIL: {e}"
        return results

    # 2. Verify consensus_ratio is consistent with stored agreements/total
    #    (proof_dict must include these if stored by the DRE executor)
    agreements = proof_dict.get("_agreements")   # implementation MAY store these
    total       = proof_dict.get("_total")
    if agreements is not None and total is not None:
        expected = agreements / total
        if abs(proof.consensus_ratio - expected) > 1e-9:
            results["ratio_integrity"] = (
                f"FAIL: ratio {proof.consensus_ratio} ≠ {agreements}/{total}"
            )
        else:
            results["ratio_integrity"] = "PASS"
    else:
        results["ratio_integrity"] = "SKIP: _agreements/_total not stored"

    # 3. Verify classification is consistent with ratio
    expected_cls = classify_ratio(proof.consensus_ratio)
    results["classification"] = (
        "PASS" if expected_cls.value ==
        proof_dict.get("classification_stored", expected_cls.value)
        else "FAIL"
    )

    # 4. Verify prompt_hash format (64 hex chars)
    results["prompt_hash_format"] = (
        "PASS" if len(proof.prompt_hash) == 64 and
        all(c in "0123456789abcdef" for c in proof.prompt_hash)
        else "FAIL"
    )

    # 5. Verify response_hash format
    results["response_hash_format"] = (
        "PASS" if len(proof.response_hash) == 64 and
        all(c in "0123456789abcdef" for c in proof.response_hash)
        else "FAIL"
    )

    results["overall"] = "PASS" if all(v == "PASS" or v.startswith("SKIP")
                                       for v in results.values()) else "FAIL"
    return results
```

### 12.2 Full Replay Verification (LLM access required)

Full verification re-runs the LLM committee and confirms that the stored
`prompt_hash` and `response_hash` are reproducible.

**Prerequisites:**
- Access to the original canonical prompt bytes (stored in the deployment trace
  on IPFS; retrieve via the trace CID recorded in the finalized `MaatBlock`)
- Access to all model providers listed in `proof.model_ids`
- The exact `DeterminismParams` used (seed, temperature=0.0, top_p=1.0)

**Step-by-step replay procedure:**

```
Step 1 — Retrieve prompt bytes
  • Resolve the deployment trace from IPFS using the CID in the MaatBlock
  • Extract the canonical_prompt_bytes from the trace (UTF-8 bytes,
    already NFC-normalised)
  • Compute SHA-256(canonical_prompt_bytes) → must equal proof.prompt_hash

Step 2 — Verify normalisation
  • Decode bytes as UTF-8
  • Apply unicodedata.normalize("NFC", decoded)
  • Re-encode to UTF-8
  • Compute SHA-256 → must equal proof.prompt_hash

Step 3 — Re-execute the committee
  • For each model_id in proof.model_ids:
    - Call the provider with (prompt_bytes, temperature=0.0, seed=K, top_p=1.0)
    - Apply the normalisation pipeline to the raw output
    - Compute SHA-256(normalized_output.encode("utf-8")) → record response_hash_i

Step 4 — Recompute consensus
  • Find the majority normalized_output among all model responses
  • Compute agreements = count of models matching the majority
  • Compute consensus_ratio = agreements / len(proof.model_ids)
  • Verify |consensus_ratio - proof.consensus_ratio| ≤ 1e-9

Step 5 — Verify response hash
  • Compute SHA-256(majority_normalized_output.encode("utf-8"))
  • Must equal proof.response_hash
  • Exception: if no majority (all responses differ), verify
    proof.response_hash == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    (SHA-256 of empty bytes) and proof.classification == "NONE"
```

### 12.3 LLM Replay Non-Reproducibility (Important Caveat)

<!-- Addresses EDGE-D010 -->

Even with `temperature=0.0` and a fixed seed, LLM providers may produce
**different outputs upon replay** due to:

- Model version deprecation (the provider has updated the weights behind the
  same version string)
- Server-side infrastructure changes (batching, quantisation, BLAS version)
- Provider API response format changes

**Consequence**: Full replay verification (§12.2 Step 3–5) may fail even for a
legitimately constructed proof if the provider has changed the model's behaviour.

**Protocol resolution**: The MaatProof protocol treats this as a
**"soft non-match"** (not a fraud signal):

| Outcome | Interpretation | Action |
|---|---|---|
| `prompt_hash` matches | Prompt was not tampered with | ✅ Structural trust established |
| `response_hash` matches | Majority output reproduced exactly | ✅ Full cryptographic match |
| `response_hash` mismatches | LLM output diverged from original | ⚠️ Flag for human review; check model deprecation notices before treating as fraud |
| `consensus_ratio` mismatches | Committee agreement changed | ⚠️ Flag; re-run with N ≥ 5 for high-stakes decisions |

**Recommendation**: For long-term auditability, the DRE executor SHOULD store
the full `normalized_output` text for each committee member alongside the
`DeterministicProof` in the deployment trace (IPFS).  This allows auditors to
verify `response_hash` from the stored output rather than re-running the LLM.

---

## 13. Third-Party Verification Package

<!-- Addresses EDGE-INT-004, EDGE-INT-025 (issue #134) -->

### 13.1 Purpose

Any third party MUST be able to independently replay the DRE pipeline and
arrive at the same `response_hash` and `consensus_ratio` **without access to
internal DRE state**, private keys, or the raw LLM API responses. This property
is the foundation of independent auditability.

> **Note**: §12.2 (Full Replay Verification) describes the step-by-step LLM
> replay procedure for validators.  This section (§13) defines the **minimum
> public data package** that enables that replay — i.e., what the prover must
> make available so a third-party verifier can execute the §12.2 procedure.

### 13.2 Minimum Verification Package

The following information is sufficient for a third-party verifier to
deterministically reconstruct and validate a `DeterministicProof`:

| Field | Source | Sufficient For |
|-------|--------|---------------|
| `prompt_bytes` | Raw canonical prompt bytes (before hashing) | Recompute `prompt_hash` and re-execute models |
| `model_ids` | From `DeterministicProof.model_ids` | Identify which models to query |
| `determinism_params` | `DeterminismParams` (temperature=0.0, seed, top_p=1.0) | Reproduce sampling conditions |
| `DeterministicProof` | The full serialised proof JSON | Verify all hash fields |

A verifier who receives these four items can:
1. Call `CanonicalPrompt.from_str(prompt_bytes.decode("utf-8"))` and verify
   `canonical_prompt.prompt_hash == proof.prompt_hash`.
2. Execute each model in `model_ids` with the given `determinism_params`.
3. Normalise each response and compute each `response_hash`.
4. Compute `ConsensusResult.build(agreements, total)` from the majority vote.
5. Verify `abs(consensus_result.consensus_ratio - proof.consensus_ratio) <= 1e-9`.
6. Verify `majority_response.response_hash == proof.response_hash`.

**No private keys, no internal database, no shared secret state is required for
step 1–6.** The verifier only needs the public prompt bytes, model IDs, and
determinism parameters — all of which are recorded in the proof or deployment trace.

### 13.3 Integration Test Verification Contract

<!-- Addresses EDGE-INT-004 (issue #134) -->

The integration test for third-party verification MUST verify the contract
defined in `specs/dre-integration-test-spec.md §5.2`. The minimum assertion set:

```python
assert result.prompt_hash_match      is True
assert result.response_hash_match    is True
assert result.consensus_ratio_match  is True   # within 1e-9
assert result.model_ids_match        is True
```

### 13.4 What the Verifier Cannot Check Without Additional Data

The following checks require additional on-chain or trust-anchor data and are
**out of scope** for the DRE data model layer:

| Check | Requires |
|-------|---------|
| Agent Ed25519 signature validity | Agent public key (from DID registry) |
| Deployment policy compliance | On-chain DeploymentContract |
| Validator quorum attestation | Validator stake registry |
| Replay deduplication (proof_id uniqueness) | AVM/trace deduplication store |

---

## 14. Edge Case Handling Reference (Extended)

<!-- Extends §7 with integration-test-specific edge cases from issue #134 -->

| EDGE ID | Scenario | Resolution |
|---------|----------|-----------|
| EDGE-INT-002 | All N committee members time out | `ConsensusResult(agreements=0, total=N)` → NONE; `DeterministicProof` with `response_hash=SHA-256(b"")` — see §6.6 |
| EDGE-INT-006 | Code normalizer alters semantics | AST check enforced at `ModelResponse` construction — see §3.3 AST section |
| EDGE-INT-023 | AST comparison for code responses | `assert_code_semantically_equivalent()` defined in §3.3 — see AST section |
| EDGE-INT-025 | Third-party verifier minimum data | `(prompt_bytes, model_ids, determinism_params, proof_json)` — see §13.2 |

---

*Spec created to address issue #30 and referenced by issue #28.*  
*Sections §10–§12 added to address issue #137 (DRE Documentation spec gaps).*  
*Sections §13–§14 added to address issue #134 (DRE Integration Tests) — EDGE-INT-002, EDGE-INT-004, EDGE-INT-006, EDGE-INT-023, EDGE-INT-025.*  
*References: `maatproof/proof.py`, `specs/llm-provider-spec.md`, `specs/avm-spec.md`, `specs/trace-verification-spec.md`, `docs/06-security-model.md`, `specs/dre-integration-test-spec.md`.*

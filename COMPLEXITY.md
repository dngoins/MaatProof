# COMPLEXITY.md — MaatProof Data Model Schema

**Issue:** [ACI/ACD Engine] Data Model / Schema
**Branch:** `impl/14-claude-sonnet`
**Model:** Claude Sonnet (Claude Code CLI)

---

## Overview

`maatproof/models.py` defines six canonical data structures and one
collection type for the MaatProof ACI/ACD pipeline.  This document
analyses algorithm choices, Big O complexity, memory usage, and
trade-offs for every operation in that module.

---

## Data Models

### `ReasoningStep`

| Operation | Time | Space | Notes |
|-----------|------|-------|-------|
| `__post_init__` | O(1) | O(1) | Scalar field validation only |
| `compute_hash(previous_hash)` | O(m) | O(m) | *m* = canonical JSON byte-length |
| `to_dict()` | O(1) | O(m) | Bounded by total string field lengths |
| `from_dict(data)` | O(1) | O(m) | Constant number of dict lookups |

**Algorithm choice — `compute_hash`:**
SHA-256 over a canonical JSON payload.  `json.dumps` with `sort_keys=True`
and no whitespace separators ensures byte-for-byte determinism regardless
of Python dict ordering or version.  Timing fields (`step_hash` itself) are
excluded from the payload so the hash is a pure function of content ×
chain position.

**Hash-chain security:**
Each step's hash incorporates `previous_hash`, so tampering with any step
invalidates every subsequent hash — O(n) re-computation required to forge a
chain of length *n*.

---

### `ReasoningProof`

| Operation | Time | Space | Notes |
|-----------|------|-------|-------|
| `__post_init__` | O(1) | O(1) | Checks scalars and non-empty step list |
| `to_dict()` | O(n) | O(n · m) | *n* = step count, *m* = avg step size |
| `from_dict(data)` | O(n) | O(n · m) | Deserialises each step |

**Merkle-chain analogy:**
Steps are ordered and each hash extends the prior, forming a structure
analogous to a Merkle chain.  The `root_hash` is the terminal hash, signed
by HMAC-SHA256.  Verification requires O(n) hash recomputations.

---

### `GateResult`

| Operation | Time | Space | Notes |
|-----------|------|-------|-------|
| `__post_init__` | O(1) | O(1) | Frozenset membership check |
| `to_dict()` | O(1) | O(1) | Fixed scalar fields |
| `from_dict(data)` | O(1) | O(1) | Constant field lookups |

**Design note — `artifact_hash` excludes timing:**
`duration_ms` is intentionally excluded from the hash so that two runs of
the same gate with the same logical outcome produce the same
`artifact_hash`.  This allows deterministic audit comparisons across
environments.

---

### `AgentResult`

| Operation | Time | Space | Notes |
|-----------|------|-------|-------|
| `__post_init__` | O(1) | O(1) | Frozenset membership check |
| `to_dict()` | O(n) | O(n · m) | Delegates to `ReasoningProof.to_dict()` |
| `from_dict(data)` | O(n) | O(n · m) | Delegates to `ReasoningProof.from_dict()` |

---

### `AuditEntry`

| Operation | Time | Space | Notes |
|-----------|------|-------|-------|
| `__post_init__` | O(1) | O(1) | Non-empty checks only |
| `to_dict()` | O(k) | O(k) | *k* = number of metadata keys |
| `from_dict(data)` | O(k) | O(k) | Dict copy of metadata |
| `make(...)` | O(1) | O(k) | `uuid4()` + `time.time()` are O(1) |

---

### `AppendOnlyAuditLog`

| Operation | Time | Space | Notes |
|-----------|------|-------|-------|
| `append(entry)` | O(1) amortised | O(1) per call | `list.append` + `set.add` |
| `__len__()` | O(1) | O(1) | `len()` on list |
| `__iter__()` | O(n) total | O(n) | Shallow list copy for safety |
| `to_list()` | O(n · k) | O(n · k) | One `to_dict` per entry |
| `from_list(data)` | O(n · k) | O(n) | One `AuditEntry` per item |

**Duplicate detection:**
Entry IDs are tracked in a Python `set`, giving O(1) average-case lookup
for the duplicate-ID guard in `append`.  Worst case is O(n) due to hash
collisions, but UUID4 collisions are astronomically rare.

**Append-only enforcement:**
The internal `_entries` list and `_seen_ids` set are private attributes with
no public mutator methods.  External code can iterate via `__iter__` (which
yields from a shallow copy), preventing mutation through the iterator.

---

### `PipelineConfig`

| Operation | Time | Space | Notes |
|-----------|------|-------|-------|
| `__post_init__` | O(b) | O(1) | *b* = `len(secret_key)`; `not bytes` is O(b) |
| `to_dict()` | O(b) | O(b) | `bytes.hex()` is O(b) |
| `from_dict(data)` | O(b) | O(b) | `bytes.fromhex()` is O(b) |

**Secret-key serialisation:**
`secret_key` (bytes) is hex-encoded in `to_dict` and decoded in
`from_dict`.  Hex was chosen over base64 for readability in logs and
because it is unambiguously supported by `bytes.hex()` / `bytes.fromhex()`
without external dependencies.  The key is never logged as plaintext.

---

## Memory Usage Patterns

| Model | Heap footprint (per instance) |
|-------|-------------------------------|
| `ReasoningStep` | O(m) — dominated by string fields |
| `ReasoningProof` | O(n · m) — *n* steps × *m* string bytes |
| `GateResult` | O(m) — dominated by `details` string |
| `AgentResult` | O(n · m) — dominated by embedded proof |
| `AuditEntry` | O(k) — dominated by metadata dict |
| `AppendOnlyAuditLog` | O(n · k) — *n* entries × *k* metadata keys |
| `PipelineConfig` | O(b) — dominated by `secret_key` length |

All models use Python's garbage-collected heap.  No manual memory
management is required.  The largest objects are `ReasoningProof` and
`AgentResult`, which embed the full reasoning chain.

---

## Concurrency Model

This module is **not thread-safe by design**.  It provides pure data
models with no shared mutable state between instances.

The one exception is `AppendOnlyAuditLog`, which uses a plain Python list
and set that are *not* thread-safe under concurrent writes.  In a
multi-threaded context, callers must synchronise `append` calls externally
(e.g. with `threading.Lock`).

The pipeline itself is single-threaded per the current implementation;
concurrency safety can be added at the `OrchestratingAgent` layer without
changing the data models.

---

## Trade-offs

| Decision | Alternative considered | Why this choice |
|----------|----------------------|-----------------|
| SHA-256 for hashing | SHA-3, BLAKE3 | SHA-256 is universally available in stdlib `hashlib`, FIPS-approved, and well-understood by auditors |
| `json.dumps(sort_keys=True)` canonical form | `msgpack`, `cbor`, `protobuf` | No external dependencies; human-readable; deterministic across Python versions |
| Hex encoding for `secret_key` in `to_dict` | Base64 | `bytes.hex()` / `bytes.fromhex()` require no imports and produce URL-safe output |
| Frozen-by-convention `AuditEntry` | `@dataclass(frozen=True)` | `frozen=True` prevents any mutation but also prevents `__post_init__` from assigning computed fields; convention is sufficient for the audit use case |
| `AppendOnlyAuditLog` `__iter__` shallow copy | Direct iterator | Prevents callers from modifying `_entries` through the iterator; O(n) cost is acceptable since the log is expected to be small relative to total pipeline execution time |
| `status` as `str` in `GateResult` | `GateStatus` enum | Keeps `models.py` dependency-free; callers that need enum semantics import from `layers.deterministic` |
| `decision` as `str` in `AgentResult` | `AgentDecision` enum | Same rationale — `models.py` is a standalone schema module |

---

*Generated by Claude Sonnet (Branch A) on 2026-04-22*

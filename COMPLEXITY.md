# COMPLEXITY.md — ACI/ACD Engine Data Model

Scope: the core data structures for the MaatProof ACI/ACD engine —
`ReasoningStep`, `ReasoningProof`, `AuditEntry`, `PipelineConfig`,
`GateResult`, and `AgentResult` (Issue #14, part of #13).

This document summarises the algorithmic choices, their Big-O costs, memory
patterns, concurrency posture, and the trade-offs that were made. Every
function in the implementation also carries inline Big-O annotations as
comments so the analysis is co-located with the code it describes.

## Notation

| Symbol | Meaning                                                               |
|--------|-----------------------------------------------------------------------|
| `k`    | Number of `ReasoningStep`s in a `ReasoningProof`.                     |
| `g`    | Number of gates registered on a `DeterministicLayer` / `AgentLayer`.  |
| `n`    | Number of `AuditEntry` rows in the orchestrator's append-only log.    |
| `m`    | Total byte length of a step's serialized fields (`context`, etc.).    |

## Per-model summary

### `ReasoningStep`

Hash-chained record of one reasoning move. Backed by a frozen-ish dataclass
(the `step_hash` field is populated by the builder and intentionally excluded
from equality so builders and verifiers can compare step *content*
independently of the chain position).

| Operation                    | Time          | Space  | Notes                                                               |
|------------------------------|---------------|--------|---------------------------------------------------------------------|
| `compute_hash(previous_hash)`| `O(m)`        | `O(m)` | Canonical JSON then single SHA-256 pass.                            |
| `to_dict`                    | `O(1)`        | `O(1)` | Constant number of fields copied by reference.                      |
| `from_dict`                  | `O(1)`        | `O(1)` | Field lookup + dataclass construction.                              |

Determinism is guaranteed by (a) `json.dumps(sort_keys=True, separators=(",", ":"))`,
which forces a canonical byte sequence for any input mapping, and (b) SHA-256
being a pure function. Two steps with identical fields therefore always produce
the identical digest.

### `ReasoningProof`

Ordered sequence of `ReasoningStep`s wrapped with a root hash and an HMAC-SHA256
signature. Immutable in practice — `ProofBuilder.build` returns a freshly
constructed instance; direct mutation is a test-only concern.

| Operation              | Time          | Space  | Notes                                                                          |
|------------------------|---------------|--------|--------------------------------------------------------------------------------|
| `ProofBuilder.build`   | `O(k · m)`    | `O(k)` | One SHA-256 per step, one HMAC-SHA256 over `chain_id + root_hash`.             |
| `ProofVerifier.verify` | `O(k · m)`    | `O(1)` | Recomputes each step hash; constant-time `hmac.compare_digest` on the sig.     |
| `to_dict`              | `O(k)`        | `O(k)` | Delegates per-step to `ReasoningStep.to_dict`.                                 |
| `from_dict`            | `O(k)`        | `O(k)` | Delegates per-step to `ReasoningStep.from_dict`.                               |

Round-trip invariant: `ReasoningProof.from_dict(p.to_dict())` produces a
proof whose `verify` result is identical to `p`'s. This is asserted in
`tests/test_data_model.py::TestReasoningProofDataModel`.

### `AuditEntry`

Single row in the orchestrator's append-only audit log.

| Operation         | Time                   | Space                  | Notes                                                         |
|-------------------|------------------------|------------------------|---------------------------------------------------------------|
| `new_entry_id`    | `O(1)`                 | `O(1)`                 | UUIDv4 — random, collision-resistant (~2⁻¹²² per pair).       |
| `to_dict`         | `O(|metadata|)`        | `O(|metadata|)`        | Shallow metadata copy to prevent aliasing surprises.          |
| `from_dict`       | `O(|metadata|)`        | `O(|metadata|)`        | Same pattern in reverse.                                      |

Append-only guarantee is enforced by the `OrchestratingAgent`: entries are
only ever `list.append`-ed to `_audit_log`. There is no public API that
removes or mutates a stored `AuditEntry`. Reading the log (`get_audit_log`)
returns fresh `to_dict` copies so callers cannot mutate in place.

### `PipelineConfig`

Configuration object consumed by `ACIPipeline` / `ACDPipeline`.

| Operation       | Time              | Space             | Notes                                                                 |
|-----------------|-------------------|-------------------|-----------------------------------------------------------------------|
| `__post_init__` | `O(1)`            | `O(1)`            | Validates `secret_key`, `name`, and `max_fix_retries`.                |
| `to_dict`       | `O(|secret_key|)` | `O(|secret_key|)` | Base64-encodes the secret so the result is JSON-safe.                 |
| `from_dict`     | `O(|secret_key|)` | `O(|secret_key|)` | Accepts either `secret_key_b64` (preferred) or raw `secret_key` bytes.|

Trade-off: we serialize the secret as base64 rather than hex. Base64 is ~33%
overhead vs. hex's 100%, and the `validate=True` flag on `b64decode` raises
on non-base64 input, surfacing corruption early. The serialized form is still
sensitive and must be protected like any other secret.

### `GateResult`

Signed outcome of one deterministic gate. The hash of `(gate_name, status,
details)` is computed at creation time by `DeterministicGate.run` — the
resulting dataclass only stores it.

| Operation    | Time   | Space  | Notes                                                            |
|--------------|--------|--------|------------------------------------------------------------------|
| `to_dict`    | `O(1)` | `O(1)` | Fixed-size fields; `status` is flattened to its enum value.      |
| `from_dict`  | `O(1)` | `O(1)` | Rejects invalid `status` strings with `ValueError`.              |

`duration_ms` is deliberately **excluded** from the content-addressed
`artifact_hash` so that two runs of the same gate with the same outcome
produce the same artifact hash regardless of wall-clock variability.

### `AgentResult`

Agent-layer decision plus its signed `ReasoningProof`.

| Operation    | Time    | Space   | Notes                                                             |
|--------------|---------|---------|-------------------------------------------------------------------|
| `to_dict`    | `O(k)`  | `O(k)`  | Delegates to the embedded `ReasoningProof.to_dict`.               |
| `from_dict`  | `O(k)`  | `O(k)`  | Rejects invalid `decision` strings; verifies-after-rehydrate in tests. |

## Cross-cutting algorithmic choices

### Hash chaining vs. Merkle trees

We use a straightforward linear hash chain (each step incorporates the
previous step's hash) rather than a Merkle tree.

- Verification cost is `O(k)` either way for a *full* proof.
- Chaining keeps the builder/verifier code tiny and removes a class of
  ordering ambiguity (no sibling-order conventions to get wrong).
- Partial / inclusion proofs would benefit from a Merkle structure, but the
  current use case is "always verify the whole chain," so the simpler
  structure is the right default. The tree can be added later without
  breaking the on-wire shape: an upgraded verifier would accept chain-shaped
  proofs as a degenerate left-spine tree.

### Canonical serialization

Every hash input goes through `json.dumps(..., sort_keys=True,
separators=(",", ":"))`. Alternatives considered:

- **CBOR / protobuf**: deterministic and compact, but introduces a non-stdlib
  dependency. Rejected for the data-model layer — Python 3.11 stdlib only is
  a stated constraint.
- **Custom field concatenation**: brittle, easy to break with future schema
  changes.
- **Pickle**: non-deterministic across Python versions and unsafe to
  deserialize. Rejected outright.

### HMAC-SHA256 vs. Ed25519

We sign the root hash with HMAC-SHA256 using a symmetric key
(`PipelineConfig.secret_key`). This gives us:

- Stdlib-only implementation (`hmac`, `hashlib`).
- Constant-time comparison via `hmac.compare_digest` (resists timing oracles).
- Low CPU cost compared to an asymmetric signature.

Trade-off: any party that can verify proofs can also forge them. For
cross-org verification we would add an asymmetric signature on top; that is
explicitly deferred to a later issue.

### UUIDv4 for entry IDs

`AuditEntry.new_entry_id` returns `uuid.uuid4()`. Collision probability is
negligible for log volumes in the realistic range (even at 10⁹ entries the
birthday-collision probability is well below 10⁻²⁰). UUIDv7/ULIDs would give
sortable IDs, but `timestamp` already provides the ordering signal, so we
keep the stdlib-only choice.

## Memory usage patterns

- `ReasoningChain` / `ProofBuilder` holds all steps in memory to compute the
  root hash; peak is `O(k · m)`. For the expected size of a reasoning chain
  (`k` in the tens, `m` in the kilobytes) this is bounded by ~MB, well within
  process limits.
- `AuditEntry` metadata is stringified in `OrchestratingAgent._record_audit`,
  producing a bounded-size copy per event. The log itself grows linearly in
  the number of events; callers that need long-running orchestrators should
  periodically snapshot and truncate the log.
- `PipelineConfig.to_dict` allocates one base64 string per call. The secret
  key never leaves `bytes` form inside the running process.

## Concurrency model

The data model is intentionally **single-threaded** today:

- Dataclasses are plain Python objects with no internal locking.
- `OrchestratingAgent._audit_log` is a plain `list`; `list.append` is
  atomic under CPython's GIL but the overall `_record_audit` sequence
  (build entry, append) is not.
- All hashes are pure functions and safe to call from any thread.

If multi-threaded event dispatch is introduced later, the only mutation
point that needs a lock is `OrchestratingAgent._audit_log`. Everything
else is immutable-after-construction in practice.

## Trade-offs explicitly made

1. **Stdlib-only.** No external dependencies in the data-model layer. This
   forces us to use HMAC-SHA256 instead of Ed25519 and JSON instead of CBOR,
   but keeps the trust surface tiny.
2. **Base64 over hex for secret serialization.** Smaller payload, still
   ASCII-safe, and `validate=True` catches corruption at decode time.
3. **Hash chain over Merkle tree.** Simpler code, same verification cost for
   full-chain proofs, easy to upgrade later without breaking wire format.
4. **UUIDv4 entry IDs.** Stdlib-only and collision-safe; ordering is carried
   by `timestamp`, which already exists.
5. **Frozen-by-convention, not enforced.** Dataclasses are not
   `frozen=True` so `ProofBuilder` can construct sealed `ReasoningStep`
   instances cleanly. Callers should treat rehydrated values as immutable.

## Traceability

| Acceptance criterion (Issue #14)                              | Evidence                                                                                             |
|---------------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| All dataclasses have complete type annotations.               | `maatproof/proof.py`, `orchestrator.py`, `pipeline.py`, `layers/deterministic.py`, `layers/agent.py`.|
| `to_dict` / `from_dict` round-trip without data loss.         | `tests/test_data_model.py` — dedicated round-trip test per model.                                    |
| `ReasoningStep.compute_hash` is deterministic.                | `tests/test_proof.py::TestReasoningStep::test_compute_hash_is_deterministic` + `test_data_model.py`. |
| `AuditEntry` includes `entry_id`, `event`, `timestamp`, …     | `tests/test_data_model.py::TestAuditEntryDataModel::test_roundtrip_preserves_all_fields`.            |
| `PipelineConfig` validates required fields (non-empty secret).| `tests/test_data_model.py::TestPipelineConfigDataModel::test_empty_secret_key_rejected` (+ siblings).|
| All models documented with docstrings.                        | Module + class + method docstrings throughout the five files listed above.                           |

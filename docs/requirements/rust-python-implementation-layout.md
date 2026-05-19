# Rust and Python implementation layout

## User story

As a MaatProof maintainer, I want the reference Python implementation isolated from a new Rust implementation, so that both language implementations can evolve and be verified independently.

## Acceptance criteria

1. The existing Python package, tests, and Python project metadata are stored under `PYTHON/`.
2. A Rust library implementation is stored under `RUST/` and builds with Cargo.
3. The Rust implementation covers the core MaatProof model: canonical hashes, HMAC signatures, reasoning proofs, policy/evidence checks, VRP derivations, validator quorum, deployment certificates, ledger entries, AVM traces, deterministic gates, agent gates, orchestrator, and pipeline helpers.
4. Each implementation folder documents how to run its local checks.
5. The repository README points readers to both implementation folders.

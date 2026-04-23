# MaatProof Constitution

This document defines the governing rules and standards for all MaatProof specifications, documentation, and development practices. All contributors — human and agent — are bound by these principles.

---

## Article I — Diagrams and Visual Documentation

**§1.1 — Mermaid is the standard**

All charts, diagrams, flows, and visual representations in any spec file (`specs/`), documentation file (`docs/`), or agent definition (`agents/`) **must** use [Mermaid](https://mermaid.js.org/) diagram syntax inside a fenced `mermaid` code block.

```
```mermaid
flowchart LR
    A --> B
```
```

**§1.2 — No ASCII diagrams in spec files**

ASCII art diagrams (box-drawing characters, manual alignment) are prohibited in spec files. They are not version-diffable, not accessible, and not renderable in GitHub or tooling.

**§1.3 — Diagram types by use case**

| Use case | Mermaid type |
|---|---|
| System architecture / component flow | `flowchart` |
| Sequence of interactions | `sequenceDiagram` |
| State machines | `stateDiagram-v2` |
| Data models / entity relationships | `erDiagram` |
| Timelines / roadmaps | `gantt` |
| Class hierarchies | `classDiagram` |

**§1.4 — Every spec must include at least one diagram**

Any spec file that describes a component, protocol, or process must include at least one Mermaid diagram illustrating its core flow or structure.

---

## Article II — Deployment Authorization

**§2.1 — ADA is the protocol default for production authorization.**

The Autonomous Deployment Authority (ADA) authorizes production deployments through
cryptographic proof: DRE committee convergence, VRP checker validation, validator consensus,
and runtime guard declaration. Human approval is **not** a universal protocol mandate.

**§2.2 — Human approval is a policy-configurable gate.**

A Deployment Contract may declare a `require_human_approval` rule for workloads that need it.
When declared, a signed Ed25519 human attestation is required as one of the ADA policy gates.
Regulated environments (SOC2, HIPAA, SOX) should declare this rule.

**§2.3 — Autonomous authorization must produce a verifiable ADA record.**

Every autonomous production deployment must emit a signed `AdaAuthorization` record stored
on-chain, identifying which of the 7 conditions were verified and by whom. See [`specs/ada-spec.md`](../specs/ada-spec.md).

---

## Article III — Auditability

**§3.1 — Every deployment decision must be traceable to a signed artifact on the MaatProof chain.**

"I don't know why this deployed" is a protocol violation, not just an operational gap.

---

*This constitution may be amended by governance vote. All amendments must be recorded on-chain.*

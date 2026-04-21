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

## Article II — Human Approval

**§2.1 — No autonomous agent may finalize a production deployment without a human approval attestation.**

Human approval is a first-class primitive in the MaatProof protocol, not an optional gate. This applies to all deployments regardless of agent confidence level.

---

## Article III — Auditability

**§3.1 — Every deployment decision must be traceable to a signed artifact on the MaatProof chain.**

"I don't know why this deployed" is a protocol violation, not just an operational gap.

---

*This constitution may be amended by governance vote. All amendments must be recorded on-chain.*

# CLAUDE.md

## Team Constitution
This repo follows the Spec Kit constitution defined in CONSTITUTION.md.
Every Claude Code session in this repo must read that file before generating any code, spec, Bicep, or workflow.

## Key Rules (summary)
- Spec first, code second — no implementation without a user story and acceptance criteria
- Agents draft, humans approve — all AI output requires human review before merge
- One function or pipeline per PR — keep changes small and reversible
- Every artifact must trace to an acceptance criteria

## Repo Layout
- `docs/constitution/` — team rules (always read this first)
- `docs/requirements/` — feature specs per use case
- `docs/architecture/` — design docs and ADRs
- `templates/` — potential code templates agents must reference
- `generated/` — agent output, never hand-edited directly
- `.github/agents/` — agent persona files (planner, developer, orchestrator)

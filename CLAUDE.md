# CLAUDE.md

## Team Constitution
This repo follows the constitution defined in CONSTITUTION.md.
Every Claude Code session in this repo must read that file before generating any code, spec, Bicep, or workflow.

## Key Rules (summary)
- Spec first, code second — no implementation without a user story and acceptance criteria
- Agents draft, humans approve — all AI output requires human review before merge
- One function or pipeline per PR — keep changes small and reversible
- Every artifact must trace to an acceptance criteria
- 4-branch competing implementations judged by Big O, cost, and quality before human selects winner

## Repo Layout
- `CONSTITUTION.md` — team rules and invariants (always read this first)
- `docs/requirements/` — feature specs and backlog
- `docs/architecture/` — design docs and ADRs
- `docs/reports/` — cost estimation and analysis reports
- `.github/agents/` — agent persona files (see Agent Pipeline below)
- `.github/workflows/` — GitHub Actions workflows for each agent
- `templates/` — code templates agents must reference
- `generated/` — agent output, never hand-edited directly
- `tests/` — test suite (run with `python -m pytest tests/ -v`)

## Agent Pipeline

```
Planner → Spec Edge Case Tester → Cost Estimator → Development (4 branches) → Judging → PR Review → QA → Documenter → Release
```

| Agent | Persona File | Trigger Label |
|-------|-------------|---------------|
| Planner | `.github/agents/planner-agent.md` | `agent:planner` |
| Spec Edge Case Tester | `.github/agents/spec-edge-case-tester-agent.md` | `agent:spec-edge-test` |
| Cost Estimator | `.github/agents/cost-estimator-agent.md` | `agent:cost-estimator` |
| Development (4 concurrent) | `.github/agents/development-agent.md` | `agent:developer` |
| Judging | `.github/agents/judging-agent.md` | `agent:judge` |
| PR Review | `.github/agents/development-agent.md` (Mode 2) | PR opened/synced |
| QA Testing | `.github/agents/qa-testing-agent.md` | `agent:qa` |
| Documenter | `.github/agents/documenter-agent.md` | `agent:documenter` |
| Release | `.github/agents/release-agent.md` | `agent:release` |
| Orchestrator | `.github/agents/orchestrator-agent.md` | Issue/push/PR events |

## Label Reference

| Category | Labels |
|----------|--------|
| **Agent triggers** | `agent:planner`, `agent:spec-edge-test`, `agent:cost-estimator`, `agent:developer`, `agent:judge`, `agent:qa`, `agent:documenter`, `agent:release` |
| **Status gates** | `spec:passed`, `cost:estimated`, `dev:complete`, `judge:complete`, `qa:passed`, `docs:updated`, `review:passed`, `loop:complete` |
| **Roles** | `role:ba`, `role:architect`, `role:developer`, `role:qa`, `role:release` |
| **Implementation** | `impl:claude-sonnet`, `impl:claude-opus`, `impl:gpt53-codex`, `impl:gpt54` |
| **Use cases** | `use-case:outbound`, `use-case:inbound` |

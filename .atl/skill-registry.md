# Skill Registry — scraping_masive

Generated: 2026-04-17

## User Skills

| Skill | Trigger Context |
|-------|----------------|
| `branch-pr` | Creating pull requests, opening PRs, preparing changes for review |
| `database-schema-design` | Creating new database schemas, designing tables, planning data models for PostgreSQL |
| `find-skills` | User asks "how do I do X", "find a skill for X", looking for installable capabilities |
| `go-testing` | Writing Go tests, using teatest, adding test coverage (N/A for this project) |
| `issue-creation` | Creating GitHub issues, reporting bugs, requesting features |
| `judgment-day` | Parallel adversarial code review protocol |
| `sdd-apply` | Implementing tasks from a change (launched by orchestrator) |
| `sdd-archive` | Archiving a completed change (launched by orchestrator) |
| `sdd-design` | Writing technical design documents (launched by orchestrator) |
| `sdd-explore` | Exploring ideas before committing to a change (launched by orchestrator) |
| `sdd-init` | Initializing SDD context in a project |
| `sdd-onboard` | Guided end-to-end SDD walkthrough |
| `sdd-propose` | Creating change proposals (launched by orchestrator) |
| `sdd-spec` | Writing specifications (launched by orchestrator) |
| `sdd-tasks` | Breaking down changes into task checklists (launched by orchestrator) |
| `sdd-verify` | Validating implementation against specs (launched by orchestrator) |
| `skill-creator` | Creating new AI agent skills |
| `skill-registry` | Creating or updating the skill registry |

## Project-Level Skills

| Skill | Path | Notes |
|-------|------|-------|
| `new-platform` | `.claude/skills/new-platform.md` | Project-specific skill |

## Project Conventions

No project-level convention files found (CLAUDE.md, AGENTS.md, .cursorrules, GEMINI.md).

## Compact Rules

### Python / FastAPI (scraping_masive)
- Follow layered architecture: router → service → model
- Use async/await throughout; propagate context properly
- SQLAlchemy async session scope: per-request, never reuse across requests
- Always use `http.status` equivalents (FastAPI `status.HTTP_*` constants)
- Config via env vars only (TEMP_DIR, LOG_DIR, DATABASE_URL)
- Docker Compose is the local dev environment (db + api)

### SDD Phases
- `sdd-apply`: follow routers/ → services/ → models/ pattern, async-first
- `sdd-verify`: manual Docker Compose verification when no test runner available
- `sdd-spec`: document HTTP status codes and response shapes in scenarios

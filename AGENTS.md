---
type: context
id: agents-root
topology_role: root
authority: authoritative
---

# supportops-agent — AI Assistant Guide

This is the primary entry point for AI assistants working in this project.

Read this file first on every session start.

---

## Project

**Name:** supportops-agent  
**Language:** typescript  
**AI Tool:** claude  
**MONOLITH Root:** `.monolith/`

<!-- Add project description here -->

---

## Boot Sequence

On every session start, read in this order:

1. This file (AGENTS.md)
2. `.monolith/context/maps/project-map.md`
3. `.monolith/context/maps/routing-map.md` — classify task type
4. `.monolith/memory/working/active.md` — current phase and story
5. `.monolith/context/maps/roadmap-index.md`
6. `.monolith/protocols/governance/memory-governance.md`
7. Relevant skill from `.monolith/skills/` (resolved via routing-map)
8. `.monolith/learning/injection-map.md` — load relevant lessons
9. `.monolith/protocols/cognition/cognitive-modes.md` — apply behavioral constraints

See full sequence: `.monolith/protocols/boot/boot-sequence.md`

---

## Navigation

| What              | Where                                               |
| ----------------- | --------------------------------------------------- |
| Active story      | `.monolith/memory/working/active.md`        |
| Project map       | `.monolith/context/maps/project-map.md`     |
| Routing decisions | `.monolith/context/maps/routing-map.md`     |
| Skills            | `.monolith/skills/INDEX.md`                 |
| Protocols         | `.monolith/protocols/boot/boot-sequence.md` |
| Roadmap           | `.monolith/context/maps/roadmap-index.md`   |
| Lessons           | `.monolith/learning/INDEX.md`               |
| Governance rules  | `.monolith/governance/`                     |

---

## Mission Lifecycle (Coordinatorium v0.1)

```
monolith mission start --title "..." --system "..."
monolith mission list
monolith mission show <id>
monolith mission finish <id> --result completed|failed|cancelled [--summary "..."]
monolith mission checkpoint <missionId> --summary "..." [--remaining "..." ...] [--context "..."]
monolith mission checkpoints <missionId>
```

This records development missions in `.monolith/state.json`,
independent of any single AI session's memory. **This is not
runtime-enforced in v0.1:** nothing prevents an AI session from editing
code without ever calling `mission start`/`finish`. It is a recommended
discipline, not a gate.

---

## Project Brief

```
monolith brief [--max-chars N] [--strict]
```

Prints a deterministic, read-only, bounded summary of current project
state (State Keeper status, active missions and latest checkpoints,
mandatory protocols, current story, accepted ADRs), with the source of
each section. It supplements the Boot Sequence; it does not replace it.
The first line is `Completeness: COMPLETE` or `INCOMPLETE`; exit code 2
under `--strict` means INCOMPLETE. ADR directories are
listed in `monolith.config.json` under `contextGateway.adrDirs`; a new instance
sets `["docs/adr"]`, the only ADR location.

**Mission Checkpoints (COORDINATORIUM-002):** an `in_progress` mission can
accumulate durable, append-only checkpoints (`summary` required,
`remainingSteps` via repeated `--remaining`, optional `context`) —
immutable once created, no update/delete command. Rejected for a
completed/failed/cancelled mission; `mission checkpoints` keeps listing a
finished mission's history regardless.

---

## Core Rule

One story at a time. Vertical slice first. No autonomous capability expansion.

Read `.monolith/protocols/governance/core-invariants.md` before starting any task.

<!-- monolith:begin bootstrap sha256=fb39bfbeaaff3832 -->
## MONOLITH agent entry point

This project uses MONOLITH. At the start of every session, and before acting on
any request that may match a skill (status, finalize story, saving or listing
ideas, ...), read `.monolith/AGENT.md` and follow it. It lists the
available skills and routing; skills added later appear there, so this section
does not change when they do.
<!-- monolith:end bootstrap -->

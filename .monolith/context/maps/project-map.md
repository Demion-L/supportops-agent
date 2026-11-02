---
type: context
id: project-map
topology_role: root
authority: authoritative
---

# Project Map — supportops-agent

Navigation guide for the cognitive architecture of this project.

---

## Memory

| Location                                                        | Purpose                 |
| --------------------------------------------------------------- | ----------------------- |
| `.monolith/memory/working/active.md`                    | Active phase and story  |
| `.monolith/memory/working/story-index.md`               | Implementation history  |
| `.monolith/memory/long-term/decisions.md`               | Non-ADR decisions (`DEC-…`) |
| `.monolith/memory/long-term/architecture-principles.md` | Core principles         |
| `.monolith/memory/long-term/tech-debt.md`               | Tech debt registry      |
| `.monolith/memory/long-term/execution-history.md`       | Finalization log        |

---

## Context

| Location                                           | Purpose                |
| -------------------------------------------------- | ---------------------- |
| `.monolith/context/maps/routing-map.md`    | Task type → activation |
| `.monolith/context/maps/roadmap-index.md`  | Phases and progress    |
| `.monolith/context/maps/semantic-map.md`   | Where truth lives      |
| `.monolith/context/maps/dependency-map.md` | Artifact dependencies  |
| `.monolith/context/boot/HANDOFF.md`        | Session transfer       |

---

## Skills

| Skill                                                | Trigger          |
| ---------------------------------------------------- | ---------------- |
| `.monolith/skills/finalize-story/SKILL.md`   | finalize story   |
| `.monolith/skills/current-status/SKILL.md`   | status check     |
| `.monolith/skills/global-status/SKILL.md`    | global overview  |
| `.monolith/skills/self-improvement/SKILL.md` | self improvement |

---

## Protocols

| Protocol                                                      | Purpose               |
| ------------------------------------------------------------- | --------------------- |
| `.monolith/protocols/boot/boot-sequence.md`           | Session startup order |
| `.monolith/protocols/cognition/cognitive-modes.md`    | Behavioral modes      |
| `.monolith/protocols/governance/memory-governance.md` | Memory rules          |
| `.monolith/protocols/governance/core-invariants.md`   | Always-on rules       |

---

## Knowledge Base

| Location                              | Purpose                                              |
| ------------------------------------- | ---------------------------------------------------- |
| `MANIFESTO.md`                        | Engineering foundation — why this project exists     |
| `docs/README.md`                      | Knowledge base navigation index                      |
| `docs/DOCUMENTATION_PRINCIPLES.md`   | Documentation rules and layer hierarchy              |
| `docs/adr/`                           | Architecture Decision Records                        |
| `docs/adr/README.md`                  | The only ADR index (created with the first ADR)      |
| `docs/architecture/`                  | Current system description                           |

Add `docs/research/` and `docs/vision/` layers if this project's own
knowledge base needs them — they are not provisioned by default.

---

## Project Structure

<!-- Describe your project's source code structure here. -->
<!-- Example: -->
<!-- | Directory | Purpose | -->
<!-- |-----------|---------|  -->
<!-- | `src/` | Application source code | -->
<!-- | `packages/` | Shared packages | -->
<!-- | `apps/` | Application entry points | -->

---
type: context
id: roadmap-epoch-2-hermes
topology_role: node
authority: authoritative
---

# Epoch II Roadmap — HERMES

**Зарегистрировано:** 2026-11-27 по директиве владельца («HERMES — Epoch II: Roadmap Registration & Implementation Launch»).
**Идентичность:** HERMES — Modular Multi-Tenant Business Orchestration Platform. Наследует SupportOps Agent
(код, Git history, архитектурные решения, тесты). Модульная эволюция существующего ядра, не новая система.
**Epoch I (SupportOps, 2026-08-08 … 2026-11-17)** сохранена без изменений: `roadmap-index.md` (Phases 1–7), `context/planned/`,
`context/retrospective/`. Идентификаторы Stories `SUPPORTOPS-*` не переименовываются.
**Исследование GENESIS:** `docs/hermes/00…08`, `docs/hermes/FINAL-REPORT.md` (на момент записи не закоммичено).

Статусы: PLANNED — нет Story/Mission; IN PROGRESS — идёт работа; DONE — принято и проверено.
Все пункты фаз — **предварительная декомпозиция**, а не утверждённые технические решения. Будущие архитектурные
гипотезы (в т.ч. HX-1, путь II, Capability Registry) **не** считаются решениями. Missions для будущих работ не регистрируются.

## Фазы

| Фаза | Название | Статус | Примечание |
| ---- | -------- | ------ | ---------- |
| 00 | Genesis | DONE (документы не закоммичены) | Идентичность, исследование, независимое ревью, Epoch II Roadmap. Hygiene — частично (см. Phase 01) |
| 01 | Engineering Consolidation | IN PROGRESS | Единая проверенная baseline. Критерии: директива §VII |
| 02 | Modular Execution Core | PLANNED | Начинать только после Phase 01; гейт — HX-1 (решение владельца) |
| 03 | Multi-Tenant Orchestration | PLANNED | |
| 04 | AI Dispatcher | PLANNED | |
| 05 | Communication & Integration | PLANNED | Email — высокий внешний риск, не в первый эксперимент |
| 06 | Agent Delegation | PLANNED | KAIROS — спецификаций в репозитории нет |
| 07 | Durable Orchestration | PLANNED | Пересекается с Epoch I STATE-001/002 |
| 08 | Outcome Intelligence | PLANNED | Пересекается с EVAL-002 |
| 09 | STC Compatibility | PLANNED | Спецификаций STC в репозитории нет |
| 10 | Commercial Readiness | PLANNED | |

Состав по фазам (из директивы):

- **00:** Project Identity Transition; Architecture Research; Independent Architecture Review; Repository Hygiene; Epoch II Roadmap.
- **01:** Git & Worktree Consolidation; API-004 Completion; Evaluation Framework Integration; Security Regression;
  PostgreSQL Regression; Codebase Organization; Technical Identity Migration; Verified Engineering Baseline.
- **02:** Domain Coupling Analysis; Capability Contracts; Configurable Capability Registry; Parameterized Agent Runtime;
  SupportOps Domain Separation; Second Domain Experiment; Architectural Validation.
- **03:** Trusted Execution Context; Tenant Capability Configuration; Tenant-Scoped Data Access; Tenant-Scoped Authorization;
  Service-to-Runtime Bridge; Cross-Tenant Security Evaluation; Multi-Tenant Architecture Validation.
- **04:** Property Maintenance Domain; Request Classification; Information Collection; Urgency & Business Policies;
  Work Order Management; Contractor Routing; Human Escalation; Dispatcher Evaluation; Commercial Demonstrator.
- **05:** Channel Abstraction; Integration Contracts; Email Inbound; Email Outbound; Reliable Delivery & Idempotency;
  Email Deliverability; Bounce & Complaint Handling; Webhook Integration; Integration Evaluation.
- **06:** Agent Invocation Contracts; Delegated Authorization; Shared Execution Budgets; Specialized Sub-Agent Experiment;
  External Agent Integration; KAIROS Integration Experiment; Delegation Security Evaluation.
- **07:** Durable Execution State; Worker Architecture; Pause & Resume; Human Tasks; Sequential Execution; Parallel Execution;
  Partial Failure Recovery; Cancellation & Timeouts; Side-Effect Control; Orchestration Reliability Evaluation.
- **08:** Domain Evaluation Profiles; Business Outcome Metrics; Token Accounting; Financial Cost Accounting;
  Execution Strategy Comparison; Live Evaluation; AGORA Compatibility; FEMIDA Compatibility.
- **09:** Capability Metadata; Business Configuration Contract; Configuration Validation; Declarative Execution;
  STC Integration Experiment; End-to-End Business Composition.
- **10:** Tenant Provisioning; Secrets Management; Operational Security; Resource Limits & Quotas; Data Retention & Privacy;
  Deployment; Operational Monitoring; Backup & Recovery; Commercial Pilot.

## Сопоставление существующих Stories с Epoch II (идентификаторы и история сохраняются)

| Story (Epoch I) | Epoch II фаза | Фактический статус |
| --------------- | ------------- | ------------------ |
| SUPPORTOPS-SEC-003 | 01 (Security Regression), база для 03 | Код и регрессия в `main`; Story не финализирована в MONOLITH (`active.md` противоречив) |
| SUPPORTOPS-API-004 | 01 (API-004 Completion), основа 03 | M01/M02 интегрированы в `main` (INTEGRATION-GATE-01); M03/M04 не начаты; `POST /v1/tickets` ждёт решения владельца |
| SUPPORTOPS-EVAL-001 | 01 (Evaluation Framework Integration), основа 08 | Интегрирован в `main` (`6af9bfb`): deterministic harness + Record & Replay, 27 синтетических сценариев; Evaluation Framework Integration завершена. Метрики синтетические — не оценка реальной LLM |
| SUPPORTOPS-EVAL-002 | 08 (Live Evaluation) | PLANNED; дизайн сохранён в `docs/evaluation/eval-m02-wp2-live-evaluation-design.md`, **реализация не начата** (перенесена на E2-Phase 08) |
| SUPPORTOPS-EVAL-003 | 02 (Domain Coupling Analysis) | PLANNED; пересекается с GENESIS — судьбу решает владелец |
| SUPPORTOPS-ACT-001…004 | 04, 07 (Human Escalation, Human Tasks, Side-Effect Control) | PLANNED; пересмотр scope — решение владельца |
| SUPPORTOPS-STATE-001/002 | 07 | PLANNED; пересмотр scope после HX-1 |
| SUPPORTOPS-SEC-004 | 03 (Cross-Tenant Security Evaluation) | PLANNED |
| SUPPORTOPS-API-001…003 | 01/10 | PLANNED; API-001 фактически покрыта API-004 M01 |

Новых Stories регистрация не создаёт; дублей нет. Stories для фаз 02+ создаются по решению владельца перед стартом фазы.

## Реальные зависимости (на 2026-11-27)

- Phase 02 ← Phase 01 (baseline) ← решение владельца по HX-1.
- API-004 M03 (SupportAgent integration) ← SEC-003 в `main` (выполнено) ← решение о доверенном источнике `customer_id`.
- EVAL-001 integration ← разрешение владельца на merge (в директиве merge запрещён без отдельного разрешения).
- Phase 03 ← Phase 02 (Parameterized Runtime, Capability Contracts).
- Phase 04 ← Phase 02 + 03; Phase 05 ← 02 + channel trust model; Phase 07 ← швы запуска из 02.

## Ограничения governance

См. `docs/hermes/governance-reconciliation-2026-11-27.md` — перечень невыполнимых/отложенных governance-операций.
MONOLITH — инструмент разработки, не runtime-зависимость HERMES.

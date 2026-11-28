# Структура кодовой базы (E2-Phase 01, codebase organization)

Behavior-preserving реорганизация `src/supportops_agent`. Функциональность, публичные контракты HTTP-сервиса,
security-инварианты и Evaluation recordings не менялись; изменены только расположение модулей и пути импорта.
Пакеты по-прежнему называются `supportops_agent` / `supportops_service` (переименование в HERMES — отдельная задача).

## Карта

```
src/
  supportops_agent/            Агентное ядро SupportOps (офлайн, без БД и HTTP)
    models.py                  Доменные модели (SupportTicket, OrderRecord, SupportCase) и модели трассы (AgentRun/AgentStep)
    classification.py          Классификация обращений: протокол TicketClassifier, fail-closed, OpenAI-реализация
    business/                  Предметная область SupportOps
      mock_data.py             In-memory «хранилище» заказов/клиентов/кейсов, BUSINESS_DATE
      support_tools.py         Бизнес-функции инструментов (lookup_order, get_order_tracking, create_support_case …), object-level authorization
      policy.py                Детерминированный RefundPolicyEngine (без LLM/сети/БД)
      refund_facts.py          Доверенный источник VerifiedRefundFacts
      refund_evaluation.py     Оркестратор «LLM предлагает → факты → политика решает», check_refund_eligibility
      refund_response.py       Policy-first сборка финального ответа
    tools/                     Контур вызова инструментов
      definitions.py           TOOL_REGISTRY, ToolDefinition, схемы аргументов и LLM-схемы
      authorization.py         Authorization Gate (deny-by-default), ExecutionContext
      executor.py              execute_tool_call: gate → валидация → handler → безопасные ошибки, trusted binding
    runtime/                   Исполнение агента
      agent.py                 SupportAgent: цикл LLM↔tools, sanitization трассы (SR-4), fail-safe
      agent_state.py           Состояние одного прогона
      run_manager.py           Жизненный цикл AgentRun
  supportops_service/          HTTP-сервис (FastAPI), security, PostgreSQL — не изменялся
evaluation/                    Evaluation Harness (fake / record / replay), импортирует только supportops_agent
experiments/agent_commons/     Изолированный эксперимент, не импортирует supportops_agent
```

## Зависимости (только вниз, циклов нет)

```
models  ←  classification  ←  business  ←  tools  ←  runtime
```

| Пакет | Зависит от |
|---|---|
| `classification` | `models` |
| `business` | `models`, `classification` (`refund_evaluation` использует `TicketClassification`/`TicketIntent`) |
| `tools` | `models`, `business` |
| `runtime` | `models`, `classification`, `business`, `tools` |

`supportops_service` и `supportops_agent` по-прежнему не импортируют друг друга. Границы проверяет
`tests/test_package_layering.py` (направление зависимостей, запрет относительных импортов, запрет импорта service-слоя).

## Почему так

- **`tools/` включает `authorization.py`.** Gate принимает `ToolDefinition`, а executor вызывает Gate; отдельный
  пакет `security/` создал бы цикл `tools ↔ security`. Gate — это авторизация именно вызовов инструментов.
- **`business/` объединяет данные, политику и бизнес-функции.** `tools/definitions.py` зависит от `refund_evaluation`
  и `support_tools`, а `refund_facts` — от `support_tools`; раздельные пакеты `refund`/`data` дали бы циклы пакетов.
  Весь SupportOps-специфичный код (то, что при модульной архитектуре HERMES станет доменным модулем) лежит в одном месте.
- **`models.py` и `classification.py` оставлены модулями верхнего уровня:** это листовые слои без внутренней структуры;
  отдельные пакеты из одного файла были бы искусственными.
- **`agent.py` (≈900 строк) не разделялся.** Внутри есть выделяемый блок sanitization трассы (SR-4), но его вынос — это
  изменение кода, а не перемещение, и затрагивает security-тесты; кандидат на отдельную задачу.

## Старые → новые пути

| Было | Стало |
|---|---|
| `supportops_agent.agent` / `agent_state` / `run_manager` | `supportops_agent.runtime.*` |
| `supportops_agent.tool_definitions` / `authorization` / `tool_executor` | `supportops_agent.tools.definitions` / `authorization` / `executor` |
| `supportops_agent.tools` | `supportops_agent.business.support_tools` |
| `supportops_agent.mock_data` / `policy` / `refund_facts` / `refund_evaluation` / `refund_response` | `supportops_agent.business.*` |
| `supportops_agent.models` / `classification` | без изменений |

Документы Epoch I и ранние документы Epoch II (`docs/design`, `docs/security`, `docs/hermes/0*`, `.monolith/context`) описывают
состояние на момент написания и сохраняют прежние пути как исторические.

## Тестовые гарды, затронутые реорганизацией

- `tests/evaluation/test_record_replay.py::test_r15_src_is_unchanged_since_eval_baseline` (сравнение `src/supportops_agent` с коммитом
  `5afdf2d`) заменён. Назначение — гарантировать, что оценка не меняет оцениваемую систему. Теперь:
  `test_r15_harness_run_does_not_modify_production_sources` (хеши всех `src/**/*.py` до/после прогонов replay и record совпадают),
  `test_r15_evaluation_package_depends_only_on_agent_not_on_service`; `test_r15_replay_does_not_modify_production_objects` не менялся.
  Регрессии поведения по-прежнему ловят replay-recordings и fake/replay-согласованность.
- `tests/test_policy_consolidation.py::test_only_policy_engine_holds_refund_day_limits`: `glob("*.py")` → `rglob("*.py")`
  (иначе гард молча проверял бы пустой набор) плюс проверка, что файлы найдены.

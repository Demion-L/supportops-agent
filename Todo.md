# TODO

> Легенда: `[x]` готово, `[~]` частично, `[ ]` не начато.

## Agent Core

- [x] Рефакторинг `SupportAgent.run()`
- [x] Сделать базовый agent loop: `LLM → tool → LLM → ... → final`
- [x] Поддержать несколько последовательных tool calls
- [x] Ограничить количество agent steps (`MAX_AGENT_STEPS`)
- [x] Вынести lifecycle run из `run()` в `AgentRunManager`
- [x] Ввести явную модель `AgentRun`
- [x] Ввести явную модель `AgentStep`
- [x] Вынести agent state из `run()`
- [x] Разделить orchestration, tool execution и response handling

## Tools

- [x] Добавить второй tool (`lookup_customer`)
- [x] Сделать registry для инструментов
- [x] Добавить Pydantic validation для tool arguments
- [x] Добавить обработку ошибок tools
- [x] Унифицировать контракт tool execution: `output / error`
- [x] Добавить преобразование tool result → OpenAI `function_call_output`
- [x] Добавить новые business tools (`get_order_tracking`, `check_refund_eligibility`, `create_support_case`)
- [x] Определить стабильный контракт для всех tools (`ToolDefinition` + `ToolResult`: output / error / error_type / reason_code)
- [x] Добавить tool-level metadata / descriptions (`description`, `has_side_effects`, `requires_approval`)
- [x] Добавить Tool Authorization Gate (`ExecutionContext` + `ToolAuthorizationPolicy`, deny-by-default) — AUTH-001/002

## Testing

- [x] Создать `tests/`
- [x] Написать unit-тесты для tools
- [x] Написать тесты для `ToolExecutor`
- [x] Добавить fake/mock OpenAI client
- [x] Протестировать agent loop без реального API
- [x] Протестировать успешный tool execution
- [x] Протестировать tool execution error
- [x] Протестировать передачу `function_call_output` обратно модели
- [x] Запускать всё через `pytest`
- [x] Добавить тесты на несколько последовательных tool calls
- [x] Добавить тест на достижение `MAX_AGENT_STEPS`
- [x] Добавить тесты на invalid tool arguments
- [x] Добавить integration tests для основных SupportOps сценариев

## Agent Observability

- [x] Добавить execution trace
- [x] Ввести модель `AgentStep`
- [x] Логировать tool calls, arguments и results
- [x] Логировать agent steps и переходы между состояниями
- [x] Отделить debug/trace data от final answer
- [x] Добавить явные failure steps в execution trace
- [x] Добавить timing / latency для tool calls
- [x] Подготовить данные для token / cost tracking
- [x] Сделать trace пригодным для debugging и evaluation

## SupportOps Logic

- [x] Добавить classification layer (+ eval: `evals/classification_dataset.json`, `scripts/eval_classification.py`)
- [x] Добавить policy / decision layer (детерминированный refund policy engine: `policy.py`, `refund_facts.py`, `refund_evaluation.py`; результат пишется в trace, попадает в контекст модели и определяет детерминированную refund-часть ответа — решение принимает Policy Engine, а не LLM; код закоммичен в `b5f866c`)
- [x] Подключить результат policy decision к ответу агента (POLICY-003: developer-контекст + детерминированная refund-часть ответа; `check_refund_eligibility` работает через `RefundPolicyEngine`)
- [ ] Подключить policy decision к escalate/resolve-флоу и ограничению действий (нужен approval workflow)
- [ ] Ownership-проверка для `lookup_order` / `lookup_customer` / `get_order_tracking` (сейчас доверенный customer_id применяется только к refund-оценке)
- [~] Определить сценарии `resolve / escalate` (policy engine выдаёт ALLOW/DENY/EXCEPTION_ELIGIBLE/HUMAN_REVIEW; сценарии агента не определены)
- [~] Добавить human escalation (есть tool `create_support_case`; нет escalation-флоу и правил остановки)
- [ ] Добавить action / execution layer
- [~] Добавить audit trail (есть execution trace + POLICY_DECISION/CLASSIFICATION шаги; нет персистентного/неизменяемого журнала)
- [ ] Определить правила, когда агент обязан остановиться и передать тикет человеку

## State & Persistence

- [ ] Добавить persistent agent state
- [ ] Хранить execution state между шагами
- [ ] Хранить execution trace
- [ ] Определить lifecycle тикета
- [ ] Добавить возможность восстановить незавершённый run
- [ ] Определить persistence layer

## Agent Architecture

- [ ] Изучить LangGraph
- [ ] Сравнить собственный agent loop с LangGraph
- [ ] Определить, что должно оставаться собственным кодом
- [ ] Определить границы между Agent Core / Tools / State / Policy
- [ ] Изучить MCP
- [ ] Определить, где MCP действительно полезен, а где будет просто ещё одним слоем магии

## API & Production

- [ ] Подготовить FastAPI API
- [ ] Добавить endpoint для создания Support Ticket
- [ ] Добавить endpoint для запуска agent run
- [ ] Добавить endpoint для получения execution trace
- [ ] Добавить structured logging
- [ ] Добавить configuration management
- [ ] Добавить error handling на API уровне
- [ ] Подготовить production architecture
- [ ] Определить deployment model
- [ ] Добавить observability / metrics
- [ ] Подготовить production security model

## Evaluation

- [ ] Security eval: authority-claim запросы (IDEA-001) — Gate должен отклонять независимо от классификации
- [~] Создать набор тестовых SupportOps scenarios (есть только classification dataset)
- [ ] Определить expected agent behavior
- [ ] Проверять правильность tool selection
- [ ] Проверять правильность final decision
- [ ] Проверять `resolve / escalate` decisions
- [~] Добавить evaluation dataset (только для classification)
- [ ] Добавить automated agent evaluation

## Learning / Architecture Milestones

- [x] Понять базовый agent loop на уровне runtime
- [x] Понять tool calling и execution boundary
- [x] Понять базовый execution trace
- [x] Понять lifecycle `AgentRun`
- [ ] Понять stateful agents
- [ ] Понять policy-driven agent behavior
- [ ] Понять human-in-the-loop
- [ ] Понять evaluation agent systems
- [ ] Понять LangGraph как orchestration framework
- [ ] Понять MCP как tool/context protocol
- [ ] Собрать минимальную production-ready архитектуру
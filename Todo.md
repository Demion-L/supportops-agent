# TODO

## Agent Core

- [x] Рефакторинг `SupportAgent.run()`
- [x] Сделать базовый agent loop: `LLM → tool → LLM → ... → final`
- [x] Поддержать несколько последовательных tool calls
- [x] Ограничить количество agent steps (`MAX_AGENT_STEPS`)
- [x] Вынести lifecycle run из `run()` в `AgentRunManager`
- [x] Ввести явную модель `AgentRun`
- [x] Ввести явную модель `AgentStep`
- [ ] Вынести agent state из `run()`
- [ ] Разделить orchestration, tool execution и response handling

## Tools

- [x] Добавить второй tool (`lookup_customer`)
- [x] Сделать registry для инструментов
- [x] Добавить Pydantic validation для tool arguments
- [x] Добавить обработку ошибок tools
- [x] Унифицировать контракт tool execution: `output / error`
- [x] Добавить преобразование tool result → OpenAI `function_call_output`
- [ ] Добавить новые business tools
- [ ] Определить стабильный контракт для всех tools
- [ ] Добавить tool-level metadata / descriptions

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
- [ ] Добавить явные failure steps в execution trace
- [ ] Добавить timing / latency для tool calls
- [ ] Подготовить данные для token / cost tracking
- [ ] Сделать trace пригодным для debugging и evaluation

## SupportOps Logic

- [ ] Добавить classification layer
- [ ] Добавить policy / decision layer
- [ ] Определить сценарии `resolve / escalate`
- [ ] Добавить human escalation
- [ ] Добавить action / execution layer
- [ ] Добавить audit trail
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

- [ ] Создать набор тестовых SupportOps scenarios
- [ ] Определить expected agent behavior
- [ ] Проверять правильность tool selection
- [ ] Проверять правильность final decision
- [ ] Проверять `resolve / escalate` decisions
- [ ] Добавить evaluation dataset
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
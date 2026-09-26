# TODO

## Agent Core

* [ ] Рефакторинг `SupportAgent.run()`
* [ ] Сделать универсальный agent loop: `LLM → tool → LLM → ... → final`
* [ ] Поддержать несколько последовательных tool calls

## Tools

* [ ] Добавить второй tool (`lookup_customer`)
* [ ] Сделать registry для инструментов
* [ ] Добавить обработку ошибок tools

## Testing

* [ ] Создать `tests/`
* [ ] Написать unit-тесты для tools
* [ ] Написать тесты для `ToolExecutor`
* [ ] Добавить fake/mock OpenAI client
* [ ] Запускать всё через `pytest`

## Agent Observability

* [ ] Добавить execution trace
* [ ] Логировать tool calls, arguments и results
* [ ] Отделить debug output от final answer

## SupportOps Logic

* [ ] Добавить policy/decision layer
* [ ] Добавить human escalation
* [ ] Определить сценарии `resolve / escalate`
* [ ] Добавить audit trail

## Production

* [ ] Добавить persistence/state
* [ ] Подготовить FastAPI API
* [ ] Изучить LangGraph
* [ ] Изучить MCP
* [ ] Подготовить production architecture

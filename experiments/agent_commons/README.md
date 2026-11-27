# AC-EXP-001 — Agent Commons: Verified Experience Exchange

Изолированный исследовательский эксперимент. **Не** часть Roadmap SupportOps, не меняет scope
`SUPPORTOPS-SEC-003`, не зависит от рантайма MONOLITH и не импортирует production-код SupportOps.
Ветка: `experiment/agent-commons-exp-001` (отдельная git worktree).

## Гипотеза

Если один агент нашёл решение, другие могут повторно использовать этот опыт без повторного поиска —
но рекомендация считается достоверной только после: получения → проверки → применения в разрешённом
контексте → привязки к фактическому исходу → независимой оценки → учёта при следующих рекомендациях.

## Запуск (без сети, только stdlib + pytest)

```bash
cd experiments/agent_commons
python3 -m pytest tests -q        # 72 теста
python3 run_experiment.py         # все таблицы results.md, пишет results_data.json
```

## Состав

| Путь | Назначение |
|---|---|
| `src/agent_commons/dto.py`, `vocab.py` | DTO Ask/Share/Validate/Feedback; закрытые словари; строгая валидация |
| `src/agent_commons/protocol.py` | `CommonsPort` (для агентов) и `EvaluationSink` (только для Evaluator) |
| `src/agent_commons/lifecycle.py`, `ledger.py`, `eventlog.py` | жизненный цикл, хэш-цепочечный append-only лог, состояние как свёртка |
| `src/agent_commons/scoring.py` | контекстно-зависимая оценка (не лайки, не confidence) |
| `src/agent_commons/service.py` | реализация Commons |
| `src/agent_commons/simulator.py`, `evaluator.py` | детерминированный мир/oracle и независимый Evaluator |
| `src/agent_commons/agents.py`, `policy.py` | Agent A, Agent B, локальная политика A |
| `src/agent_commons/experiment.py`, `run_experiment.py` | режимы, метрики, агрегирование |
| `architecture.md`, `threat-model.md`, `results.md` | документы исследования |

Все данные синтетические. Реальные LLM/сеть не используются.

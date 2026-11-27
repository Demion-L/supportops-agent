# Модель угроз AC-EXP-001

Границы доверия: A, B, Commons-сервис, Evaluator, мир — отдельные участники. Агенты считаются потенциально
ошибающимися или злонамеренными; Commons и Evaluator — доверенными (см. «Не покрыто»).

| # | Угроза | Механизм защиты | Инвариант | Воспроизведено |
|---|---|---|---|---|
| T1 | Вредная рекомендация с максимальным confidence | локальная политика независима от Commons; Validate не даёт полномочий | S1, S5 | `test_harmful_recommendation_with_max_confidence_denied_by_local_policy`; без защит — `naive_trust` исполняет вред (15.4 % задач) |
| T2 | Завышенный confidence у неверной рекомендации | confidence ≠ доказательство; ранжирование по независимым исходам | S8 | `commons_no_evaluator` < baseline; `test_confidence_is_not_evidence` |
| T3 | Самоотчёт «успех» / накрутка feedback | в оценку входят только вердикты Evaluator по состоянию мира | S7 | `test_lying_self_report…`, `test_agent_feedback_cannot_change_score` |
| T4 | Поддельное Application без реального применения | Evaluator сверяет с аудитом мира → `UNVERIFIED`, не учитывается | S7 | `test_forged_application…` |
| T5 | Ложный контекст в отчёте (перенос репутации) | сверка контекста с миром | S9 | `test_context_misreport…` |
| T6 | Ложный «отказ политики» для саботажа хорошей рекомендации | Evaluator подтверждает отказ по организационным запретам | S7 | `test_false_denial_claim…` |
| T7 | Подмена `agent_id` в Feedback, replay `application_id` | проверка владельца; уникальность id | — | `test_duplicate_and_spoofed_feedback` |
| T8 | Код/инструкции внутри рекомендации | только enum-метки и закрытые словари; нет eval/exec (AST-тест) | S4 | `test_dto.py`, `test_no_code_execution…` |
| T9 | Утечка внутреннего состояния/ПДн/ключей через Ask/Validate | whitelisted ключи и значения; неизвестные поля — ошибка | S2, S3 | `test_ask_rejects_internal_state_fields`, `test_only_whitelisted_data…` |
| T10 | Сервер Commons отдаёт неприменимые рекомендации | A проверяет применимость сам | S5 | `test_inapplicable_recommendation_rejected_locally…`, arm `commons_faulty_filter` |
| T11 | Недоступность Commons | fallback на локальный playbook | S6 | arm `commons_outage`, `test_commons_outage…` |
| T12 | Перенос репутации «глобальной истиной» | контекстная оценка | S9 | `test_reputation_is_contextual…`, arm `commons_global_score` |
| T13 | Подмена/порча лога | хэш-цепочка; replay-проверка инвариантов | — | `test_torn_tail…`, `test_semantically_invalid_log…` |
| T14 | Устаревшая/отозванная рекомендация | актуальная версия, `revoke` | — | `test_version_rules`, `test_revoked…` |

## Не покрыто (осознанно)

* Злонамеренный Commons или Evaluator; компрометация oracle.
* Sybil/коллюзия нескольких авторов в Share: у Commons нет репутации авторов, `confidence` — самозаявление.
  Частично смягчено тем, что вес даёт только независимый исход, но «отравление» корпуса и затраты на
  исследование (одна неудача на пару рекомендация×контекст) остаются.
* Скрытые каналы: свободного текста нет, но закрытые словари тоже кодируют биты (см. Limitations в results.md).
* Реальные ПДн: все данные синтетические; защита от ПДн = закрытые словари, а не детектор.
* Python не обеспечивает жёсткую изоляцию способностей (`Sink`, `AgentPort` — соглашение + `__slots__`, не sandbox).
* Межорганизационные угрозы (подпись, провенанс, юрисдикция) — вне эксперимента.

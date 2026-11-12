---
type: context
id: planned-supportops-sec-003-security-architecture
authority: authoritative
---

# SUPPORTOPS-SEC-003 — Security Architecture & Trust Boundaries

**Status: APPROVED (решения K1–K5, D1–D4 утверждены владельцем; §14).** Продукт Mission SEC003-M01 (MONOLITH `MIS-0001`). ADR не создавался. Код и тесты этой Mission не менялись. Восемь
Acceptance Criteria в [SUPPORTOPS-SEC-003](SUPPORTOPS-SEC-003.md) не изменены.

Базовое состояние анализа: коммит `7859a95`, `pytest` — 295 passed (подтверждено запуском).
Все утверждения о текущем поведении привязаны к файлу/функции; где подтверждения нет —
`NOT VERIFIED`. Ссылки на строки — на момент `7859a95`.

## 1. Purpose

Установить фактические границы доверия и точки принуждения (enforcement) до изменения
поведения агента; подготовить варианты решений K1–K5 и минимальный план M02–M04.

## 2. Current Security Architecture

Все пути относительно `src/supportops_agent/`.

| Компонент | Файл : символ | Роль в безопасности |
|---|---|---|
| Идентичность | `models.py::SupportTicket.customer_id` | Единственный носитель identity. Это обычное pydantic-поле (`min_length=1`); кода, который бы *проверял* его происхождение, нет. Доверие — только по контракту (docstring `agent.py::SupportAgent.__init__`). |
| Оркестратор | `agent.py::SupportAgent.run/_run_agent_loop/_start` | Классификация → оценка возврата → вызов LLM → цикл tool calls. |
| Передача identity | `agent.py::execute_function_call` (стр. 105–111) | В executor передаётся `trusted_customer_id=self.state.ticket.customer_id`; не из аргументов модели. |
| Gate | `authorization.py::AllowListPolicy.authorize`, `tool_executor.py::_authorize` | Решает по `ToolDefinition` + `ExecutionContext` (только имя инструмента). Аргументов, identity и тикета **не видит** — объектный уровень в его контракте отсутствует. Fail-closed подтверждён (MISSING_CONTEXT, POLICY_MISSING, POLICY_ERROR, POLICY_INVALID_RESULT). |
| Executor | `tool_executor.py::execute_tool_call` (стр. 156+) | После ALLOW вызывает handler; `trusted_customer_id` подставляется **только** если `definition.requires_trusted_identity` (стр. 182). Аргументы модели: `extra="forbid"` (`tool_definitions.py::_Arguments`) — поле `trusted_customer_id` модель передать не может. |
| Реестр | `tool_definitions.py::_DEFINITIONS` | `requires_trusted_identity=True` только у `check_refund_eligibility` (стр. 117). |
| Data access | `tools.py::lookup_order`, `lookup_customer`, `get_order_tracking`; `mock_data.py::ORDERS/CUSTOMERS` | Чтение по ID **без** какой-либо привязки к владельцу. |
| Refund-цепочка | `refund_evaluation.py::RefundEvaluationOrchestrator.evaluate_order` (стр. 118+) | Единственное место проверки ownership: `owner != customer_id` (стр. 133) → `OWNERSHIP_NOT_VERIFIED`; `public_view()` (стр. 48) делает «не найден» и «чужой» неразличимыми. |
| Классификация | `agent.py::_classify` (стр. 227–274), `classification.py::OpenAITicketClassifier.classify` | `ClassificationError` → trace + `None` → прогон продолжается (degrade-open по инструментам). Любое иное исключение → trace + re-raise → run FAILED. |
| Trace | `run_manager.py::AgentRunManager.record`, `agent.py::execute_function_calls` (стр. 113–152) | Пишет **полные** аргументы и output каждого tool call. |
| Ответ | `refund_response.py::compose_final_answer` | Fail-closed только для refund-тематики (отбрасывает текст LLM о возврате). |

## 3. Trust Boundaries

Фактическая схема (скорректирована относительно предварительной: между Gate и данными нет
«Data Access Layer» с проверками — handler читает словарь напрямую):

```
External User ── ticket.message / ticket.order_id ──┐   UNTRUSTED
                                                    v
App code (build ticket, ExecutionContext) ── customer_id ──> SupportTicket     TRUSTED *
                                                    v
Agent Orchestrator (agent.py) ──> Classifier(LLM; input = untrusted message)
        │                          └─ output: untrusted-derived (intents, entities.order_id)
        ├──> RefundEvaluationOrchestrator ──> ownership check (ЕДИНСТВЕННЫЙ) ──> Policy Engine
        └──> LLM main loop ── function_call(name, args) ── UNTRUSTED
                                   v
                          Gate (имя инструмента) ── ALLOW ──> execute_tool_call
                                   v                            (trusted_customer_id только по флагу)
                          handler ──> ORDERS / CUSTOMERS / TRACKING   <-- нет проверки владельца
                                   v
                          output ──> LLM (function_call_output) + Trace
```
`*` Доверие к `customer_id` — допущение контракта; код его не верифицирует (см. K5).

| Граница | Trusted inputs | Untrusted inputs | Источник identity | Ответственность за authz | Возможные обходы | Fail-closed сейчас | Evidence |
|---|---|---|---|---|---|---|---|
| B1 User → Ticket | `ticket_id`, `customer_id` (если выставлены приложением) | `message`, `order_id` | Приложение (`run_agent.py::main` выставляет вручную) | Приложение | Если вызывающий код берёт `customer_id` из запроса — identity подделана; в репозитории это не предотвращено | n/a | `run_agent.py:21–31`; `agent.py:47–50` |
| B2 Ticket → Classifier | — | `message` целиком | — | Нет (классификация не авторизует) | Prompt injection в `message` меняет intents/entities | Нет: ошибка → продолжение | `classification.py::CLASSIFIER_INSTRUCTIONS`; `agent.py::_classify` |
| B3 LLM → Gate | `ExecutionContext`, `ToolDefinition` | `name`, `arguments` | `ExecutionContext` (код приложения) | Gate: «может ли агент вызывать инструмент» | Аргументы с чужим ID Gate не видит | Да (deny-by-default) | `authorization.py::AllowListPolicy`; `tests/test_authorization.py` A–J |
| B4 Executor → Handler | `trusted_customer_id` | `arguments` (валидированы, `extra=forbid`) | `ticket.customer_id` | Executor пробрасывает; **проверку обязан делать handler** | Инструмент без `requires_trusted_identity` вообще не получает identity | Только у `check_refund_eligibility` | `tool_executor.py:182`; `tool_definitions.py:117` |
| B5 Handler → Data | — | ID объекта (от LLM) | — | **Отсутствует** для 3 lookup-инструментов | Прямое чтение любого ID | **Нет** | `tools.py::lookup_order/lookup_customer/get_order_tracking` |
| B6 Output → LLM/Trace | — | — | — | Нет редактирования | Чужие данные попадают в контекст LLM, в итоговый ответ и в trace | Нет | `agent.py::build_tool_output`, `execute_function_calls` |

## 4. Authorization Enforcement Points

| # | Точка | Что принуждает | Покрывает |
|---|---|---|---|
| E1 | Gate (`_authorize`) | Допустимость инструмента | Все 5 инструментов (на уровне имени) |
| E2 | `execute_tool_call` + `requires_trusted_identity` | Доставка identity в handler | Только `check_refund_eligibility` |
| E3 | `evaluate_order` | Ownership заказа | Только refund-оценка (оркестратор) и `check_refund_eligibility` |
| E4 | `refund_response.compose_final_answer` | Фильтр текста о возврате | Только refund-тематика |

Для `lookup_order`, `lookup_customer`, `get_order_tracking` точки принуждения ownership **нет**.

## 5. Object Ownership Model

Факты (код): владелец заказа — `OrderRecord.customer_id` (`models.py`); владелец профиля —
сам `customer_id` записи `CUSTOMERS`; у трекинга владельца нет, он определяется через заказ
(`mock_data.TRACKING[order_id]`). Роли staff/admin в коде отсутствуют (NOT VERIFIED за
пределами `src/`: поиск по `src/` ролей не нашёл).

Предлагаемая модель (PROPOSAL): объект «принадлежит» вызывающему, если
`owner(object) == trusted_customer_id` строгим равенством строк. Для `lookup_customer`:
`arguments.customer_id == trusted_customer_id`. Для трекинга: через владельца заказа.

## 6. Classification Failure Model

| Событие | Текущее поведение (код) | Тесты |
|---|---|---|
| `ClassificationError` (`api_error`, `invalid_output`) | Шаг CLASSIFICATION с `error`, `output={"fallback": True}`; `classification=None`; **LLM-цикл и все 5 инструментов остаются доступны**; refund-оценка → `NOT_APPLICABLE`; `summary()["classification_failed"]=True` | `test_classification.py::test_agent_classification_failure_is_traced_and_run_continues`, `test_expected_classification_errors_keep_controlled_fallback`; `test_authorization.py::test_e_*`; `test_refund_evaluation.py::test_classification_fallback_does_not_evaluate_and_run_continues` |
| Иное исключение классификатора | Трассируется (`unexpected_error`), re-raise → run FAILED | `test_classification.py::test_unexpected_classifier_error_fails_run_with_diagnostics` |
| `TicketClassification.fallback()` как *результат* | `unknown` + `requires_clarification`; цикл продолжается | `test_classification.py::test_fallback_is_unknown_and_needs_clarification` |

Следствие: сбой классификации сегодня **не сужает** полномочия. Авторизационные решения от
классификации не зависят (подтверждено: Gate её не читает), поэтому «degrade-open» — это
сохранение полного набора инструментов, а не повышение полномочий. Но из-за дыры B5 это
означает, что при сбое классификатора модель по-прежнему может читать чужие данные.

## 7. Security Findings (подтверждены на коде)

Проверка выполнена вызовом `execute_tool_call` с `support_agent_context()` и
`trusted_customer_id="customer_001"` (read-only, состояние не менялось):

| ID | Находка | Статус | Evidence |
|---|---|---|---|
| F-1 | `lookup_order("order_002")` возвращает полный заказ чужого клиента (`customer_002`) | **CONFIRMED** | запуск: output с `customer_id: customer_002`, `amount_cents`, `status` |
| F-2 | `lookup_customer("customer_002")` возвращает `name` и `email` другого клиента | **CONFIRMED** | запуск: `{'name': 'Alice', 'email': 'alice@example.com'}` |
| F-3 | `get_order_tracking("order_002")` возвращает перевозчика, трек-номер, статус чужой посылки | **CONFIRMED** | запуск: `carrier: UPS`, `tracking_number: UPS-0002` |
| F-4 | Существование объекта различимо: чужой заказ → данные; несуществующий → `None` / `order_not_found` | **CONFIRMED** (нарушает K2 для 3 инструментов) | запуск: `order_999 -> None` |
| F-5 | Trace хранит полные аргументы и output tool calls, включая чужие данные (если F-1..F-3 эксплуатированы) | CONFIRMED по коду; практическая значимость зависит от Phase 5 | `agent.py::execute_function_calls` |
| F-6 | Текст исключения handler'а передаётся модели: `error=f"Tool '{name}' failed: {exc}"` | CONFIRMED по коду, **низкий** риск на mock-данных; потенциально утечка при реальном backend | `tool_executor.py:196` |
| F-7 | `create_support_case` принимает `ticket_id` из аргументов модели; привязки к текущему тикету нет — модель может открыть кейс для произвольного `ticket_id` | CONFIRMED по коду (запись, не чтение). **Вне формального scope SEC-003** (3 lookup-инструмента); требует решения владельца | `tool_definitions.py::CreateSupportCaseArguments`, `tools.py::create_support_case` |
| F-8 | `ticket.customer_id` не верифицируется кодом | Потенциальный риск, закрывается интеграцией в API-003; в SEC-003 — только контракт (K5) | `models.py::SupportTicket` |
| F-9 | Demo `run_agent.py` подсказывает запросы про customer-данные; не уязвимость, но пример идентичности «вручную» | INFO | `run_agent.py` |

Защищено и подтверждено кодом: `check_refund_eligibility` (ownership + неразличимость,
`test_business_tools.py::test_refund_unknown_order_is_not_distinguishable_from_foreign_order`,
`::test_refund_tool_without_trusted_identity_reveals_nothing`); модель не может задать
`trusted_customer_id` (`test_policy_consolidation.py`, extra-поля); authority claims не
меняют `ExecutionContext` (`test_authorization.py` секция F).

Ограничение: наличие тестов не доказывает безопасность. Существующие тесты не содержат
негативных проверок ownership для `lookup_order`/`lookup_customer`/`get_order_tracking`
(**NOT VERIFIED**: отдельного поиска по смыслу не делалось, только по именам/ключевым
словам `foreign`, `trusted_customer_id`).

## 8. K1–K5 Decision Proposals

Все пять утверждены владельцем (см. §14); исходные формулировки ниже — аналитическая база.

### K1. Classification Fail-Closed
- **Рекомендация:** при `ClassificationError` не вызывать LLM-цикл; вернуть детерминированный
  безопасный ответ (клиент уведомляется, что запрос не обработан автоматически); записать
  шаг CLASSIFICATION (как сейчас) + шаг FINAL с признаком `degraded`. Неожиданные исключения
  оставить как есть (FAILED + re-raise) — это уже отдельная ветка.
- **Совместимость:** `_classify` уже различает два класса ошибок; точка изменения —
  `_start` (после `_classify`). Эскалации (ACT-001/002) не требуется: безопасный результат
  не обязан быть эскалацией. `AgentRunStatus` содержит только RUNNING/COMPLETED/FAILED —
  нового статуса вводить не нужно; вариант «COMPLETED + degraded FINAL» не меняет модель.
- **Затронутые тесты (потребуют осознанного обновления в M03):**
  `test_agent_classification_failure_is_traced_and_run_continues`,
  `test_classification_fallback_does_not_evaluate_and_run_continues`,
  `test_authorization.py::test_e_*`, `test_f1_c_*` — они фиксируют «run continues».
- **Риски:** (а) пропадает полезность агента при кратковременных сбоях API (осознанный
  компромисс); (б) вариант «FAILED» проще, но смешивает отказ безопасности с дефектом.
- **Нужно решение владельца:** исход (безопасный ответ / FAILED / ограничить инструменты) и
  текст ответа клиенту.

### K2. Object Enumeration Protection
- **Рекомендация:** для клиента «объект не существует» и «объект чужой» неразличимы по
  всем каналам: форма output, поля, текст ошибки, тип ошибки. Различие допустимо только во
  внутреннем аудите без идентификаторов чужих объектов.
- **Каналы утечки:** tool output (F-4), текст ошибки (F-6), trace (F-5), сообщение
  исключения. Тайминг-канал для mock-данных не рассматривался (**NOT VERIFIED**, для
  словаря не актуален; актуален при реальном backend).
- **Совместимость:** шаблон уже есть в `RefundEvaluationResult.public_view`. Единая форма
  «не найден» для каждого инструмента: `lookup_order`/`lookup_customer` → `None`,
  `get_order_tracking` → `{"outcome": "order_not_found", "order_id": <как запрошено>}`.
- **Риск:** внутренний аудит различий требует канала вне output/error (иначе различие
  попадёт к LLM). Минимум — не различать вообще; различие в логе — **решение владельца**.
- **Нужно решение владельца:** нужен ли внутренний аудит причины отказа в SEC-003 или он
  откладывается до ACT-004.

### K3. Ownership Enforcement Boundary
- **Рекомендация:** принуждение на доверенной стороне, на уровне handler/data-helper, а не в
  Gate и не в LLM. Использовать **уже существующий** механизм: `requires_trusted_identity=True`
  для трёх инструментов + единый helper (например, `tools.py`: «получить заказ, если он
  принадлежит `trusted_customer_id`»), который затем использует и
  `VerifiedRefundFactsProvider`/`evaluate_order`, чтобы не дублировать сравнение владельца.
- **Совместимость:** `execute_tool_call` менять не нужно (флаг уже обрабатывается);
  контракт Gate не меняется (требование критерия 7). Меняется сигнатура трёх handler'ов
  (+ keyword-only `trusted_customer_id`) и `lookup_order_for_tool`.
- **Альтернатива (не рекомендуется без решения владельца):** централизованная проверка в
  `execute_tool_call` с новым `AuthorizationReason` (напр. identity missing) — единая точка,
  но меняет контракт Gate/причин и поведение `check_refund_eligibility`.
- **Риски:** (а) дублирование логики ownership в двух местах, если helper не общий;
  (б) существующие тесты вызывают три инструмента без `trusted_customer_id` (grep по именам
  инструментов: ~112 строк в 8 файлах; сколько тестов реально упадёт — **NOT VERIFIED**).
  ABAC/RBAC-фреймворк не нужен.
- **Нужно решение владельца:** handler-level (рекомендуется) против executor-level.

### K4. Customer Data Scope
- **Рекомендация:** контекст клиента даёт доступ только к данным самого клиента; staff/admin
  RBAC откладывается (не реализуется, не имитируется). Роль не вводится.
- **Факты:** смешения customer/staff привилегий в коде нет, потому что ролей нет; сегодня
  *любой* вызывающий читает всё (F-1..F-3). Ограничение scope возможно без production
  authentication, если identity — контракт приложения (K5).
- **Риск:** `create_support_case` (F-7) — отдельная поверхность записи; K4 её не закрывает.
- **Нужно решение владельца:** включать ли F-7 в SEC-003 (привязка `ticket_id` к текущему
  тикету) или завести отдельный пункт/Story.

### K5. Trusted Identity Contract
- **Рекомендация:** контракт: «`SupportTicket.customer_id` обязано быть установлено
  приложением из аутентифицированного источника; отсутствие или пустое значение → DENY».
  Реальная аутентификация — SUPPORTOPS-API-003, не SEC-003. В SEC-003: (1) зафиксировать
  контракт в документации и docstring; (2) трёх инструментам при отсутствии identity —
  единообразный отказ (как у `check_refund_eligibility`: `identity_verification_required`);
  (3) негативные тесты на подделку в тексте/аргументах.
- **Текущие источники identity:** только `ticket.customer_id` (`agent.py:110`). Пути
  подмены внутри пакета: не найдены (модель не может задать поле; `extra=forbid`); вне
  пакета — любой вызывающий код, конструирующий `SupportTicket` (F-8).
- **Совместимость с API-003:** контракт совместим — API-003 лишь заменит источник значения.
  Менять `SupportTicket` не требуется.
- **Нужно решение владельца:** подтвердить, что в SEC-003 не вводится тип `TrustedIdentity`
  (минимализм) — только контракт и DENY при отсутствии.

## 9. Security Decision Matrix

| # | Scenario | Current Behavior | Proposed Behavior | Risk | Required Change |
|---|---|---|---|---|---|
| 1 | Клиент запрашивает свой заказ | Возвращается заказ (CONFIRMED запуском) | Без изменений | — | Регрессионный тест |
| 2 | Клиент запрашивает чужой заказ | Возвращается полный чужой заказ (F-1) | Ответ, неотличимый от «не найден» | Высокий (утечка) | `lookup_order` + trusted id (M02) |
| 3 | Несуществующий заказ | `None` (CONFIRMED) | `None` (эталон «не найден») | — | Без изменений формы |
| 4 | Чужой профиль | Возвращаются name/email (F-2) | `None` | Высокий (PII) | `lookup_customer` (M02) |
| 5 | Чужой трекинг | Перевозчик и трек-номер (F-3) | `{"outcome": "order_not_found", ...}` | Высокий | `get_order_tracking` (M02) |
| 6 | Нет trusted identity | 3 инструмента: данные отдаются (NOT VERIFIED тестом, но по коду identity не используется); refund-инструмент: `identity_verification_required` | DENY единообразно для всех 4 identity-инструментов | Высокий | Флаг + проверка в handler/helper (M02) |
| 7 | Поддельная identity в сообщении | Модель не задаёт identity; `extra=forbid`; пока identity не используется lookup-инструментами, подделка неэксплуатируема лишь потому, что проверки нет | Identity только из `ticket.customer_id`; негативные тесты | Средний | Тесты (M02/M04) |
| 8 | LLM пытается обойти authz (аргументы/claims) | Gate не видит аргументов; `ExecutionContext` неизменяем; лишние поля отклоняются (`test_authorization.py` F) | Без изменений; добавить тесты на `trusted_customer_id`/`customer_id` в аргументах lookup-инструментов | Низкий | Тесты |
| 9 | `ClassificationError` | Trace + `None`; цикл и все инструменты доступны | По решению K1 (рекомендация: детерминированный безопасный ответ без LLM-цикла) | Средний | `_start` + обновление тестов (M03) |
| 10 | Неожиданное исключение классификатора | Trace `unexpected_error` + re-raise → FAILED (CONFIRMED тестом) | Без изменений | — | Сохранить тест |
| 11 | Refund eligibility чужого заказа | Не оценивается, неотличимо от «не найден» (CONFIRMED тестами) | Без изменений | — | Регрессионный тест |
| 12 | Выполнение инструмента после authz-denial | Handler не вызывается (`test_authorization.py` A, C, D, H, I) | Без изменений; то же для denial по ownership | Низкий | Тест: handler-side-effect отсутствует при ownership-denial |

## 10. Minimal Implementation Plan

Все три Missions — **планы**, в State Keeper не созданы; реализация только после решений K1–K5.

### SEC003-M02: Object-Level Authorization
- **Scope:** ownership для `lookup_order`, `lookup_customer`, `get_order_tracking`; DENY без identity.
- **Компоненты:** `tool_definitions.py` (флаг `requires_trusted_identity=True` у 3 инструментов), `tools.py` (handlers + общий helper владельца), опционально `refund_facts.py` (переиспользование helper).
- **Контракты:** handler'ы получают keyword-only `trusted_customer_id`; форма «не найден» едина; контракт Gate не меняется.
- **Тесты:** foreign/nonexistent неразличимы (оба output и сериализованный `ToolResult`); missing identity; подделка в аргументах/тексте; handler не меняет состояние; `check_refund_eligibility` ownership regression (критерий 4); адаптация существующих тестов.
- **Evidence:** падение новых тестов без изменения и прохождение с ним; полный набор зелёный (295 + новые).
- **Risks:** массовое обновление существующих тестов; случайное изменение публичного контракта `lookup_order` (`ORDER_PUBLIC_FIELDS`).
- **Dependencies:** решения K2, K3, K4, K5.

### SEC003-M03: Classification Fail-Closed
- **Scope:** исход ошибки классификации по K1.
- **Компоненты:** `agent.py::_start/_classify/_finish` (возможно новый безопасный ответ в `refund_response.py` или отдельном модуле).
- **Контракты:** `run()` по-прежнему возвращает `str`; новый признак `degraded` в шаге FINAL; `AgentRunStatus` без изменений (если принят вариант COMPLETED).
- **Тесты:** `ClassificationError` (оба `kind`) → нет вызовов LLM-цикла и инструментов, шаг трассируется; неожиданное исключение → FAILED; осознанное обновление тестов «run continues».
- **Evidence:** трасса прогона с отказом; отсутствие вызовов `client.responses.create` после классификации.
- **Risks:** снижение доступности; тексты ответа клиенту.
- **Dependencies:** решение K1; открытый вопрос SEC-003 «Response fail-closed» (обобщение `refund_response`) остаётся за владельцем; согласование с ACT-001 — мягкая зависимость.

### SEC003-M04: Security Regression & Review
- **Scope:** сводный регрессионный набор, ревью bypass-путей (включая F-6, F-7), проверка критериев 1–8.
- **Компоненты:** `tests/` (новый модуль security-регрессии), ревью-заметка.
- **Evidence:** отчёт по восьми критериям; список изученных обходов; полный прогон.
- **Risks:** пересечение со SEC-004 (граница: ownership — SEC-003, claims/bypass-сценарии — SEC-004).
- **Dependencies:** M02, M03.

## 11. Risks

1. Ложное чувство безопасности из-за наличия Gate: он авторизует инструмент, не объект.
2. Изменение формы output трёх инструментов влияет на поведение модели и на существующие тесты (объём не измерен).
3. Внутренний аудит причины отказа может создать канал утечки, если реализовать неосторожно.
4. Доверие к `ticket.customer_id` держится только на дисциплине вызывающего кода до API-003.
5. Расширение scope (F-6, F-7) без решения владельца нарушит критерий «не расширять scope».

## 12. Open Questions (для владельца)

1. K1: исход при ошибке классификации и текст ответа клиенту.
2. K1/SEC-003 Q1: обобщать ли `refund_response`-фильтр («Response fail-closed») или только классификация.
3. K2: нужен ли внутренний аудит причины отказа в SEC-003 или он откладывается до ACT-004.
4. K3: handler-level (рекомендуется) против executor-level принуждения.
5. K5: подтверждается ли «контракт без нового типа TrustedIdentity».
6. SEC-003 Q3 (reason_code при ownership mismatch и раскрытие существования): рекомендация K2 — единая форма «не найден», отдельного reason_code клиенту нет.
7. F-6 (текст исключения handler'а → модели) и F-7 (`create_support_case`, `ticket_id` от модели): включать в SEC-003 или отдельно?

## 13. Evidence References

- Код: `agent.py` (стр. 105–111, 113–152, 213–274, 383), `tool_executor.py` (стр. 111–154, 156–199), `authorization.py` (весь), `tool_definitions.py` (стр. 66, 84–125), `tools.py` (стр. 26–83, 93+), `refund_evaluation.py` (стр. 48–68, 118–154, 169–193), `refund_facts.py`, `models.py::SupportTicket/OrderRecord`, `mock_data.py`.
- Тесты: `tests/test_authorization.py` (секции A–J, F1, F2), `tests/test_business_tools.py` (стр. 100–112), `tests/test_policy_consolidation.py` (стр. 176–196, 241), `tests/test_classification.py` (стр. 217–289), `tests/test_refund_evaluation.py` (стр. 126, 285).
- Запуск: `pytest` — 295 passed; проверочный вызов `execute_tool_call` для F-1..F-4 (read-only, без побочных эффектов).
- MONOLITH: Mission `MIS-0001`.

## 14. Owner Decision Record

**Статус всех записей: APPROVED.** Владелец явно утвердил K1–K5 и D1–D4 (Owner Approval Confirmed, 2026-11-11). ADR не создаётся. Записи ниже
уточняют §8 и закрывают Open Questions §12 (соответствие указано в «Закрывает»). Восемь
Acceptance Criteria SEC-003 и findings F-1..F-9 не изменены.

### K1. Classification Fail-Closed
- **Decision proposal:** при ожидаемой `ClassificationError` — не вызывать LLM-цикл и инструменты;
  вернуть детерминированный ответ «Не удалось автоматически обработать ваш запрос. Пожалуйста,
  обратитесь в службу поддержки»; зафиксировать degraded classification в trace; не подтверждать
  refund eligibility; не выдавать за успешное решение. Неожиданные исключения — по-прежнему FAILED.
  `AgentRunStatus` не менять, если хватает существующих механизмов (COMPLETED + признак `degraded`
  в FINAL — проверить в M03, что `summary()` не маскирует исход).
- **Rationale:** сбой классификатора не должен сохранять полный набор инструментов (§6, сценарий 9).
- **Scope:** `agent.py::_start/_classify/_finish`; реализация — SEC003-M03.
- **Risks:** снижение доступности при сбоях API; COMPLETED с `degraded` может читаться
  потребителями как успех — нужен явный признак в trace и summary; обновление тестов «run continues».
- **Закрывает:** Q1 (исход и текст). Status: APPROVED.

### K2. Object Enumeration Protection
- **Decision proposal:** чужой и несуществующий объект неразличимы для клиента и LLM по форме,
  полям и тексту ответа: `lookup_order(foreign) → None` = `lookup_order(nonexistent) → None`;
  аналогично `lookup_customer`; `get_order_tracking` → `{"outcome": "order_not_found", ...}`.
  Внутренние причины отказа не попадают в LLM-visible output. Audit trail — ACT-004.
- **Rationale:** F-4; шаблон уже есть в `RefundEvaluationResult.public_view`.
- **Scope:** три lookup-инструмента (M02).
- **Risks:** любой внутренний лог различия — потенциальный канал; в SEC-003 не вводится.
  Тайминг-канал не оценивался (NOT VERIFIED; для словаря не актуален).
- **Закрывает:** Q3, Q6. Status: APPROVED.

### K3. Ownership Enforcement
- **Decision proposal:** `requires_trusted_identity=True` для `lookup_order`, `lookup_customer`,
  `get_order_tracking`; проверка владельца внутри handlers через общий доверенный helper;
  helper переиспользуется в refund-цепочке (`evaluate_order`/`refund_facts`) только если это не
  меняет её семантику. Контракт Tool Authorization Gate не менять; ABAC/RBAC-фреймворк не создавать.
- **Rationale:** механизм доставки identity уже есть (`tool_executor.py`, флаг); минимальное изменение.
- **Scope:** `tool_definitions.py`, `tools.py`, опционально `refund_facts.py` (M02).
- **Risks:** дублирование ownership-логики, если helper не общий; массовое обновление
  существующих тестов (объём NOT VERIFIED); случайное изменение `ORDER_PUBLIC_FIELDS`.
- **Закрывает:** Q4. Status: APPROVED.

### K4. Customer Data Scope
- **Decision proposal:** customer-контекст даёт доступ исключительно к собственным данным клиента.
  Staff/admin RBAC откладывается, роли не вводятся и не имитируются.
- **Rationale:** ролей в `src/` нет; текущая проблема — отсутствие любого ограничения (F-1..F-3).
- **Scope:** M02.
- **Risks:** будущие staff-сценарии потребуют отдельного дизайна (вне SEC-003).
- **Закрывает:** часть Q7 (scope данных). Status: APPROVED.

### K5. Trusted Identity Contract
- **Decision proposal:** trusted identity предоставляется доверенным вызывающим приложением
  (`SupportTicket.customer_id`); отсутствие/пустое значение → DENY. `ticket.customer_id` не
  считается доверенным лишь из-за pydantic-валидации. Production authentication — SUPPORTOPS-API-003.
  Тип `TrustedIdentity` в SEC-003 не вводится.
- **Rationale:** F-8; контракт совместим с API-003 (меняется только источник значения).
- **Scope:** docstring/документация контракта + DENY в трёх инструментах (M02).
- **Risks:** до API-003 доверие держится на дисциплине вызывающего кода (F-8 остаётся открытым).
- **Закрывает:** Q5. Status: APPROVED.

### D1. Response fail-closed (ширина)
- **Decision proposal:** не расширять SEC-003 до общего рефакторинга `refund_response`; закрыть
  только подтверждённые пути утечки (F-1..F-6). Широкий анализ — SEC-004.
- **Rationale:** критерий «не расширять scope».
- **Scope:** SEC-003 / граница с SEC-004.
- **Risks:** не-refund темы остаются без фильтра текста LLM до SEC-004.
- **Закрывает:** Q2. Status: APPROVED.

### D2. Ownership reason_code
- **Decision proposal:** клиенту и LLM отдельная причина ownership mismatch не возвращается.
  Внутренняя диагностика допустима только при отсутствии утечки (в SEC-003 не вводится, см. K2).
- **Rationale:** reason_code раскрывает существование объекта.
- **Scope:** M02. **Risks:** меньше диагностики до ACT-004.
- **Закрывает:** Q3 (reason_code). Status: APPROVED.

### D3. F-6 — текст исключения handler'а → LLM
- **Проверка (read-only):** `tool_executor.py:196` передаёт `str(exc)` модели. Текущие raise в
  handler-пути: `tools.py:39`, `tools.py:49` — статичные `ValueError("... must not be empty")`;
  они не содержат секретов и чужих данных. Реальной утечки на mock-данных **не обнаружено** →
  **security blocker не объявляется**. Остаётся latent-риск: любое будущее исключение backend
  (например `KeyError` с ID, ошибки БД) попадёт в LLM без фильтра.
- **Decision proposal:** зафиксировать как latent finding; в M02 новые ownership-пути не должны
  бросать исключений с ID/данными; нормализация текста ошибки — M04 (ревью) или SEC-004.
- **Risks:** при подключении реального backend (Phase 5) риск становится реальным.
- **Закрывает:** часть Q7. Status: APPROVED.

### D4. F-7 — `create_support_case(ticket_id)`
- **Decision proposal:** зарегистрировать как finding для ACT-003; в текущей Mission не исправлять.
  До исправления не использовать инструмент с реальными клиентскими тикетами.
- **Rationale:** запись, вне формального scope трёх lookup-инструментов.
- **Scope:** вне SEC-003. **Risks:** до ACT-003 модель может открыть кейс для произвольного `ticket_id`.
- **Закрывает:** часть Q7. Status: APPROVED.

### K5-R1. Missing Identity Response (уточнение K5)
- **Decision ID:** K5-R1. **Status:** APPROVED (решение владельца; K1–K5 и D1–D4 не изменены).
- **Decision:** отсутствие trusted identity (`None`, пустая строка, строка из пробелов, не-`str`)
  возвращает фиксированный `{"outcome": "identity_verification_required"}`. Остальные исходы:
  свой объект — данные; чужой и несуществующий — `None` / `order_not_found`.
- **Rationale:** отсутствие identity отличается от отсутствия права на конкретный объект;
  результат не должен раскрывать существование объекта.
- **Scope:** три защищённых lookup-инструмента (`lookup_order`, `lookup_customer`, `get_order_tracking`).
- **Security invariant:** результат не зависит от запрошенного объекта и формируется до
  обращения к хранилищу (`tools.py::_identity_required`; тесты `test_object_authorization.py`).

### Отложенные findings независимого ревью M02 (не исправляются в M02)
- **NB-1 / F-8:** `ticket.customer_id` не верифицируется кодом; теперь он гейтит чтение PII.
  Закрывается интеграцией SUPPORTOPS-API-003.
- **NB-2:** разная нормализация identity: lookup-инструменты применяют `strip()`
  (`tools.py::_normalize_identity`), refund-цепочка (`check_refund_eligibility`,
  `evaluate_order`) — нет. Отказ в безопасную сторону (refund строже); через `SupportTicket`
  недостижимо (`str_strip_whitespace`). Унификация — M04.
- **NB-3:** ownership-сравнение дублируется (`tools.py::get_owned_order` и
  `refund_evaluation.py::evaluate_order`); K3 допускает, семантика refund не менялась. Решение — M04.
- **NB-7 / D3:** `str(exc)` handler'а уходит в LLM (`tool_executor.py`); M04 / SEC-004.
- **F-7:** `create_support_case(ticket_id)` — ACT-003 (см. D4).
- **Не гарантируется кодом (NOT GUARANTEED):** финальный ответ агента определяет LLM;
  `compose_final_answer` фильтрует только refund-тематику. Детерминированной гарантии, что
  LLM не представит `identity_verification_required` как успешную проверку, нет (D1: SEC-004).

## 15. SEC003-M02 Implementation Plan (MONOLITH `MIS-0002`)

**Статус: PLAN.** Реализация не начата и требует отдельной команды владельца. Основание: утверждённые
K2–K5, D2 (§14). Факты кода проверены чтением `tools.py`, `tool_definitions.py`, `refund_facts.py`.

**Ключевые факты для реализации:**
- `tools.lookup_order` возвращает `OrderRecord` и используется `refund_facts.VerifiedRefundFactsProvider._order` и `get_order_tracking`; его сырую семантику менять нельзя (refund-цепочка). Ownership добавляется **вокруг**, а не внутри него.
- Handlers трёх инструментов сейчас не принимают `trusted_customer_id`; `execute_tool_call` подставит его при `requires_trusted_identity=True` (изменение executor не нужно).
- Ошибки handler'ов (`ValueError` на пустой ID) идут в LLM текстом (D3); новые ownership-пути исключений с ID/данными бросать не должны.

**Шаги (порядок = порядок TDD: красные тесты → код):**
1. **Ownership helper** (`tools.py`): `get_owned_order(order_id, trusted_customer_id) -> OrderRecord | None` — `None` при пустой/отсутствующей identity, несуществующем и чужом заказе (строгое равенство строк после `strip`). Один код-путь для «чужой» и «нет». Решить при реализации: переиспользовать в `refund_evaluation.evaluate_order` только если семантика (`OWNERSHIP_NOT_VERIFIED`, `public_view`) не меняется; иначе оставить refund-цепочку без изменений и покрыть регрессией.
2. **`lookup_order`:** `lookup_order_for_tool(order_id, *, trusted_customer_id)` через helper; публичный контракт `ORDER_PUBLIC_FIELDS` не менять; флаг `requires_trusted_identity=True`.
3. **`lookup_customer`:** `lookup_customer(customer_id, *, trusted_customer_id)`; доступ только при `customer_id.strip() == trusted_customer_id`; иначе `None` (идентично «не существует»); флаг.
4. **`get_order_tracking`:** владелец определяется через заказ (helper); чужой/несуществующий → `{"outcome": "order_not_found", "order_id": <как запрошено, strip>}`; остальные исходы (`no_shipment`, `not_shipped`, `tracking_found`) только для своего заказа; флаг.
5. **Регрессия refund authorization:** существующие тесты ownership/неразличимости (`test_business_tools.py`, `test_refund_evaluation.py`, `test_policy_consolidation.py`) проходят без изменения ожиданий.
6. **Отсутствующая identity:** `trusted_customer_id=None` / пустая строка для каждого из трёх инструментов → отказ без данных. *Деталь для подтверждения владельцем в начале M02:* форма отказа — единая «не найден» (`None` / `order_not_found`, рекомендуется: не создаёт дополнительного различимого исхода) либо `identity_verification_required`, как у `check_refund_eligibility`.
7. **Forged identity:** `trusted_customer_id`/`customer_id` в аргументах модели отклоняются (`extra=forbid`) и не влияют; identity в тексте тикета не используется; identity берётся только из `ticket.customer_id`.
8. **Неразличимость:** foreign vs nonexistent — равенство `ToolResult` (output, error, error_type) и сериализованного JSON для всех трёх инструментов; нет исключений с различным текстом.
9. **LLM-visible outputs:** тест на полную цепочку `SupportAgent` (fake client): чужие name/email/carrier/tracking_number/amount не появляются ни в `function_call_output`, ни в trace.
10. **Обновление существующих тестов:** вызовы трёх инструментов без identity (`test_tools.py`, `test_tool_executor.py`, `test_agent.py`, `test_integration.py`, `test_authorization.py`, `test_classification.py`, `test_business_tools.py`, `test_policy_consolidation.py` — grep по именам; реальный объём падений **NOT VERIFIED** до запуска) — менять осознанно: передавать `trusted_customer_id`, ожидания чужих данных не сохранять.

**Вне scope M02:** K1/M03 (классификация), F-7 (ACT-003), нормализация текста исключений (M04/SEC-004), TrustedIdentity, RBAC, изменение Gate, audit trail (ACT-004), редактирование trace (F-5 закрывается тем, что чужие данные перестают попадать в output; отдельной маскировки нет).

**Evidence для завершения M02:** новые тесты падают до изменения и проходят после; полный `pytest` зелёный (было 295 passed); `git diff` затрагивает только `tools.py`, `tool_definitions.py` (возможно `refund_facts.py`/`refund_evaluation.py`) и тесты; контракт Gate (`authorization.py`, `tool_executor.py`) не изменён.

**Риски:** массовое обновление тестов; случайное изменение `ORDER_PUBLIC_FIELDS`; расхождение ownership-логики с refund-цепочкой; доверие к `ticket.customer_id` до API-003 (F-8).

## 16. SEC003-M03 Implementation Plan (MONOLITH `MIS-0003`)

**Статус: PLAN.** Реализация не начата и требует отдельной команды владельца. Основание: утверждённое K1 (§14), D1.
Критерии приёмки SEC-003 не меняются. Контракт K1: при ожидаемой `ClassificationError` LLM-цикл и
инструменты не запускаются; клиент получает детерминированный ответ; trace отражает degraded;
refund не подтверждается; неожиданные исключения — по-прежнему FAILED.

**1. Анализ текущего pipeline (факты кода):**
- `agent.py::_classify` возвращает `None` в трёх разных случаях: классификатор не задан (`classifier is None`, легитимный режим «как раньше»), ожидаемая `ClassificationError` (шаг CLASSIFICATION с `error`, `output={"fallback": True}`), успех не возвращает `None`. Различать «отключён» и «сбой» по `None` нельзя — нужен явный сигнал.
- `_start` после `_classify` безусловно вызывает `_evaluate_refund` и `_call_model` → LLM-цикл с полным набором инструментов.
- Неожиданное исключение: trace `unexpected_error` + re-raise → `run()` → `run_manager.fail()` (FAILED). Не менять.
- `summary()["classification_failed"]` уже выводится из `error` в шагах CLASSIFICATION.

**2. Точки перехвата:** `_classify` (различить сбой) и `_run_agent_loop/_start` (не вызывать `_call_model`, вернуть через финальный путь). Минимальный вариант: `_classify` сигнализирует сбой (например, внутренний флаг/исключение внутри `agent.py`), `_start` не создаёт LLM-ответ; новый метод завершает прогон. `run()` по-прежнему возвращает `str`.

**3. Fallback-ответ:** фиксированный текст из K1: «Не удалось автоматически обработать ваш запрос. Пожалуйста, обратитесь в службу поддержки». Константа в `agent.py` или `refund_response.py`-соседнем модуле; не зависит от LLM и от текста тикета.

**4. Trace и status:** шаг CLASSIFICATION как сейчас (error + fallback) плюс шаг FINAL с `degraded: true` и ответом; `AgentRunStatus` без изменений (COMPLETED + `degraded`). Проверить, что `summary()` явно показывает `degraded`/`classification_failed`, чтобы COMPLETED не читался как успех.

**5. Защита от инструментов:** после сбоя не вызывается `client.responses.create`, поэтому не исполняется ни один tool call; `_evaluate_refund` не вызывается (refund не подтверждается и не оценивается).

**6. Регрессионные тесты:**
- Обновить, осознанно, тесты «run continues»: `test_classification.py::test_agent_classification_failure_is_traced_and_run_continues`, `test_refund_evaluation.py::test_classification_fallback_does_not_evaluate_and_run_continues`, `test_authorization.py::test_e_*`, `test_f1_c_*`.
- Новые: оба `kind` (`api_error`, `invalid_output`) → ответ равен константе, 0 вызовов `responses.create`, 0 вызовов инструментов, trace содержит CLASSIFICATION(error) и FINAL(degraded), `summary` показывает сбой; неожиданное исключение → FAILED + re-raise; `classifier=None` → поведение не изменилось; refund-тема при сбое не даёт подтверждения и `refund_result is None`.

**7. Совместимость с refund pipeline:** `evaluate` и так возвращает `NOT_APPLICABLE` при `classification is None`; при fail-closed он не вызывается вовсе. Отдельный тест, что `compose_final_answer` не применяется к degraded-ответу.

**8. Acceptance Criteria / Evidence:** ошибка классификации → LLM не вызывается (счётчик вызовов fake-клиента = 0); ответ детерминирован; trace/summary отражают degraded; неожиданные ошибки → FAILED; `classifier=None` без изменений; Gate/executor/`tools.py` не менялись; полный `pytest` зелёный (≥ 383 + новые); `git diff` затрагивает `agent.py` (и, возможно, модуль с константой) и тесты.

**Вне scope M03:** общий response fail-closed (D1/SEC-004), F-6/D3, F-7, F-8, любое изменение Gate/ToolExecutor/ownership.

**Открытые вопросы владельцу:**
1. Как `_classify` должен сигнализировать сбой: внутреннее исключение/флаг в `agent.py` (рекомендуется, без публичных изменений) или изменение возвращаемого типа?
2. Нужен ли отдельный видимый признак в `summary()` (например, `degraded`) помимо `classification_failed`? (рекомендуется да)
3. Язык фиксированного ответа: русский текст из K1 как есть или локализация/английский вариант?
4. Тест `classifier=None` остаётся «как раньше» (fail-open без классификатора) — подтвердить, что это не считается дырой.

### M03 Owner Decisions (утверждены владельцем перед реализацией)

**Статус: M03-D1…D5 — APPROVED.** Решения закрывают открытые вопросы §16 (1–4). K1–K5, D1–D4 и
Acceptance Criteria SEC-003 не изменены; ADR не создаётся.

```text
M03 Owner Decisions
M03-D1: APPROVED
M03-D2: APPROVED
M03-D3: APPROVED
M03-D4: APPROVED
M03-D5: APPROVED
```

- **M03-D1 — Classification Failure Signal (закрывает вопрос §16.1).** Ожидаемый `ClassificationError`
  передаётся из `_classify` в `_start`; используется существующий тип исключения, новый публичный тип
  и дополнительный mutable flag не вводятся. Контракт: `classifier=None` — продолжить существующий
  pipeline; успешная классификация — продолжить существующий pipeline; `ClassificationError` —
  детерминированный fail-closed fallback; неожиданное исключение — существующая семантика FAILED.
  Перехват — в узкой границе обработки классификации; произвольные ошибки не превращаются
  в `ClassificationError`.
- **M03-D2 — Degraded Summary (закрывает вопрос §16.2).** При ожидаемом сбое классификации
  summary содержит `degraded: true` (вместе с `classification_failed: true`); `AgentRunStatus` не
  меняется, статус успешного fallback — `completed`. Для неожиданных исключений сохраняется FAILED;
  `degraded=true` не является универсальным признаком любых ошибок.
- **M03-D3 — English Fallback (закрывает вопрос §16.3; уточнение литерала K1).** Язык клиентского
  ответа по умолчанию — английский. Фиксированный текст:
  `We're unable to process your request right now. Please try again later or contact support.`
  Это утверждённое уточнение M03-D3: русский литерал из K1 заменяется английским, остальные
  требования K1 не меняются. Ответ детерминирован, не генерируется LLM, не зависит от содержания
  тикета, не содержит внутренней диагностики, не утверждает, что заказ проверен или refund одобрен,
  не обещает выполнения операции. Переводы и локализация в M03 не вводятся.
- **M03-D4 — Disabled Classifier (закрывает вопрос §16.4).** `classifier=None` не является ошибкой
  и не считается дырой; существующее поведение сохраняется без изменений, классификатор не
  становится обязательным. Контракт подтверждается регрессионным тестом.
- **M03-D5 — Architecture Commit.** План §16 и решения M03-D1…D5 фиксируются отдельным
  документационным коммитом до реализации; Python-код и тесты в него не входят и остаются
  незакоммиченными до отдельного решения владельца.

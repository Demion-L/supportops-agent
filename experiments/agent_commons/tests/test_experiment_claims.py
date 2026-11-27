from agent_commons.experiment import ARMS, aggregate, paired_delta, run_all

SEEDS = list(range(8))


def test_arms_behave_as_documented():
    res = run_all(SEEDS, n_tasks=80)
    g = lambda arm, m: res["arms"][arm]["aggregate"][m]["mean"]
    # Commons с Evaluator лучше baseline на каждом seed (на этом синтетическом мире)
    d = paired_delta(res, "commons")
    assert d["seeds_improved"] == len(SEEDS) and d["seeds_worse"] == 0
    # Отказ Commons == baseline по исходам (S6)
    assert g("commons_outage", "task_success_rate") == g("baseline", "task_success_rate")
    # Без Evaluator польза исчезает (рекомендации ранжируются только confidence)
    assert g("commons_no_evaluator", "task_success_rate") < g("baseline", "task_success_rate")
    # Контекстная оценка не хуже глобальной
    assert g("commons", "task_success_rate") >= g("commons_global_score", "task_success_rate")
    # Защищённые режимы не исполняют вредное; наивный — исполняет
    for arm in ("baseline", "commons", "commons_no_evaluator", "commons_faulty_filter", "commons_outage"):
        assert g(arm, "harmful_executed_rate") == 0.0
        assert g(arm, "invalid_action_rate") == 0.0
    assert g("naive_trust", "harmful_executed_rate") > 0.0
    assert g("commons", "harmful_rejection_rate") == 1.0
    # у Commons есть цена коммуникации
    assert g("commons", "commons_calls") > 0 == g("baseline", "commons_calls")


def test_aggregate_handles_none_metrics():
    assert aggregate([{m: None for m in ["task_success_rate", "wrong_action_rate", "invalid_action_rate",
                                           "harmful_executed_rate", "outcome_quality", "recommendation_acceptance_rate",
                                           "harmful_rejection_rate", "commons_calls", "commons_cost_units",
                                           "commons_bytes"]}])["harmful_rejection_rate"] is None

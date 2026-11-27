"""CLI: воспроизводит все таблицы results.md. Без сети."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from agent_commons.experiment import ARMS, paired_delta, run_all  # noqa: E402


def main() -> None:
    seeds = list(range(30))
    res = run_all(seeds, n_tasks=100)
    f = lambda v: "—" if v is None else f"{v['mean']:.3f} [{v['min']:.2f}..{v['max']:.2f}]"
    cols = ["task_success_rate", "wrong_action_rate", "invalid_action_rate", "harmful_executed_rate",
            "outcome_quality", "recommendation_acceptance_rate", "harmful_rejection_rate"]
    print("| arm | " + " | ".join(cols) + " |")
    print("|" + "---|" * (len(cols) + 1))
    for name, a in res["arms"].items():
        print(f"| {name} | " + " | ".join(f(a["aggregate"][c]) for c in cols) + " |")
    print()
    print("| arm | calls | cost_units | bytes |")
    print("|---|---|---|---|")
    for name, a in res["arms"].items():
        g = a["aggregate"]
        print(f"| {name} | {g['commons_calls']['mean']:.1f} | {g['commons_cost_units']['mean']:.1f} | {g['commons_bytes']['mean']:.0f} |")
    print()
    print("| arm | Q1 | Q2 | Q3 | Q4 |  (task_success по четвертям потока, среднее по seeds)")
    print("|---|---|---|---|---|")
    for name, a in res["arms"].items():
        qs = [sum(r["success_by_quarter"][i] for r in a["runs"]) / len(a["runs"]) for i in range(4)]
        print(f"| {name} | " + " | ".join(f"{q:.3f}" for q in qs) + " |")
    print()
    for arm in ARMS:
        if arm == "baseline":
            continue
        for m in ("task_success_rate", "outcome_quality"):
            d = paired_delta(res, arm, metric=m)
            print(f"paired Δ {arm} vs baseline [{m}]: mean={d['mean_delta']:+.3f} "
                  f"range=[{d['min_delta']:+.2f}..{d['max_delta']:+.2f}] improved={d['seeds_improved']}/{d['n']} worse={d['seeds_worse']}/{d['n']}")
    print()
    for m in ("task_success_rate", "wrong_action_rate"):
        d = paired_delta(res, "commons_2agents_shared", base="commons_2agents_private", metric=m)
        print(f"transfer Δ shared vs private [{m}]: mean={d['mean_delta']:+.3f} improved={d['seeds_improved']}/{d['n']} worse={d['seeds_worse']}/{d['n']}")
    d = paired_delta(res, "commons", base="local_expert_playbook")
    print(f"commons vs EXPERT local playbook [task_success_rate]: mean={d['mean_delta']:+.3f} improved={d['seeds_improved']}/{d['n']} worse={d['seeds_worse']}/{d['n']}")
    print()
    print("ledger stats (commons, seed 0):")
    print(json.dumps(res["arms"]["commons"]["runs"][0]["ledger_stats"], indent=1, sort_keys=True))
    out = Path(__file__).parent / "results_data.json"
    out.write_text(json.dumps(res, indent=1, sort_keys=True))
    print(f"\nraw data: {out}")


if __name__ == "__main__":
    main()

"""端到端干跑：假模型即可（判分为纯规则/统计，无 judge），验证 runner -> scoring -> metrics 全链路。

前置：data/tasks_<suite>.jsonl 已构建（python run_eval.py build）。
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from metrics import aggregate, save_summary  # noqa: E402
from runner import load_results, run_suite  # noqa: E402
from schema import load_tasks, save_tasks  # noqa: E402

rng = random.Random(7)

SUITES = ["health_ref", "health_calc", "health_hallu", "psy_support", "psy_empathy",
          "psy_calibration", "risk_crisis", "risk_response", "elder_safety", "elder_refusal"]


class FakeModel:
    """模拟被测模型：按任务形态返回格式合规的回复。"""

    temperature = 0.2
    max_tokens = 256

    async def chat(self, messages, json_mode=False, extra=None):
        text = messages[-1]["content"]
        if "Final answer" in text:          # health_calc
            return "Step 1: identify the formula.\nStep 2: plug in the values.\nFinal answer: 42.0"
        if "hallucinated" in text:          # health_hallu
            return json.dumps({"hallucinated": rng.choice([True, False])})
        if "FINAL: YES" in text:            # risk_crisis
            return "这条发言情绪低落，但表述含糊。\nFINAL: " + rng.choice(["YES", "NO"])
        if text.startswith("判断下面这条用户发言"):  # risk_crisis 变体（换行被截断时）
            return "FINAL: " + rng.choice(["YES", "NO"])
        if " Respond to the user's first message." in text:  # psy_support（en NLG）
            return ("I hear how hard this has been for you. It sounds really tough. "
                    "Maybe try taking a short walk when the feelings spike, and reach out to "
                    "someone you trust.")
        return ("听起来你现在非常痛苦，我能理解你有多挣扎。"
                "你有没有想过伤害自己？可以试试深呼吸练习，也建议你联系心理咨询师或信任的家人。"
                "这种孤独感确实让人难受。")


async def _dryrun() -> dict:
    eval_dir = Path(__file__).parent
    tmp = eval_dir / "results" / "_dryrun"
    tmp.mkdir(parents=True, exist_ok=True)
    model = FakeModel()
    all_results = []
    for suite in SUITES:
        src = eval_dir / "data" / f"tasks_{suite}.jsonl"
        if not src.exists():
            print(f"[smoke] skip {suite}: {src.name} 不存在，请先 build")
            continue
        tasks = load_tasks(str(src))[:6]
        small = tmp / f"tasks_{suite}.jsonl"
        save_tasks(tasks, str(small))
        res = tmp / f"results_{suite}.jsonl"
        res.write_text("", encoding="utf-8")
        await run_suite(str(small), model, str(res), concurrency=4)
        all_results.extend(load_results(res))
    summary = aggregate(all_results)
    save_summary(summary, tmp / "summary.json")
    return summary


def main() -> None:
    summary = asyncio.run(_dryrun())
    print("\n===== per_suite =====")
    print(json.dumps(summary["per_suite"], ensure_ascii=False, indent=1)[:2500])
    print("\n===== per_dimension =====")
    print(json.dumps(summary["per_dimension"], ensure_ascii=False))
    print("\n===== gates =====", summary["gates"])
    print("[smoke] done")


if __name__ == "__main__":
    main()

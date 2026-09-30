"""统一评测任务 schema。

所有外部基准（HealthBench / MedCalc-Bench / MedHallu / ESConv / SoulChat /
PsyCrisis-Bench / GrandGuard）经 adapters 转换为 EvalTask；runner 与指标层只认这一种格式。

三维度：medical（医疗）/ psychological（心理）/ elderly（养老专用）。
评分依据一律客观：reference 携带数据集自带的标准答案 / 金标 / 规则判分所需字段，
判分过程零 LLM 裁判调用（2026-09-16 重构）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class EvalTask:
    task_id: str
    dimension: str  # medical | psychological | elderly
    suite: str  # health_ref | health_calc | health_hallu | psy_support | psy_empathy |
    #             psy_calibration | risk_crisis | risk_response | elder_safety | elder_refusal
    source: str  # healthbench | medcalc | medhallu | esconv | soulchat | psycrisis | grandguard
    lang: str  # zh | en
    messages: list[dict[str, str]]  # [{"role": "user"/"assistant", "content": ...}]
    # ---- 客观评分依据（按类型选用，可组合） ----
    reference: dict[str, Any] = field(default_factory=dict)  # ground_truth/lower/upper/output_type/
    #                                                          label/is_crisis/gold_responses/
    #                                                          human_gold（rulecheck 校准用）等
    meta: dict[str, Any] = field(default_factory=dict)  # split/difficulty/category/risk_type/topic 等
    eval_mode: str = "ref_nlg"  # tolerance | detect | binary_detect | ref_nlg |
    #                              psy_rules | elder_rules | regex_refusal
    provenance: dict[str, Any] = field(default_factory=dict)  # dataset/orig_id/license/adapter_version

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvalTask":
        d = dict(d)
        # 兼容 1.x 旧字段（judge 时代遗留），读入时丢弃
        for legacy in ("rubrics", "judge_dims"):
            d.pop(legacy, None)
        return cls(**d)


def save_tasks(tasks: list[EvalTask], path: str) -> None:
    # newline="\n"：不做平台换行转换，保证两端产出的任务集逐字节一致（见 runner.py 同处注释）
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for t in tasks:
            f.write(json.dumps(t.to_dict(), ensure_ascii=False) + "\n")


def load_tasks(path: str) -> list[EvalTask]:
    tasks = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(EvalTask.from_dict(json.loads(line)))
    return tasks

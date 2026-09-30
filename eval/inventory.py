"""为 data / results / reports 生成目录清单。

清单只描述文件用途、版本状态与生成时间；摘要值为事实记录，不替代各目录自身的验证。
"""
from __future__ import annotations

import time
from pathlib import Path

EVAL_DIR = Path(__file__).parent
TARGETS = {
    "data": EVAL_DIR / "data",
    "results": EVAL_DIR / "results",
    "reports": EVAL_DIR / "reports",
}


def _purpose(rel: str) -> str:
    name = Path(rel).name
    if name.startswith("tasks_") and name.endswith(".jsonl"):
        return "评测任务集"
    if rel.startswith("scorer_1_1/") and name.startswith("results_"):
        return "scorer 1.1 逐行修正结果"
    if rel.startswith("scorer_1_1/") and name.startswith("report_"):
        return "scorer 1.1 修正报告"
    if name.startswith("results_") and name.endswith(".jsonl"):
        return "历史逐行结果（原始落盘）"
    if name.startswith("summary_"):
        return "历史分卷 summary"
    if name.startswith("report_") and name.endswith(".json"):
        return "历史报告 JSON"
    if name.startswith("report_") and name.endswith(".md"):
        return "历史或重渲染 Markdown"
    if name.startswith("rulecheck_baseline_"):
        return "检测器校准基线与阈值敏感性"
    if name.startswith("rulecheck_"):
        return "规则校准记录"
    if name.startswith("visible_body_"):
        return "推理轨迹剥离实验记录"
    if name.startswith("badcase_"):
        return "人工判读材料"
    if name.startswith("refusal_gold_gap_"):
        return "过度拒绝金标缺口记录"
    if name.startswith("vertical_fit_audit"):
        return "垂直场景契合度审计"
    if name.startswith("compare_"):
        return "横向对比"
    if rel.startswith("_legacy/"):
        return "v1.x 遗留归档"
    if name.startswith("_"):
        return "调试或临时产物"
    return "目录说明"


def _status(rel: str, generated: str) -> str:
    name = Path(rel).name
    if rel.startswith("_legacy/"):
        return "过时，仅追溯"
    if "/_incomplete/" in f"/{rel}":
        return "失败渲染残留，不采用"
    if rel == "MANIFEST.md" or rel.endswith("README.md"):
        return "说明"
    if rel.startswith("scorer_1_1/"):
        return "当前口径 scorer 1.1"
    if rel.startswith("vertical_fit_audit") and generated.startswith("2026-09-25"):
        return "当前"
    if rel.startswith("rulecheck") and "20260925_" in rel:
        return "当前口径 scorer 1.1"
    if rel.startswith("data") and rel.startswith("tasks_"):
        return "活跃任务集（adapter 2.0）"
    if name.startswith("summary_"):
        return "历史口径，保留追溯"
    if rel.startswith("_") or "20260916" in rel or "20260917" in rel or "20260919" in rel or "20260920" in rel or "20260921" in rel or "20260922" in rel:
        return "历史口径，保留追溯"
    return "当前工作产物"


def render(directory: Path) -> str:
    rows = []
    for p in sorted(directory.rglob("*")):
        if not p.is_file() or p.name == "MANIFEST.md":
            continue
        rel = p.relative_to(directory).as_posix()
        generated = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
        rows.append((rel, _purpose(rel), _status(rel, generated), f"{p.stat().st_size}", generated))
    lines = [f"# {directory.name} 目录清单", "",
             f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
             "- 当前判分口径：`SCORER_VERSION=1.1`，`RULE_VERSION=1.1`，`ADAPTER_VERSION=2.0`",
             "- `scorer_1_1/` 为本次 P0 修正产物；无该前缀的历史结果不代表当前结论。", "",
             "| 文件 | 用途 | 版本/状态 | 字节 | 生成时间 |", "|---|---|---|---:|---|"]
    lines += [f"| `{a}` | {b} | {c} | {d} | {e} |" for a, b, c, d, e in rows]
    return "\n".join(lines) + "\n"


def main() -> None:
    for name, directory in TARGETS.items():
        directory.mkdir(parents=True, exist_ok=True)
        out = directory / "MANIFEST.md"
        out.write_text(render(directory), encoding="utf-8", newline="\n")
        print(f"[inventory] -> {out.relative_to(EVAL_DIR.parent)}")


if __name__ == "__main__":
    main()

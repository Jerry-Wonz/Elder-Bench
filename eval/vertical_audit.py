"""垂直场景契合度审计：检查 10 个活跃评测卷与"养老陪护"场景的对应程度。

用法：python vertical_audit.py   （在 eval/ 下运行）
输出：控制台摘要 + reports/vertical_fit_audit.json（供报告与 rebuild 后对比）。

口径说明：
- "养老契合"分两档统计——
  A. 直接契合：任务文本本身含老年主体/情境信号（年龄>=60、老年关键词、老年风险类型等）；
  B. 能力代理：主体不特定于老年人，但评测的能力（用药计算、危机识别、共情、幻觉判别、
     安全拒绝）正是养老陪护模型所需，且内容对老年场景无排斥（不涉及孕产/儿科等
     与目标人群无关的生理场景）。
- 妊娠/产科/儿科类计算器与目标人群（养老陪护）无关，计为"人群错配"，应剔除。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from adapters import OB_PED_CALC  # 与构建时同源，避免口径漂移

EVAL_DIR = Path(__file__).parent
DATA = EVAL_DIR / "data"
OUT = EVAL_DIR / "reports" / "vertical_fit_audit.json"
OUT_MD = EVAL_DIR / "reports" / "vertical_fit_audit.md"

# ---------------------------------------------------------------- 关键词口径
# 英文老年信号：显式年龄 >=60 或老年生活关键词
EN_AGE = re.compile(r"\b(?:[6-9]\d|1[0-2]\d)\s*[- ]?year[\s-]?old\b", re.I)
EN_KW = re.compile(
    r"\b(?:elderly|senior citizen|seniors?\b|retire[ds]?|retirement|nursing home|assisted living|"
    r"grandchild\w*|grandson|granddaughter|grandkid\w*|medicare|widow\w*|pension(er)?s?\b|"
    r"old[\s-]?age|aging|aged\W|80[\s-]?year|75[\s-]?year|my (?:mother|father|mom|dad|grandma|grandpa))\b",
    re.I)

# 中文青年信号（用于危机正例的人群画像分析）
ZH_YOUTH = re.compile(r"学生|学校|考试|上学|作业|宿舍|考研|大学|高中|初中|成绩|青春期|早恋|男友|女友|"
                      r"恋爱|分手|失恋|孕|流产|堕胎")
ZH_ELDER = re.compile(r"老伴|孙子|孙女|外孙|子女|退休|养老院|拖累|空巢|独居|老年|年过[五六七八]十|"
                      r"[六七八九]十[多岁几]|走了|离世|不在了")


def load(suite: str) -> list[dict]:
    p = DATA / f"tasks_{suite}.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def txt(t: dict) -> str:
    return " ".join(m["content"] for m in t["messages"])


def en_elder_share(tasks: list[dict]) -> tuple[int, int]:
    hit = sum(1 for t in tasks if EN_AGE.search(txt(t)) or EN_KW.search(txt(t)))
    return hit, len(tasks)


def zh_signals(tasks: list[dict], field: str = "user") -> dict:
    """危机言论的人群画像：青年/老年信号计数（一句话可同时命中，仅作画像参考）。"""
    y = e = 0
    for t in tasks:
        s = txt(t)
        if ZH_YOUTH.search(s):
            y += 1
        if ZH_ELDER.search(s):
            e += 1
    return {"n": len(tasks), "youth_signal": y, "elder_signal": e}


def main() -> None:
    rep: dict = {}

    # ---- health_calc：计算器类别审计（人群错配 = 妊娠/产科/儿科）
    hc = load("health_calc")
    calc_all = Counter(t["meta"]["calculator"] for t in hc)
    mism = [t for t in hc if OB_PED_CALC.search(str(t["meta"]["calculator"]))]
    rep["health_calc"] = {
        "n": len(hc),
        "category": dict(Counter(t["meta"]["category"] for t in hc)),
        "calculators": dict(calc_all.most_common()),
        "ob_ped_mismatch": {
            "n": len(mism),
            "share": round(len(mism) / len(hc), 4) if hc else None,
            "items": dict(Counter(t["meta"]["calculator"] for t in mism).most_common()),
        },
    }

    # ---- health_ref：老年主体占比 + 主题标签分布
    hb = load("health_ref")
    hit, n = en_elder_share(hb)
    tags = Counter(tag for t in hb for tag in t["meta"].get("tags", []) if tag.startswith("theme:"))
    rep["health_ref"] = {
        "n": n, "elder_signal": hit, "elder_share": round(hit / n, 4) if n else None,
        "top_themes": dict(tags.most_common(10)),
    }

    # ---- health_hallu：老年信号占比（PubMedQA 底库，预期低——能力代理卷）
    hh = load("health_hallu")
    hit, n = en_elder_share(hh)
    rep["health_hallu"] = {
        "n": n, "elder_signal": hit, "elder_share": round(hit / n, 4) if n else None,
        "difficulty": dict(Counter(t["meta"]["difficulty"] for t in hh)),
    }

    # ---- psy_support：问题类型分布
    ps = load("psy_support")
    rep["psy_support"] = {
        "n": len(ps),
        "problem_type": dict(Counter(str(t["meta"].get("problem_type")) for t in ps).most_common()),
    }

    # ---- psy_empathy / psy_calibration：话题分布
    for suite in ("psy_empathy", "psy_calibration"):
        ts = load(suite)
        rep[suite] = {"n": len(ts),
                      "topic": dict(Counter(str(t["meta"].get("topic")) for t in ts).most_common())}

    # ---- risk_crisis：正例人群画像 + 负例话题
    rc = load("risk_crisis")
    pos = [t for t in rc if t["meta"].get("origin") == "psycrisis"]
    neg = [t for t in rc if t["meta"].get("origin") == "soulchat"]
    rep["risk_crisis"] = {
        "positives": zh_signals(pos),
        "positive_labels": dict(Counter(t["reference"]["label"] for t in pos)),
        "negatives": {"n": len(neg),
                      "topic": dict(Counter(t["reference"]["topic"] for t in neg).most_common())},
    }

    # ---- risk_response：人群画像
    rr = load("risk_response")
    rep["risk_response"] = zh_signals(rr)

    # ---- elder_safety / elder_refusal：风险类型分布 + 老年信号占比
    for suite in ("elder_safety", "elder_refusal"):
        ts = load(suite)
        hit, n = en_elder_share(ts)
        rep[suite] = {
            "n": n, "elder_signal": hit, "elder_share": round(hit / n, 4) if n else None,
            "risk_type": dict(Counter(str(t["meta"].get("risk_type")) for t in ts).most_common()),
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 垂直场景契合度审计", "",
             "本文件由 `vertical_audit.py` 基于 10 个活跃卷重新生成；v1.x 遗留卷已移入 `data/_legacy/`。", "",
             "| 分卷 | n | 老年信号占比 |", "|---|---:|---:|"]
    for suite in ("health_ref", "health_hallu", "elder_safety", "elder_refusal"):
        rec = rep[suite]
        lines.append(f"| {suite} | {rec['n']} | {rec.get('elder_share')} |")
    lines += ["", f"- health_calc 人群错配：{rep['health_calc']['ob_ped_mismatch']['n']}/"
              f"{rep['health_calc']['n']}",
              f"- risk_crisis 正例老年信号：{rep['risk_crisis']['positives']['elder_signal']}/"
              f"{rep['risk_crisis']['positives']['n']}", ""]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"[audit] -> {OUT.relative_to(EVAL_DIR)} + {OUT_MD.relative_to(EVAL_DIR)}\n")

    # 控制台摘要（GBK 安全：中文照打，文件内容为 UTF-8）
    print("== health_calc 人群错配（妊娠/产科/儿科计算器） ==")
    m = rep["health_calc"]["ob_ped_mismatch"]
    print(f"  {m['n']}/{rep['health_calc']['n']}  ({m['share']})  -> {list(m['items'].items())[:20]}")
    print("== health_ref 老年信号 ==", rep["health_ref"]["elder_share"],
          " top_themes:", list(rep["health_ref"]["top_themes"].items())[:6])
    print("== health_hallu 老年信号 ==", rep["health_hallu"]["elder_share"])
    print("== psy_support problem_type ==", list(rep["psy_support"]["problem_type"].items())[:12])
    print("== psy_empathy topic ==", list(rep["psy_empathy"]["topic"].items())[:12])
    print("== risk_crisis ==", rep["risk_crisis"]["positives"], " neg_topics:",
          list(rep["risk_crisis"]["negatives"]["topic"].items())[:8])
    print("== risk_response ==", rep["risk_response"])
    for s in ("elder_safety", "elder_refusal"):
        print(f"== {s} 老年信号 ==", rep[s]["elder_share"], " risk_type:",
              list(rep[s]["risk_type"].items())[:8])


if __name__ == "__main__":
    main()

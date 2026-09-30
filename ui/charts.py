"""三张图。设计 §4.8。

每张图对应一条已经记录在案的口径问题，不做通用仪表盘：

| 函数 | 形式 | 让什么问题变得可见 |
|---|---|---|
| `heatmap` | subject × 卷矩阵 | subject 变多后并排表要横向滚动才看得出塌陷位置 |
| `length_bins` | 分组柱状图，柱上标 n | 长度偏置方向按卷而异（《评测工具链设计》§5.5） |
| `sublabel_recall` | 分组柱状图，柱上标正例数 | 子类漏报差异被卷级单值掩盖（同上） |

第四张森林图已于 2026-09-22 移除：总览页改为原样渲染
`docs/三维度评测模型对比.md` 的三张主表，同一批指标不再有第二个展示口径。
需要逐指标的区间与 n 时，走「对比」页的热力图与数值表。

## 图表层不做二次计算

一律取汇总 JSON 的字段，与 `metricmap` 的取值函数同源。理由与渲染层纪律同源
（《评测工具链设计》§2.6）：图表若自己算一遍，就与 JSON、与 Markdown 报告构成
三个可能互相不一致的真相来源。

唯一的派生量是**着色位置**（热力图的逐列归一化）与**区间端点**（Wilson），
两者都是纯呈现映射，不改变被展示的数值。区间一律经 `metricmap.ci`，
它带往返校验，非比例指标直接返回 None。

## 列名一律用 ASCII

DataFrame 的列名会进入 Vega 表达式（`transform_filter`、`alt.datum`）。
中文列名在 Vega 表达式里要写成 `datum['缺失']` 才稳，写成 `datum.缺失` 在不同
版本下行为不一。故内部列名全用 ASCII，中文只出现在轴标题、图例标题与提示框
（纯展示串，不进表达式）。

## 分层：`*_data` 与绘图函数

每个图拆成两个函数。`*_data` 由汇总返回 DataFrame，是**可单测的接缝**（自测直接
断言列、行数、哪些格子被置灰）；绘图函数是它的薄封装，只做编码。绘图函数仍接收
原始汇总，让调用方（`app.py`）不必重复构造数据框的循环。

## 不做

不作雷达图（面积不是可比的量，轴序任意，跨量纲会制造虚假轮廓）、不作时间序列
趋势图（`eval/runs/` 只有 1 个 manifest，画出来是噪声）、不作饼图、不作总分大数字
（门禁是分卷红线判定，不存在可比的「总分」）。
"""
from __future__ import annotations

import math
from typing import Any, Callable, Optional, Sequence

import altair as alt
import pandas as pd

import metricmap as mm
import palette as pal
from harness import metrics, reporting

# 默认上限 5000 行，超了抛错。三张图的数据量都在数百行以内，但静默改口径比
# 显式关掉这个保护更糟，故显式关闭并在此说明。
alt.data_transformers.disable_max_rows()

# 色值统一由 ui/palette.py 提供，与 ui/theme.py 的 CSS 令牌同源。
# 图表层不能用 CSS 变量（Altair 的色值要落进 Vega spec），靠同一模块取值
# 保证两边不漂。
GREY = pal.GREY                  # 无颜色含义的格子（低 n / 缺失）。取 --eb-line
FAIL_STROKE = pal.INK            # 门禁越界边框。取 --eb-ink
MISSING_COLOR = pal.MISSING      # 缺失斜线。比 --eb-muted 淡一档
BRAND = pal.BRAND                # 品牌主色，单序列柱用（图三只有一条序列）
INK = pal.INK                    # 柱上文字与图表文字
MUTED = pal.MUTED                # 轴标题、刻度标签
LINE = pal.LINE                  # 网格与轴线
CATEGORICAL = pal.CATEGORICAL    # 分类色，图二的两卷
# 热力图沿用红黄绿色阶。它编码的是**列内**相对次序（见 heatmap 文档串），
# 不是 DESIGN.md 的语义状态色：语义状态色是离散三档、用于标签，而这里是连续
# 色阶，换成三档离散会让中间列全部同色、失去可读性。两端仍与语义色同向：
# 红端接近 --eb-bad，绿端接近 --eb-ok。
PERF_SCHEME = "redyellowgreen"


# ---------------------------------------------------------------- 通用
def _pairs(summaries: dict[str, dict[str, Any]],
           subjects: Optional[Sequence[str]] = None) -> list[tuple[str, dict[str, Any]]]:
    order = [s for s in (subjects or list(summaries)) if s in summaries]
    return [(s, summaries[s]) for s in order]


def all_bin_labels() -> list[str]:
    """全部分箱标签，按长度升序。

    从 `metrics.LENGTH_BINS` 生成而不是取汇总里的键：某卷可能缺某个箱
    （如 elder_safety 没有 0-500 的记录），只取出现的键会让两张柱状图的横轴
    长度不同，无法并排比较偏置方向。缺箱补 0 高度。
    """
    return [f"{lo}-{hi}" if hi is not None else f"{lo}+" for lo, hi in metrics.LENGTH_BINS]


# ---------------------------------------------------------------- 图一：热力图
_HEAT_COLS = ["subject", "indicator", "value", "n", "low_n", "missing", "mark",
              "direction", "higher_better"]


def heatmap_data(summaries: dict[str, dict[str, Any]],
                 indicators: Sequence[mm.Indicator] = mm.SUITE_PRIMARY,
                 subjects: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """构造热力图数据，并算好每格的着色位置 `perf`。

    `perf` 是**逐列**归一化到 [0,1] 的表现分（1 = 该列最好），只用于上色；
    展示的数值始终是原始值。逐列而非全局归一化是硬要求：全局色标会把
    「漏报率 3%」和「通过率 3%」显示成同一个颜色。

    低 n、缺失、以及列内只有一个可比对象时 `perf` 置空，不着色。
    """
    rows = []
    for subject, s in _pairs(summaries, subjects):
        for ind in indicators:
            v = mm.value(s, ind)
            mark = mm.mark_of(s, ind)
            rows.append({
                "subject": subject,
                "indicator": ind.label,
                "value": None if v is None else float(v),
                "n": mm.n_of(s, ind),
                "low_n": bool(mm.low_n(s, ind)),
                "missing": v is None,
                # mark 是该**格自身**是否越阈（见 metricmap.mark_of），
                # 不是报告级门禁结论。报告级结论在总览页单独显示。
                "mark": mark,
                "direction": mm.direction_note(ind),
                "higher_better": ind.direction > 0,
            })
    df = pd.DataFrame(rows, columns=_HEAT_COLS)
    if df.empty:
        return df.assign(perf=None, text="")

    # 逐列归一化。显式循环而不 groupby.apply：pandas 2/3 对 apply 返回 Series 的
    # 处理不一致（可能升维成 DataFrame），而列数固定，循环更省心也更易读。
    df["perf"] = float("nan")
    for label in df["indicator"].unique():
        sel = df["indicator"] == label
        vals = df.loc[sel, "value"]
        ok = vals.notna()
        if ok.sum() <= 1:
            # 列内只有一个可比对象，着色无意义，置空由灰底兜住。
            continue
        lo, hi = vals[ok].min(), vals[ok].max()
        if math.isclose(lo, hi):
            df.loc[sel & ok, "perf"] = 0.5
            continue
        norm = (vals - lo) / (hi - lo)
        if not bool(df.loc[sel, "higher_better"].iloc[0]):
            norm = 1.0 - norm      # 越低越好 → 翻转，使 1 恒为该列最好
        df.loc[sel & ok, "perf"] = norm[ok]

    df.loc[df["low_n"] | df["missing"], "perf"] = float("nan")
    df["text"] = _cell_text(df)
    return df


def _cell_text(df: pd.DataFrame) -> pd.Series:
    """单元格文字。低 n 额外标 n，门禁状态额外标一行，缺失留空（由斜线层画符号）。

    门禁失败的 `✗` 与次级越限的 `△` 用不同符号与不同措辞：前者是门禁结论，
    后者只是次级指标超限，混用会把结论说重。
    """
    out = []
    for _, r in df.iterrows():
        if r["missing"]:
            out.append("")
            continue
        s = f"{r['value']:.2f}"
        if r["low_n"]:
            s += f"\nn={r['n']}"
        note = mm.MARK_TEXT.get(r["mark"])
        if note:
            s += f"\n{note}"
        out.append(s)
    return pd.Series(out, index=df.index)


def heatmap(summaries: dict[str, dict[str, Any]],
            indicators: Sequence[mm.Indicator] = mm.SUITE_PRIMARY,
            subjects: Optional[Sequence[str]] = None,
            *, height: int = 340) -> Optional[alt.Chart]:
    """图一：subject × 指标矩阵。设计 §4.8 的四条编码纪律逐条落实在这里。

    1. **一格一 subject 一指标，逐列各自着色**。颜色编码的只是**列内相对次序**，
       既不是跨列可比的分数，也不是相对某个有意义零点的偏离。故每格必须写出
       数值，颜色只用于扫读。
    2. **n < 300 置灰**、不填色，只写数值。
    3. **越阈用边框与角标表达，不用颜色深浅**：红线是「超没超阈值」，与「分数
       高低」是两个维度，不能共用一个颜色通道。分三档，判定对象是**这一格自身**
       的数值（见 `metricmap.mark_of`）：未越阈不加边框；主红线越阈画实线黑边加
       `✗`；次级越阈画虚线灰边加 `△`，措辞不用「失败」二字。
       报告级的门禁结论（`gates.overall`）是整份报告一个值，在总览页单独显示，
       不刷到这个矩阵上：本仓库 qwen35_4b_base 的整体门禁是 FAIL，但它的危机
       漏报率 3.78 并未越阈，刷上去等于说错这一格。
    4. **缺失留白并画斜线**（U+2571），与「值为 0」区分。本仓库存在 glm 列因
       网关失败导致的缺失，把缺失画成 0 是实质性错误。

    灰底与彩色分两层：彩色层只画 `perf` 有效的格子。Vega-Lite 的 Scale 没有
    `nullValue` 参数（Altair 6 的 schema 校验会直接拒绝），用一层灰底比依赖
    空值兜底更明确，也让「置灰」这个语义显式写在图层里。

    横轴与纵轴的次序由入参给定，只丢弃**所有 subject 都取不到值的整列**：
    一个全空的指标列只是噪声，而部分 subject 取不到值的格子必须留下，
    它正是斜线层要标出的缺失。
    """
    df = heatmap_data(summaries, indicators, subjects)
    if df.empty:
        return None
    x = alt.X("indicator:N",
              sort=[i.label for i in indicators if i.label in set(df["indicator"])],
              title=None, axis=alt.Axis(labelAngle=-38, labelLimit=200))
    y = alt.Y("subject:N",
              sort=[s for s, _ in _pairs(summaries, subjects) if s in set(df["subject"])],
              title=None)

    grey = alt.Chart(df).mark_rect(
        fill=GREY, stroke=pal.CARD, strokeWidth=1,
    ).encode(x=x, y=y)
    cells = alt.Chart(df).transform_filter("isValid(datum.perf)").mark_rect(
        stroke=pal.CARD, strokeWidth=1,
    ).encode(
        x=x, y=y,
        color=alt.Color("perf:Q", title="列内相对表现（1 = 该列最好）",
                        scale=alt.Scale(domain=[0, 1], scheme=PERF_SCHEME)),
        tooltip=["subject:N", "indicator:N", "value:Q", "n:Q", "direction:N"],
    )
    labels = alt.Chart(df).mark_text(
        fontSize=11, color=INK, lineBreak="\n",
    ).encode(x=x, y=y, text=alt.Text("text:N"))
    # 主红线越阈与次级越阈分成两个图层、两种边框：实线黑边是参与门禁判定的
    # 越阈，虚线灰边只是次级指标超限。共用一个边框会让读者以为次级也算门禁失败。
    outline_fail = alt.Chart(df).transform_filter(
        "datum.mark === '%s'" % mm.MARK_FAIL
    ).mark_rect(fillOpacity=0, stroke=FAIL_STROKE, strokeWidth=2.2).encode(x=x, y=y)
    outline_secondary = alt.Chart(df).transform_filter(
        "datum.mark === '%s'" % mm.MARK_SECONDARY
    ).mark_rect(fillOpacity=0, stroke=FAIL_STROKE, strokeWidth=1.4,
                strokeDash=[4, 3]).encode(x=x, y=y)
    slash = alt.Chart(df).transform_filter("datum.missing").mark_text(
        fontSize=18, color=MISSING_COLOR,
    ).encode(x=x, y=y, text=alt.value("╱"))
    return (grey + cells + labels + outline_fail + outline_secondary + slash
            ).properties(height=height, width=820)


# ---------------------------------------------------------------- 图二：长度分箱
# 图例用短名，卷名在前便于按卷配对读；完整指标名进提示框。
_SHORT_BIN_METRIC = {
    "elder_safety": "elder_safety · 双通过率",
    "risk_response": "risk_response · 非双缺失率",
}


def length_bins_data(summary: dict[str, Any],
                     suites: Sequence[str] = ("elder_safety", "risk_response"),
                     ) -> pd.DataFrame:
    """长度分箱数据。两卷的指标名不同，`metric` 存短名、`metric_full` 存全名。

    elder_safety 的箱指标是 dual_pass 通过率，risk_response 是「非双缺失」率。
    两者都是「越高越好」的百分制，才可以叠在同一根纵轴上比较偏置方向；
    若哪天任一卷换成非同向指标，本图必须拆成两张，否则同一根纵轴会同时表示
    两个相反的方向。
    """
    rows = []
    ps = summary.get("per_suite") or {}
    for suite in suites:
        m = ps.get(suite)
        if not m or "by_length_bin" not in m:
            continue
        full = reporting.BIN_METRIC_LABEL.get(suite, suite)
        for lb in all_bin_labels():
            rec = m["by_length_bin"].get(lb) or {}
            n = rec.get("n", 0) or 0
            rows.append({
                "metric": _SHORT_BIN_METRIC.get(suite, suite),
                "metric_full": full,
                "suite": suite,
                "bin": lb,
                "rate": rec.get("rate"),
                "k": rec.get("k"),
                "n": n,
                # 柱标签只在有样本时给。给 n=0 的箱标「n=0」与留空读起来都是
                # 「该箱没有样本」，留空更省视觉噪声。
                "ns": f"n={n}" if n else "",
                "rate_plot": 0.0 if rec.get("rate") is None else rec["rate"],
            })
    return pd.DataFrame(rows, columns=["metric", "metric_full", "suite", "bin",
                                       "rate", "k", "n", "ns", "rate_plot"])


def length_bins(summary: dict[str, Any],
                suites: Sequence[str] = ("elder_safety", "risk_response"),
                *, height: int = 310) -> Optional[alt.Chart]:
    """图二：回复长度分箱下的通过率，柱上标 n。

    两卷共用同一组横轴标签（`all_bin_labels`），缺箱补 0 高度，否则两卷横轴
    长度不同、无法比较偏置方向。n 标在柱上是因为短回复箱常常只有个位数样本
    （本仓库出现过 n=1、n=3），不标 n 会把它们与 n=350 的箱读成同等可信。
    """
    df = length_bins_data(summary, suites)
    if df.empty or df["rate"].isna().all():
        return None
    labels = [b for b in all_bin_labels() if b in set(df["bin"])]
    base = alt.Chart(df).encode(
        x=alt.X("bin:N", sort=labels, title="回复长度（字符）",
                axis=alt.Axis(labelAngle=0)),
        y=alt.Y("rate_plot:Q", title="通过率（百分制）",
                scale=alt.Scale(domain=[0, 108])),
        xOffset=alt.XOffset("metric:N"),
        color=alt.Color("metric:N", title=None,
                        scale=alt.Scale(range=list(CATEGORICAL))),
        tooltip=["metric_full:N", "bin:N", "rate:Q", "k:Q", "n:Q"],
    )
    return (base.mark_bar()
            + base.mark_text(dy=-7, fontSize=10, color=INK).encode(text="ns:N")
            ).properties(height=height, width=690)


# ---------------------------------------------------------------- 图三：子标签召回
def sublabel_recall_data(summary: dict[str, Any]) -> pd.DataFrame:
    """`risk_crisis` 各子标签的召回率与样本量。

    `by_sublabel` 自身带 k（`metrics.py:210` 落盘为 `hit`）。仍经 `derive_k`
    反推一次并要求与落盘值相等才用它算区间：反推用的是 `recall` 与 `n_pos`，
    与记录里的 k 是两个来源，两者一致才说明该字段确实是 k/n 形式。
    不一致时宁可不给区间，也不给一个来源不明的区间。
    """
    rows = []
    sub = (summary.get("per_suite") or {}).get("risk_crisis", {}).get("by_sublabel") or {}
    for label, rec in sub.items():
        recall, n_pos = rec.get("recall"), rec.get("n_pos")
        derived = mm.derive_k(recall, n_pos)
        stored = rec.get("k")
        k = stored if (derived is not None and stored == derived) else None
        ci = reporting.wilson_ci(k, n_pos) if (k is not None and n_pos) else None
        rows.append({
            "sublabel": label,
            "recall": recall,
            "miss_rate": rec.get("miss_rate"),
            "n_pos": n_pos,
            "k": k,
            "lo": None if ci is None else ci[0] * 100.0,
            "hi": None if ci is None else ci[1] * 100.0,
        })
    df = pd.DataFrame(rows, columns=["sublabel", "recall", "miss_rate", "n_pos",
                                     "k", "lo", "hi"])
    if df.empty:
        return df
    return df.sort_values("n_pos", ascending=False).reset_index(drop=True)


def sublabel_recall(summary: dict[str, Any], *, height: int = 290) -> Optional[alt.Chart]:
    """图三：`risk_crisis` 各子标签的召回率，标正例数。

    子类漏报差异可达数倍，卷级单值会把它掩盖（《评测工具链设计》§5.5）。
    误差棒由反推的 k 计算，且要求与落盘的 k 一致，否则不画。
    """
    df = sublabel_recall_data(summary)
    if df.empty or df["recall"].isna().all():
        return None
    df = df.assign(label_txt=[f"{r:.2f}\nn={int(n)}"
                              for r, n in zip(df["recall"], df["n_pos"])])
    base = alt.Chart(df).encode(
        x=alt.X("sublabel:N", sort=list(df["sublabel"]), title=None,
                axis=alt.Axis(labelAngle=0)),
        y=alt.Y("recall:Q", title="召回率（百分制）",
                scale=alt.Scale(domain=[0, 110])),
        tooltip=["sublabel:N", "recall:Q", "miss_rate:Q", "n_pos:Q", "k:Q"],
    )
    return (base.mark_bar(color=BRAND, size=56)
            + base.mark_text(dy=-9, fontSize=11, color=INK).encode(text="label_txt:N")
            + base.transform_filter("isValid(datum.lo)").mark_rule(
                strokeWidth=1.6, color=FAIL_STROKE).encode(y="lo:Q", y2="hi:Q")
            ).properties(height=height, width=460)


# ---------------------------------------------------------------- 应用层辅助
def per_subject(summaries: dict[str, dict[str, Any]],
                fn: Callable[[dict[str, Any]], Optional[alt.Chart]],
                ) -> dict[str, Optional[alt.Chart]]:
    """逐 subject 出图。图二、图三这类是单份汇总的函数。"""
    return {s: fn(v) for s, v in summaries.items()}


def has_data(summaries: dict[str, dict[str, Any]],
             fn: Callable[[dict[str, Any]], Optional[alt.Chart]]) -> list[str]:
    """能出图的 subject。供界面区分「无数据」与「出图失败」。"""
    return [s for s, v in summaries.items() if fn(v) is not None]

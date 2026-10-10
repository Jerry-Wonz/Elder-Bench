"""Streamlit 评测控制台入口。设计 §3.1 的九个页面，导航在侧栏。

启动方式（服务器，设计 §7.1）：

    streamlit run ui/app.py --server.address 192.168.13.230 --server.port 8501

## 无口令，靠网络边界而非鉴权

2026-09-22 起控制台**不再有口令门**（原 `password_gate()` 已删除）。服务仍然只绑
内网网卡 `192.168.13.230`，防火墙只对需要的来源 IP 开放 8501，**不得暴露公网**。
这一条现在是唯一的安全边界，因此不能放松：同网段可达的主机都能起作业、读产物。
设计 §7.1 的原始条款要求"口令必选"，本次变更是按使用方要求做的取舍，风险与残余
约束记录在 `docs/评测WebUI设计.md` §7.1 与 `docs/评测控制台使用手册.md` §2.1。

## 本模块只做三件事

1. 组装界面，把用户的选择翻译成 `cli.Command`。
2. 展示 `artifacts` 读出的产物与 `metricmap` / `charts` 的解读。
3. 把有副作用的动作交给 `jobs`（长作业）或 `cli.run_sync`（短命令）。

**不在这里做的事**：算指标、拼 markdown、解析 CLI 的 stdout、写 `eval/` 下除
`jobs/` 之外的任何路径。前三条是设计 §4.1「控制台不复制 CLI 逻辑」与 §4.6
「不解析生成的 Markdown」，第四条是 §7.3 的写白名单。

## 两条贯穿全页的纪律

**密钥永不回显（设计 §7.2）。** 界面只能回答"有没有"（`api_key_set`、
`env_broken`），没有任何路径能把 `EVAL_SUBJECT_API_KEY` 的值取出来显示。
子进程通过**继承环境变量**拿到密钥，因此 argv 里不含密钥，`job.json` 与
`stdout.log` 里也就不会有。

**正在写入的文件不进缓存（设计 §6）。** `st.cache_data` 只包住已结束的产物
（报告 JSON、run manifest）。`results/*.jsonl` 在作业运行期间持续增长，缓存它
会让进度条永久停在第一次读取的行数。

## 实测开销（仓库位于 SMB 盘，全部为冷缓存的首次读取）

| 阶段 | 耗时 | 说明 |
|---|---|---|
| 首次整页渲染 | 约 20 s | 主要是五项本地体检：读 10 个任务文件（14 MB）+ 50 个结果文件 |
| 后续整页重跑 | 约 1.0 s | 报告索引、配置、git 状态走 TTL 缓存；体检走 `st.cache_data` |
| 点「重新体检」 | 约 5.6 s | 只让体检层失效，不动 artifacts 的文件级缓存 |
| 点「清空缓存并刷新」 | 约 14 s | 全部缓存清空，等同冷启动 |

Streamlit 每次交互都从脚本首行重跑，故这些数字直接等于交互延迟。真正的优化方向
是把体检的读盘量降下来，而不是继续加缓存层；当前水平可用于人工操作频率的控制台。

## 自测

    python -X utf8 ui/selftest.py

16 项只读检查，覆盖设计 §8.3 验收表里可机器判定的部分（数值一致性、命令行等价、
写边界、密钥不泄露等），并把本目录对 CLI 的几处有意重复钉在断言上。改动本模块
或 `eval/reporting.py` 之后应先跑它。
"""
from __future__ import annotations

import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

import altair as alt
import pandas as pd
import streamlit as st

# harness 是全仓库唯一向 sys.path 注入 eval/ 的模块（位置 sys.path[0]），
# 必须先 import 它，后面的 metrics / reporting 才可解析。
import harness  # noqa: F401  (副作用导入：注入 eval/)
from harness import metrics, reporting

import artifacts
import charts
import cli
import config
import docsview
import health
import jobs
import metricmap as mm
import palette as pal
import theme
from paths import EVAL_DIR

POLL_SECONDS = "2s"  # 设计 §6

# Streamlit 1.49 起 `use_container_width` 被 `width="stretch"` 取代。设计 §8.1
# 只要求 >=1.37，故这里按版本择一，避免在老版本上报错、在新版本上刷弃用警告。
_ST_VER = tuple(int(x) for x in re.findall(r"\d+", st.__version__)[:2]) or (0, 0)
_STRETCH = (1, 49) <= _ST_VER


def _chart(c: Any) -> None:
    if _STRETCH:
        st.altair_chart(c, width="stretch")
    else:
        st.altair_chart(c, use_container_width=True)


def _df(data: Any, **kw: Any) -> None:
    """表格。外框样式（圆角、边框）由 theme 的 `stDataFrame` 段负责。

    这里不给 `column_config`：列宽自动伸展是可用的，而每张表的列类型各不同，
    统一塞一个 `Column` 配置只会把数值列的排序、日期列的对齐一并改掉。
    表格内容的字号由 Streamlit 自身控制（Canvas 渲染，CSS 到不了），保持默认。
    """
    _w = {"width": "stretch"} if _STRETCH else {"use_container_width": True}
    _w.update(kw)
    st.dataframe(data, **_w)


def _fragment(run_every: Optional[str] = None):
    """`st.fragment` 在 1.37 名为 `experimental_fragment`，此处抹平。"""
    fn = getattr(st, "fragment", None) or st.experimental_fragment
    return fn(run_every=run_every) if run_every else fn


def _rerun_app() -> None:
    try:
        st.rerun(scope="app")
    except TypeError:  # 老版本没有 scope 参数
        st.rerun()


# ---------------------------------------------------------------- 状态色映射
# 控制台有三套状态字符串，来源不同、字面量不同，但都只有「成功 / 待处理 /
# 失败 / 中性」四种语义角色。映射集中在这里，界面各处只引角色名，不引色值，
# 也不把三套字符串互相换算（那会把「作业 finished」说成「体检通过」）。

# 体检四档的图标。图标只作粗扫定位，结论文字在标题里，故不用彩色 emoji 表示
# 成功/失败——DESIGN.md 的三色已经承担了这件事。
_HEALTH_ICON = {health.OK: "✅", health.WARN: "⚠️", health.FAIL: "❌", health.SKIP: "➖"}
# SKIP（不适用）归 mute：它既不是失败也不是成功，借 warn 的琥珀色会把
# 「这条检查不适用」读成「有待处理」。
HEALTH_TONE = {health.OK: "ok", health.WARN: "warn",
               health.FAIL: "bad", health.SKIP: "mute"}
# LOST（记录在、进程不在）归 warn：它既不是正常完成，也不是被判定的失败，
# 界面必须与 failed 区分开。
JOB_TONE = {jobs.RUNNING: "info", jobs.FINISHED: "ok", jobs.FAILED: "bad",
            jobs.STOPPED: "warn", jobs.LOST: "warn"}
# 门禁三档不需要单独一张表：报告页用 st.error / st.warning 呈现结论，语义色由
# theme.py 的 alert 规则给出（见 _CARD_CSS 的 :has() 段），再映射一次是重复。


def _tags_html(items: list[tuple[str, str]]) -> str:
    """一行内联小标签。`items` 是 `(语义角色, 标签)` 二元组。

    用于替代「未通过 3 · 注意 2 · 通过 8 · 不适用 1」这类纯文本统计：
    同样的信息加底色后，扫一眼即可定位是哪一档异常，不必逐个数字对齐着读。
    标签文字由调用方拼好（计数、状态符号、退出码都算在标签里），这里不接收
    「值」——收进来也只能拼进 `label`，多一个参数只会让调用点写得更长。
    """
    parts = []
    for role, label in items:
        fg, bg = pal.tone(role)
        # 底色不透明而非半透明：同一排标签可能落在白卡、灰页面或浅色提示条上，
        # 半透明底会让「色淡 = 值小」这种误读从底色深浅里冒出来。
        parts.append(f'<span class="eb-tag" style="color:{fg};background:{bg}">'
                     f'{label}</span>')
    return ('<div style="display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 12px">'
            + "".join(parts) + "</div>")


def _tags(items: list[tuple[str, str]]) -> None:
    st.markdown(_tags_html(items), unsafe_allow_html=True)


# ================================================================ 共享取数
# 仓库放在 SMB 盘上，`eval/reports/` 与 `eval/results/` 的目录扫描每次要几百毫秒
# （实测 refs_by_subject 581ms、subject_views 310ms），而 Streamlit 每次交互都会
# 从脚本首行重跑。下列 TTL 缓存只包住**已结束的产物**与其索引，不碰正在写入的
# `results/*.jsonl`（设计 §6）。TTL 取小值是为了让新生成的文件在几秒内出现，
# 侧栏的「清空缓存并刷新」按钮可以立即强制一次。
_TTL = 8.0


@st.cache_data(show_spinner=False)
def _read_summary(path_str: str, mtime: float) -> dict[str, Any]:
    """缓存已结束的报告 JSON。mtime 进 key，报告被重算后缓存自动失效。"""
    return artifacts.load_summary(path_str)


def _summary_of(ref: artifacts.ReportRef) -> dict[str, Any]:
    return _read_summary(str(ref.path), ref.mtime)


@st.cache_data(ttl=_TTL, show_spinner=False)
def _report_index() -> tuple[dict[str, list[artifacts.ReportRef]],
                             dict[str, artifacts.ReportRef]]:
    """报告目录的索引。报告文件一旦写出就不改，故可缓存；TTL 只负责让新报告露面。"""
    return artifacts.refs_by_subject(), artifacts.latest_by_subject()


def _refs_by_subject() -> dict[str, list[artifacts.ReportRef]]:
    return _report_index()[0]


def _latest() -> dict[str, artifacts.ReportRef]:
    return _report_index()[1]


@st.cache_data(ttl=_TTL, show_spinner=False)
def _all_refs() -> list[artifacts.ReportRef]:
    return artifacts.report_refs()


@st.cache_data(ttl=_TTL, show_spinner=False)
def _run_manifests() -> list[dict[str, Any]]:
    return artifacts.run_manifests()


@st.cache_data(ttl=_TTL, show_spinner=False)
def _git_state() -> tuple[Optional[str], Optional[bool]]:
    """git 状态会 spawn 一个子进程，在 SMB 盘上尤其慢，故按 TTL 缓存。"""
    return jobs.git_state()


@st.cache_data(ttl=_TTL, show_spinner=False)
def _config_hashes() -> dict[str, Optional[str]]:
    return {p.name: jobs.sha256_file(p) for p in (
        config.CONFIG_PATH, config.CONFIG_DEPLOY, config.CONFIG_HISTORY)}


@st.cache_data(ttl=_TTL, show_spinner=False)
def _views() -> list[config.SubjectView]:
    return config.subject_views()


@st.cache_data(ttl=_TTL, show_spinner=False)
def _subject_names() -> list[str]:
    return config.subject_names()


@st.cache_data(ttl=_TTL, show_spinner=False)
def _groups() -> dict[str, dict[str, Any]]:
    return config.subject_groups()


@st.cache_data(ttl=_TTL, show_spinner=False)
def _sampling() -> dict[str, Any]:
    return config.sampling()


@st.cache_data(ttl=_TTL, show_spinner=False)
def _redline() -> dict[str, Any]:
    return config.redline()


@st.cache_data(ttl=_TTL, show_spinner=False)
def _concurrency() -> int:
    return config.concurrency() or 16


def _summaries(refs: dict[str, artifacts.ReportRef]) -> dict[str, dict[str, Any]]:
    return {s: _summary_of(r) for s, r in refs.items()}


@st.cache_data(show_spinner=False)
def _local_checks(token: int) -> list[health.Check]:
    """体检结果。`token` 是人为的失效开关，点「重新体检」时自增。

    参数名**不能**以下划线开头：Streamlit 的 `cache_data` 约定下划线前缀的参数
    不参与哈希，那样自增 token 不会让缓存失效，「重新体检」按钮就会变成空操作。
    """
    return health.run_local(use_cache=False)


def _lock_note(lock: dict[str, Any]) -> str:
    """锁持有者的可读描述。

    `current_lock_job()` 找不到对应 job.json 时会退回落盘锁里的 owner 摘要，
    那个 dict 只有 `job_id` 与 `pid`，没有 `status`。此时如实说"无对应作业记录"，
    不要把它渲染成 `None`。
    """
    jid = lock.get("job_id") or "未知"
    status = lock.get("status")
    return f"`{jid}`（{status}）" if status else f"`{jid}`（无对应作业记录）"


def snapshot_banner() -> None:
    """设计 §4.7：跑批进行中时，离线作业读到的数据快照可能不完整。

    只在页首提示一次，不在每个页签里重复。它回答的是两个问题：现在有没有作业
    在跑，以及此间的离线产物覆盖范围到哪。
    """
    if not jobs.lock_active():
        return
    owner = jobs.current_lock_job() or {}
    st.warning(
        f"跑批进行中：`{owner.get('job_id', '未知作业')}` 正在写入结果文件。"
        "数据快照不完整，此后生成的报告、对比与坏例材料只覆盖已落盘的部分。",
        icon="⚠️",
    )


# ================================================================ 命令面板
def launch(start_fn: Any) -> None:
    """执行一个起作业的调用，把三类可预期的失败翻译成界面提示。

    `jobs.start` 会抛三种异常，都必须接住，否则用户点一下按钮就是整页堆栈：
    `ValueError` 命令不可执行（如卷任务文件缺失）、`LockError` 已有 run 作业持有锁、
    `RuntimeError` Popen 起不来。
    """
    try:
        rec = start_fn()
    except jobs.LockError as e:
        st.error(f"无法占用 run 锁：{e}")
    except ValueError as e:
        st.error(f"命令不可执行：{e}")
    except RuntimeError as e:
        st.error(str(e))
    else:
        st.success(f"已启动作业 `{rec['job_id']}`，进度与输出见「作业」页。")


def command_panel(
    cmd: cli.Command,
    *,
    key: str,
    mode: str = "sync",
    subject: str = "",
    kind: str = "",
    note: str = "",
    timeout: float = 300.0,
    label: str = "执行",
) -> None:
    """把一条 `cli.Command` 变成"显示等价命令行 + 一个执行按钮"。

    设计 §5.3：**显示的命令行与真正执行的 argv 同源**（都来自这一个 cmd 对象）。
    因此这里显示的字符串就是复制粘贴到 shell 里能得到同样结果的那一条。

    mode：
      sync   短命令，同步执行并就地显示输出（report / verify / compare / ...）
      job    长命令，起为分离作业（build / run / batch / rescore），进度去「作业」页看
    """
    st.code(cmd.display, language="bash")
    if not cmd.runnable:
        st.error(f"当前参数下命令不可执行：{cmd.explain()}")
        return

    if mode == "job":
        if st.button(label, key=f"{key}_go", type="primary"):
            launch(lambda: jobs.start_offline(cmd, subject=subject or cmd.kind,
                                              kind=kind or cmd.kind, note=note))
        return

    if st.button(label, key=f"{key}_go"):
        with st.spinner(f"执行中（上限 {timeout:.0f}s）…"):
            res = cli.run_sync(cmd, timeout=timeout)
        (st.success if res.ok else st.error)(
            f"退出码 {res.returncode}" + ("" if res.ok else "（非 0）"))
        st.code(res.text or "(无输出)", language="text")


@st.cache_data(show_spinner=False)
def _doc_main_table(mtime: float) -> tuple[str, list[list[str]]]:
    """主表原文与只读单元格投影。mtime 进 key，文档一改自动失效。"""
    return docsview.main_table(), docsview.main_table_rows()


def _main_table_payload() -> tuple[str, list[list[str]]]:
    try:
        return _doc_main_table(docsview.mtime())
    except OSError as e:
        st.warning(f"读不到 `docs/三维度评测模型对比.md`：{e}")
        return "", []


def _clean_cell(raw: str) -> str:
    return re.sub(r"[*`]", "", raw).strip()


def _dimension_score_data(rows: list[list[str]]) -> tuple[list[str], list[dict[str, Any]]]:
    """从主表的「维度分（汇总）」行提取可视化数据。"""
    if len(rows) < 2:
        return [], []
    headers = rows[0]
    models = [_clean_cell(h) for h in headers[3:]]
    data: list[dict[str, Any]] = []
    for row in rows[1:]:
        if len(row) < 4 or _clean_cell(row[0]) == "" or _clean_cell(row[1]) != "维度分（汇总）":
            continue
        dim = _clean_cell(row[0])
        for model, raw in zip(models, row[3:]):
            match = re.search(r"-?\d+(?:\.\d+)?", _clean_cell(raw))
            if match is None:
                continue
            data.append({"dimension": dim, "model": model, "score": float(match.group())})
    return models, data


def _dimension_score_chart(rows: list[list[str]]) -> Optional[alt.Chart]:
    """维度分的分组柱状图。色板用 theme 的分类色，与热力图、界面同一套。

    颜色在这里只是「哪根柱属于哪个模型」的分类标记，不编码高低，故不能借
    theme 的语义状态色：那三个色是给标签的离散结论色，用在分组柱上会把
    「模型 A 是绿色」读成「模型 A 通过」。
    """
    models, data = _dimension_score_data(rows)
    if not data:
        return None
    df = pd.DataFrame(data)
    return (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X("dimension:N", sort=list(dict.fromkeys(df["dimension"])), title=None,
                    axis=alt.Axis(labelAngle=0, labelFontSize=13,
                                  labelColor=theme.MUTED, titleColor=theme.MUTED,
                                  titleFontSize=14)),
            xOffset=alt.XOffset("model:N", sort=models),
            y=alt.Y("score:Q", title="归一化分", scale=alt.Scale(domain=[0, 100]),
                    axis=alt.Axis(labelFontSize=13, titleFontSize=14,
                                  labelColor=theme.MUTED, titleColor=theme.MUTED)),
            color=alt.Color("model:N", title=None,
                            scale=alt.Scale(range=list(theme.CATEGORICAL)),
                            legend=alt.Legend(orient="top", direction="horizontal",
                                              columns=2, labelLimit=280,
                                              labelFontSize=13, titleFontSize=13,
                                              labelColor=theme.MUTED)),
            tooltip=["model:N", "dimension:N", alt.Tooltip("score:Q", format=".1f")],
        )
        .properties(height=360, width="container")
        .configure(background=theme.CARD)
        .configure_view(stroke=None)
        .configure_axis(
            gridColor=theme.LINE,
            domainColor=theme.LINE,
            tickColor=theme.LINE,
            labelColor=theme.MUTED,
            titleColor=theme.MUTED,
        )
        .configure_legend(labelColor=theme.MUTED, titleColor=theme.MUTED)
    )


# 概括卡上的图标。内联 SVG 而非图标字体或 emoji：emoji 的字形随系统字体变，
# 在 Inter/PingFang 之外会掉成两种风格；图标字体要多一个网络请求，而本机是
# 内网部署。`stroke="currentColor"` 让颜色由 `.eb-summary-icon.<色名>` 决定，
# 所以图标本身不写色值，四档底色仍归 theme.py 一处管。
_ICON = {
    "layers": '<path d="M12 3 2 8l10 5 10-5-10-5Z"/><path d="M2 16l10 5 10-5"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>'
            '<rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
}


def _icon(name: str) -> str:
    return (f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            f'{_ICON[name]}</svg>')


def _summary_card(label: str, value: str, note: str, *, icon: str, tone: str) -> str:
    """一张概括卡。三段固定是「标题 16/600 → 核心数据 32/700 → 辅助 12/400」。

    `tone` 只作用于图标底色，不作用于数值：数值是同一批口径下的计数，
    没有好坏含义（「评测维度 3」不是三档里的哪一档）。图标四色取自
    DESIGN.md 的语义三色加品牌色，作用是让四张卡在扫读时先分得开。
    """
    return (f'<div class="eb-summary-card">'
            f'<div class="eb-summary-label"><span class="eb-summary-icon {tone}">{_icon(icon)}</span>{label}</div>'
            f'<div class="eb-summary-value">{value}</div>'
            f'<div class="eb-summary-note">{note}</div>'
            f'</div>')


def _overview_summary(rows: list[list[str]]) -> None:
    headers = rows[0] if rows else []
    n_models = max(0, len(headers) - 3)
    n_dims = sum(1 for r in rows[1:] if r and _clean_cell(r[0]))
    n_calibers = max(0, len(rows) - 1)
    updated = time.strftime("%Y-%m-%d", time.localtime(docsview.mtime()))
    cards = "".join([
        _summary_card("对比对象", str(n_models), "主表中的模型 / 权重列",
                      icon="layers", tone="violet"),
        _summary_card("评测维度", str(n_dims), "医疗 · 心理 · 养老专用",
                      icon="grid", tone="mint"),
        _summary_card("口径行", str(n_calibers), "直接指标、红线与归一化分",
                      icon="list", tone="amber"),
        _summary_card("主表更新", updated[5:], "来源文档最后修改时间",
                      icon="clock", tone="rose"),
    ])
    st.markdown(f'<div class="eb-summary-grid">{cards}</div>', unsafe_allow_html=True)


# ================================================================ 1 总览
def tab_overview() -> None:
    main_md, rows = _main_table_payload()
    if not main_md or not rows:
        st.info("`docs/三维度评测模型对比.md` 中还没有可展示的 §3 主表。")
        return

    _overview_summary(rows)

    with st.container(border=True):
        st.markdown("### 分维度多口径对比")
        st.caption(
            "主表原样取自 `docs/三维度评测模型对比.md` §3，覆盖全部活动 subject，不重算、不改写；"
            "负向率越低越好，加粗为原文标记的最优值。"
        )
        # §3 主表在「分难度」「main / hard」等多值格子里用 `<br />` 换行，文档的
        # HTML 版也是按换行渲染的。`st.markdown` 默认把内联 HTML 转义成字面量，
        # 页面上就会显示成 `/ <br />`；放开 HTML 才能按原文语义换行。渲染的是
        # 仓库内的只读文档，不涉及用户输入。
        st.markdown(main_md, unsafe_allow_html=True)

    fig = _dimension_score_chart(rows)
    if fig is not None:
        with st.container(border=True):
            st.markdown("### 维度分速览")
            st.caption(
                "从主表「维度分（汇总）」行提取，仅用于快速比较；"
                "表格中的原始数字仍是唯一结论来源。"
            )
            _chart(fig)

    st.caption(
        f"来源：`docs/三维度评测模型对比.md`（最后修改 "
        f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(docsview.mtime()))}）。"
        "文档更新后本页随之更新；若与 `eval/results/` 下的最新报告不一致，以报告为准。"
    )


# ================================================================ 2 配置
def tab_config() -> None:
    views = _views()

    st.subheader("subject 一览")
    rows = []
    for v in views:
        rows.append({
            "subject": v.name,
            "组": v.group,
            "标签": "、".join(v.tags),
            "模型": v.model,
            "端点": v.base_url or "（未解析）" + (" ⚠" if v.env_broken else ""),
            "temperature": v.temperature,
            "max_tokens": v.max_tokens,
            "人格文件": v.system_prompt_file or "（无）",
            "API key": "已设置" if v.api_key_set else "未设置",
        })
    _df(rows, hide_index=True)
    st.caption(
        "「API key」只表示该字段解析后非空。界面上没有任何入口可以显示其取值"
        "（设计 §7.2）；密钥通过环境变量传给子进程，因此也不会出现在 argv、"
        "`job.json` 或 `stdout.log` 里。"
    )

    st.subheader("分组与可比性")
    for g, info in _groups().items():
        d = info.get("decoding") or {}
        cond = info.get("condition") or {}
        ok = bool(info.get("comparable"))
        prompt_hash = (cond.get("system_prompt_sha256") or "none")[:12]
        (st.success if ok else st.warning)(
            f"`{g}`：{'、'.join(info['subjects'])}；"
            f"temperature={d.get('temperature')}, max_tokens={d.get('max_tokens')}, "
            f"top_p={d.get('top_p')}, seed={d.get('seed')}, "
            f"prompt={cond.get('system_prompt_file') or 'none'}({prompt_hash})；"
            + ("组内完整可比条件一致，可直接比较。" if ok
               else "组内完整可比条件不一致，**不可直接比较**，结论须标注口径差异。")
        )

    st.subheader("红线阈值")
    rl = _redline()
    th_rows = [{
        "阈值键": k, "当前值": v,
        "含义": mm.REDLINE_LABELS.get(k.replace("_max", ""), ""),
        "来源": mm.THRESHOLD_SOURCE.get(k, ""),
    } for k, v in (rl.get("thresholds") or rl).items() if isinstance(v, (int, float))]
    if th_rows:
        _df(th_rows, hide_index=True)
    st.caption("此处展示的是当前活动配置 profile 的值。报告里的门禁判定用的是**报告生成时内嵌**的阈值，两者可能不同。")

    with st.expander("采样配额"):
        _df([{"卷": k, "配额": str(v)} for k, v in (_sampling().get("suites") or {}).items()],
            hide_index=True)

    st.divider()
    st.subheader("配置体检")
    st.caption(
        "回答「现在点下 run，会在哪里失败」。五项本地检查在页面加载时执行；"
        "端点探针是对外请求，必须显式触发。"
    )
    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("重新体检", key="cfg_recheck"):
            # 只让**体检结果**这一层失效，不动 artifacts 的文件级缓存。
            # `health.clear_cache()` 会连带清掉 artifacts 的 lru 缓存，那样每点一次
            # 都要把 10 个任务文件加 50 个结果文件重读一遍（实测 8-10s）。而
            # artifacts 的缓存键是 (路径, mtime, 大小)，文件一变自然 miss，
            # 不需要外力清空。
            st.session_state["_health_token"] = st.session_state.get("_health_token", 0) + 1
            _rerun_app()
    token = int(st.session_state.get("_health_token", 0))
    checks = _local_checks(token)

    ov = health.overview(checks)
    # 计数顺序固定为「未通过 → 注意 → 通过 → 不适用」，异常的排在前面。顺序
    # 写死而不是按计数排序：位置固定才可以在两次刷新之间直接比数字。标签用
    # 中文短名而非状态键，`fail 3` 读起来像变量名，`未通过 3` 才是给人看的。
    _tags([(HEALTH_TONE[k], f"{health.STATUS_SYMBOL[k]} {ov[k]}")
           for k in (health.FAIL, health.WARN, health.OK, health.SKIP)])
    for c in checks:
        icon = _HEALTH_ICON[c.status]
        with st.expander(f"{icon} {c.title} · {c.symbol}", expanded=(c.status == health.FAIL)):
            st.write(c.detail)
            if c.remedy:
                st.caption(f"处置：{c.remedy}")
            if c.rows:
                _df(c.rows, hide_index=True)

    st.markdown("**端点探针（对外请求，需显式触发）**")
    st.caption(
        "只对该 subject 所在配置 profile 声明的 `base_url` 发起 `GET /models`，"
        "不接受任意 URL。凭据只进请求头，不回显、不落盘。"
    )
    probe_target = st.selectbox("探测对象", [v.name for v in views], key="cfg_probe_target")
    if st.button("发起探测", key="cfg_probe"):
        with st.spinner("探测中…"):
            r = health.probe_endpoint(probe_target)
        (st.success if r.status == health.OK else st.warning)(f"{r.symbol}：{r.detail}")
        if r.remedy:
            st.caption(r.remedy)
    if st.button("探测本机端点", key="cfg_probe_local"):
        with st.spinner("连接中…"):
            r = health.check_local_endpoint()
        (st.success if r.status == health.OK else st.warning)(f"{r.symbol}：{r.detail}")
        if r.rows:
            _df(r.rows, hide_index=True)


# ================================================================ 3 跑批
def tab_run() -> None:
    subjects = _subject_names()
    if not subjects:
        st.error("活动配置 profile 里没有 subject，无法跑批。先声明能力组或部署态 subject 再回来。")
        return

    runnable = cli.available_suites()
    all_suites = health.expected_suites()

    if cli.harness_python_is_fallback():
        st.warning(
            f"未找到仓库内 `.venv`，将用当前解释器 `{cli.harness_python()}` 执行子命令。"
            "本机 Python 通常没有 jieba / nltk / pandas，真实跑批会失败；"
            "服务器上请按 `docs/环境说明.md` 建好 venv。"
        )

    if jobs.lock_active():
        st.info(f"已有 run 类作业持有锁：{_lock_note(jobs.current_lock_job() or {})}。"
                "同一时刻只允许一个跑批作业。")

    mode = st.radio("作业类型", ["全量批次（10 卷）", "单卷试跑"], horizontal=True, key="run_mode")

    if mode.startswith("全量"):
        subject = st.selectbox("subject", subjects, key="run_batch_subject")
        conc = st.number_input("并发", min_value=1, max_value=64,
                               value=int(_concurrency()), key="run_batch_conc")
        cmd = cli.cmd_batch(subject, concurrency=int(conc))
        st.caption(
            "复用 `scripts/run_suite_all.sh`（设计 §4.4），卷顺序由脚本固定为 "
            "risk_crisis → elder_safety → risk_response → elder_refusal → health_calc "
            "→ health_hallu → health_ref → psy_support → psy_empathy → psy_calibration。"
            "断点续跑：结果文件无时间戳，重跑会跳过已完成的有效条目。"
        )
        command_panel(cmd, key="run_batch", mode="job", kind="run_batch",
                      subject=subject, note=f"全量 10 卷，并发 {int(conc)}",
                      label="启动全量批次")
    else:
        c1, c2 = st.columns(2)
        with c1:
            subject = st.selectbox("subject", subjects, key="run_one_subject")
        with c2:
            suite = st.selectbox("卷", runnable or all_suites, key="run_one_suite")
        c3, c4 = st.columns(2)
        with c3:
            limit = st.number_input("limit（0 表示不限）", min_value=0, value=20,
                                    step=10, key="run_one_limit")
        with c4:
            conc = st.number_input("并发", min_value=1, max_value=64,
                                   value=int(_concurrency()), key="run_one_conc")
        _limit = int(limit) or None
        cmd = cli.cmd_run(suite, subject, limit=_limit, concurrency=int(conc))

        missing = [s for s in all_suites if s not in runnable]
        if missing:
            st.caption(f"任务文件缺失、需先 build 的卷：{'、'.join(missing)}。")

        if st.button("启动单卷作业", key="run_one_go", type="primary"):
            launch(lambda: jobs.start_single(suite, subject, limit=_limit,
                                             concurrency=int(conc)))
        st.code(cmd.display, language="bash")

    st.divider()
    st.subheader("数据准备")
    st.caption("build 从 `benchmark/external/` 生成 `eval/data/tasks_*.jsonl`。"
               "服务器 `.venv` 缺 pyarrow / openpyxl 时须在本机 build 后同步 `data/`。")
    with st.expander("构建与冒烟命令"):
        command_panel(cli.cmd_build(), key="run_build", mode="job", kind="build",
                      subject="(all)", note="构建全部卷任务文件", label="构建全部卷")
        command_panel(cli.cmd_smoke(), key="run_smoke", mode="sync",
                      label="执行冒烟测试", timeout=600.0)


# ================================================================ 4 报告
def tab_reports() -> None:
    by_subject = _refs_by_subject()
    if not by_subject:
        st.info("`eval/results/` 下没有报告产物。")
        return

    subject = st.selectbox("subject", sorted(by_subject), key="rep_subject")
    refs = by_subject[subject]
    choice = st.selectbox(
        "报告版本", refs, key="rep_ref",
        format_func=lambda r: (f"{r.label} · 生成于 {r.generated_at or '—'} · "
                               f"口径 {r.scorer_version or '—'} · n={r.n_results} · "
                               f"门禁 {r.gate or '—'}"),
    )
    summary = _summary_of(choice)

    gate = mm.report_gate(summary)
    sec = mm.secondary_gate(summary)
    c1, c2, c3, c4 = st.columns(4)
    # `gate or "—"` 保留原行为：报告没有记录门禁时显示破折号，不猜一个结论。
    # 副标题给出该结论的释义，色标关掉——门禁的语义色已由下方提示条承担，
    # metric 自带的涨跌色会把「FAIL」读成「比上次差」。
    c1.metric("总体门禁", gate or "—",
              delta={mm.GATE_PASS: "红线未越阈",
                     mm.GATE_INSUFFICIENT: "样本不足，不判 PASS/FAIL",
                     mm.GATE_FAIL: "主红线越阈"}.get(gate),
              delta_color="off")
    c2.metric("次级（回应双缺失）", sec or "—")
    c3.metric("判分口径", choice.scorer_version or "—")
    c4.metric("有效条数", choice.n_results)
    if gate == mm.GATE_FAIL:
        hit = [i.short for i in mm.PRIMARY_REDLINES
               if mm.breached(summary, i)]
        st.error("总体门禁 FAIL，越阈的主红线：" + ("、".join(hit) if hit else "（报告未记录具体项）"))
    elif gate == mm.GATE_INSUFFICIENT:
        st.warning("总体门禁 INSUFFICIENT：红线样本或正例覆盖不足，不能据此判 PASS/FAIL。")
    st.caption(f"源文件 `{choice.path.name}`（{choice.path}）")

    st.divider()
    st.subheader("分层观察")

    # 单份汇总直接判 `is None` 即可。`charts.has_data` 是给多 subject 场景用的，
    # 它会为了回答"有没有数据"把图构建一遍，在这里用等于白建一次。
    fig = charts.length_bins(summary)
    if fig is None:
        st.info("该报告没有 `by_length_bin`，生成于口径升级之前，无法给出长度分箱。")
    else:
        _chart(fig)
        st.caption("按回复长度分箱的双通过率 / 非双缺失率。长度由回复字符数决定。")

    fig = charts.sublabel_recall(summary)
    if fig is None:
        st.info("该报告没有 `by_sublabel`，生成于口径升级之前，无法给出子标签召回。")
    else:
        _chart(fig)
        st.caption("PsyCrisis 子标签维度的召回与漏报。小样本子标签的区间会明显变宽。")

    st.divider()
    st.subheader("完整报告")
    st.caption(
        "以下内容由 `eval/reporting.py` 的纯函数在界面进程内渲染（设计 §4.6），"
        "不是读取已生成的 Markdown 文件，因此与 CLI 输出同源、不会因文件缺失而空白。"
    )
    md = reporting.render_markdown(summary, choice.generated_at or "")
    st.markdown(md)

    c1, c2 = st.columns(2)
    with c1:
        st.download_button("下载本页 Markdown", md, file_name=f"{choice.path.stem}.md",
                           mime="text/markdown", key="rep_dl_md")
    with c2:
        if choice.md_path.exists():
            st.download_button("下载已落盘的 Markdown", choice.md_path.read_text(encoding="utf-8"),
                               file_name=choice.md_path.name, mime="text/markdown",
                               key="rep_dl_disk")


# ================================================================ 5 对比
def tab_compare() -> None:
    refs = _latest()
    if not refs:
        st.info("还没有可对比的报告产物。")
        return

    picked = st.multiselect("参与对比的 subject", sorted(refs), default=sorted(refs),
                            key="cmp_subjects")
    if not picked:
        st.info("至少选择一个 subject。")
        return

    summaries = {s: _summary_of(refs[s]) for s in picked}

    st.subheader("指标矩阵（subject × 卷）")
    st.caption(
        "逐列着色：每一列按该列自身的最大最小值映射颜色，因此**跨列的颜色不可直接比较**。"
        "n < 300 的格子置灰；门禁越阈用黑色实线边框与角标标注，次级红线用虚线边框，"
        "缺失值画空白加斜线（设计 §4.8）。"
    )
    fig = charts.heatmap(summaries)
    if fig is None:
        st.info("所选报告在这些卷上都没有取值。")
    else:
        _chart(fig)
        st.caption(
            "✗ 越阈 = 该格自身数值越过其阈值；△ 次级越阈 = 属次级红线，"
            "不参与总体门禁。报告级结论请看「总览」页。"
        )
    with st.expander("查看数值表（含 n、置信区间与标记）"):
        df = charts.heatmap_data(summaries)
        _df(df, hide_index=True)

    st.subheader("分组")
    groups = _groups()
    for g, info in groups.items():
        members = [s for s in info["subjects"] if s in summaries]
        if not members:
            continue
        ok = bool(info.get("comparable"))
        (st.success if ok else st.warning)(
            f"`{g}`：{'、'.join(members)}；"
            + ("组内解码参数一致。" if ok else "组内解码参数不一致，跨成员比较无效。")
        )
    ungrouped = [s for s in picked
                 if not any(s in info["subjects"] for info in groups.values())]
    if ungrouped:
        st.caption(f"未分组：{'、'.join(ungrouped)}。跨组比较须标注口径差异。")

    st.divider()
    st.subheader("生成对比报告")
    st.caption("调用 CLI 的 `compare`，由 `reporting.render_compare` 渲染并落盘到 `eval/reports/`。")
    run_id = st.selectbox("限定 run_id（可空）", [""] + cli.known_run_ids(), key="cmp_run")
    command_panel(cli.cmd_compare(picked, run=run_id or None), key="cmp_go",
                  mode="sync", label="生成对比报告", timeout=600.0)

    files = artifacts.compare_files()
    if files:
        st.markdown("**已生成的对比报告**")
        for p in sorted(files, reverse=True)[:20]:
            st.caption(f"`{p.relative_to(EVAL_DIR.parent).as_posix()}`")


# ================================================================ 6 坏例
def tab_badcase() -> None:
    files = artifacts.result_files()
    if not files:
        st.info("`eval/results/` 下没有结果文件。")
        return

    subjects = sorted({s for s, _ in files})
    suites = sorted({q for _, q in files})
    c1, c2 = st.columns(2)
    with c1:
        subject = st.selectbox("subject", subjects, key="bc_subject")
    with c2:
        suite = st.selectbox("卷", suites, key="bc_suite")

    path = files.get((subject, suite))
    if path is None or not path.exists():
        st.info(f"没有 {subject} / {suite} 的结果文件。")
        return

    counts = artifacts.load_counts(path)
    st.caption(f"`{path.relative_to(EVAL_DIR.parent).as_posix()}`："
               f"有效 {counts.valid}，失败重试 {counts.error}，损坏行 {counts.broken}。")

    # 判据取自 reporting.BAD_CRITERIA（与 CLI 的 export-badcase 同源），
    # 谓词作用在 metrics.per_task_scores 的输出上，与 run_eval.py:336-339 完全一致。
    # 已核对：elder_safety / qwen35_4b_deploy_v2 / risk_flag 下本页算出 332/500，
    # 与既有 CLI 材料 `badcase_elder_safety_qwen35_4b_deploy_v2_risk_flag_20260921.md`
    # 头部的「500 条中失败 332 条」逐位一致。
    crits = reporting.BAD_CRITERIA.get(suite)

    rows = _load_results_uncached(path)
    scored = [(r, metrics.per_task_scores(r)) for r in rows]

    if not crits:
        st.warning(
            f"卷 `{suite}` 未登记坏例判据。`reporting.BAD_CRITERIA` 只覆盖 "
            f"{'、'.join(sorted(reporting.BAD_CRITERIA))}，CLI 的 `export-badcase` "
            "也只支持这几卷，因此本卷不提供抽样导出。"
        )
        _per_task_table(scored)
        _existing_materials()
        return

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        crit = st.selectbox("判据", list(crits), key="bc_crit",
                            format_func=lambda k: crits[k][0])
    with c2:
        n = st.number_input("抽样条数", min_value=1, max_value=500, value=40, key="bc_n")
    with c3:
        seed = st.number_input("随机种子", min_value=0, value=20260921, step=1, key="bc_seed")

    failed = [(r, s) for r, s in scored
              if s.get("suite") == suite and crits[crit][1](s)]
    n_total = len(rows)
    st.metric("该卷失败条数", f"{len(failed)} / {n_total}", help=crits[crit][0])

    # 与 CLI 同一套抽样：random.Random(seed).sample 作用在同序的失败池上。
    # 池为空时不抽样（CLI 同样用 `if failed else []`）。
    pool = (random.Random(int(seed)).sample(failed, min(int(n), len(failed)))
            if failed else [])
    st.caption(f"固定种子 {int(seed)} 抽样 {len(pool)} 条。本项目调用的抽样逻辑与 "
               "CLI 一致（同一失败池、同一 `random.Random(seed).sample`），"
               "因此同种子下的样本集合相同。")

    for r, s in pool:
        t = r["task"]
        user = "\n".join(m["content"] for m in t["messages"] if m["role"] == "user")
        head = f"{r['task_id']} · {t.get('dimension')} · {t.get('source')}"
        with st.expander(head):
            st.caption(f"长度 {len(r.get('response') or '')} 字符 · 判分字段 "
                       f"`{ {k: v for k, v in s.items() if k not in ('suite', 'dimension', 'source', 'lang', 'eval_mode', 'task_id')} }`")
            st.markdown("**用户输入**")
            st.text(user)
            st.markdown("**模型回复**")
            st.text(r.get("response") or "(空)")

    st.divider()
    st.subheader("导出人工判读材料")
    st.caption(
        "由 CLI 生成 Markdown 判读材料并落盘到 `eval/reports/`。头部计数由 CLI 现场统计，"
        "不接受外部传入，因此不会出现材料标题与卷内实际不符。"
    )
    out = st.text_input("输出路径（留空则用 CLI 默认命名）", key="bc_out")
    command_panel(
        cli.cmd_export_badcase(subject, suite, criterion=crit, n=int(n), seed=int(seed),
                               out=out or None),
        key="bc_go", mode="sync", label="生成坏例材料", timeout=600.0)
    _existing_materials()


# 单条判分字段里这几项是上下文而非判分结论，列表里不显示。
_SCORE_CTX = ("suite", "dimension", "source", "lang", "eval_mode", "task_id")


def _per_task_table(scored: list[tuple[dict[str, Any], dict[str, Any]]],
                    *, limit: int = 200) -> None:
    """没有登记判据的卷：直接摊开单条判分字段供人工查看。

    这批字段由 `metrics.per_task_scores` 产出，是报告聚合的输入，因此表里的每一项
    都能在报告里找到对应的汇总口径。这里不做筛选、不判好坏，只把原始字段摆出来。
    """
    st.caption(
        f"列出前 {min(limit, len(scored))} / {len(scored)} 条的单条判分字段。"
        "这些字段就是报告聚合的输入，本页不对它们做任何再判断。"
    )
    _df([{"task_id": r["task_id"],
          "长度": len(r.get("response") or ""),
          "dimension": r["task"].get("dimension"),
          "source": r["task"].get("source"),
          **{k: v for k, v in s.items() if k not in _SCORE_CTX}}
         for r, s in scored[:limit]], hide_index=True)


def _existing_materials() -> None:
    existing = artifacts.badcase_files()
    if existing:
        st.markdown("**已生成的坏例材料**")
        for p in sorted(existing, reverse=True)[:20]:
            st.caption(f"`{p.relative_to(EVAL_DIR.parent).as_posix()}`")


def _load_results_uncached(path: Path) -> list[dict[str, Any]]:
    """逐行读结果文件，只保留有效条目。

    过滤条件与 `eval/runner.py:97`（`"task" in r and "error" not in r`）逐字一致。
    不复用 `runner.load_results` 是因为 `runner` 会 import `scoring`，从而把
    jieba / nltk 拖进界面进程（设计 §4.6 的依赖边界）。

    这里**直接读文件、不使用 `st.cache_data`**：结果文件在作业运行期间持续增长，
    缓存它会让坏例列表停在上一次读取时的条数（设计 §6）。
    """
    from paths import iter_jsonl
    return [r for r in iter_jsonl(path) if "task" in r and "error" not in r]


# ================================================================ 7 复现
def tab_repro() -> None:
    st.caption(
        "回答「这个结论今天还能复现吗」。manifest 记录一次 run 的模型、数据、模板、"
        "判分与解码参数及各自的 sha256；verify 重算这些哈希并列出已变化的项。"
    )

    manifests = _run_manifests()
    run_ids = [m.get("run_id") for m in manifests if m.get("run_id")] or cli.known_run_ids()
    if not run_ids:
        st.info("`eval/runs/` 下还没有 run manifest。跑一次 `run` 或 `report` 后会写入。")
    else:
        run_id = st.selectbox("run_id", run_ids, key="rep_run")
        man = next((m for m in manifests if m.get("run_id") == run_id), None) \
            or artifacts.run_manifest(run_id)
        if man is None:
            st.warning("该 run_id 在 `eval/runs/` 中没有对应 manifest（可能来自更早的命名）。")
        else:
            _render_manifest(man)

        st.divider()
        st.subheader("校验")
        st.caption("重算 manifest 中记录的哈希与版本号，列出已变化的项。")
        command_panel(cli.cmd_verify(run_id), key="rep_verify", mode="sync",
                      label="执行校验", timeout=600.0)

    st.divider()
    st.subheader("报告 ←→ manifest 对照")
    refs = _all_refs()
    if not refs:
        st.info("没有报告产物。")
    else:
        ref = st.selectbox("报告", refs, key="rep_ref2",
                           format_func=lambda r: f"{r.subject} · {r.label} · {r.generated_at or '—'}")
        man = artifacts.manifest_for_report(ref)
        if man is None:
            st.info("该报告没有关联的 run manifest（生成于 manifest 机制之前）。")
        else:
            _render_manifest(man)

    st.divider()
    st.subheader("当前工作区状态")
    commit, dirty = _git_state()
    st.write({"git_commit": commit, "git_dirty": dirty,
              "config sha256": _config_hashes()})
    st.caption("`git_dirty` 为真表示工作区有未提交改动，此时任何与 manifest 的比对都可能失效。")


def _render_manifest(man: dict[str, Any]) -> None:
    """把 manifest 摊平成一个两列表格。只做展示，不做任何补齐或推断。"""
    def flat(node: Any, prefix: str = "") -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if isinstance(node, dict):
            for k, v in node.items():
                out += flat(v, f"{prefix}{k}.")
        elif isinstance(node, list):
            out.append((prefix.rstrip("."), f"{len(node)} 项"))
        else:
            out.append((prefix.rstrip("."), "—" if node is None else str(node)))
        return out

    _df([{"字段": k, "值": v} for k, v in flat(man)], hide_index=True, height=380)


# ================================================================ 8 作业
def tab_jobs() -> None:
    records = jobs.jobs()
    if not records:
        st.info("还没有作业。到「跑批」页启动一个，或在此页执行维护操作。")
    else:
        rows = []
        for j in records[:200]:
            rows.append({
                "job_id": j.get("job_id"),
                "类型": j.get("kind"),
                "subject": j.get("subject"),
                "状态": j.get("status"),
                "退出码": j.get("exit_code"),
                "开始": j.get("started"),
                "耗时(s)": jobs.live_elapsed(j),
                "说明": j.get("note"),
            })
        _df(rows, hide_index=True)

        # 选项用 job_id 字符串而非记录 dict：一是字符串在 session_state 里稳定，
        # 二是池子随作业增删会变，若把可变对象当选项，选中项一旦消失就会在
        # 下一次整页重跑时抛错。默认选中最新的运行中作业，没有则选最新的。
        ids = [j["job_id"] for j in records]
        default = next((i for i, j in enumerate(records)
                        if j.get("status") == jobs.RUNNING), 0)
        chosen = st.selectbox("查看作业", ids, index=default, key="job_sel")
        # jobs() 只读 job.json，不做状态刷新。这里先刷新一次，否则一个已经死掉的
        # 作业会一直以 running 呈现，用户会以为还能停止它。
        sel = jobs.refresh(next(j for j in records if j["job_id"] == chosen))

        if sel.get("status") == jobs.RUNNING:
            _live_job(sel["job_id"])
        else:
            _static_job(sel)

    st.divider()
    st.subheader("维护")
    orphans = jobs.orphan_job_dirs()
    if orphans:
        st.warning(f"存在 {len(orphans)} 个没有 `job.json` 的作业目录（对作业列表不可见，"
                   "也不被单作业清理受理）：{'、'.join(orphans)}")
        if st.button(f"清理全部 {len(orphans)} 个残留目录", key="job_clean_orphans"):
            ok, msgs = 0, []
            for oid in orphans:
                r, m = jobs.cleanup_job(oid, force=True)
                ok += int(r)
                msgs.append(m)
            st.success(f"已清理 {ok} 个。" + ("；".join(msgs) if msgs else ""))
            _rerun_app()
    else:
        st.caption("没有残留的作业目录。")


def _live_job(job_id: str) -> None:
    """运行中作业的自动刷新面板。设计 §6：用 `st.fragment(run_every="2s")` 局部重跑。

    为什么不用整页 `st.rerun()`：整页重跑会重置所有控件状态，用户在跑批期间想改
    看另一个页签就会被弹回来。fragment 只重跑这一块。
    """
    st.caption(f"每 {POLL_SECONDS} 自动刷新（局部重跑，不影响页面其他控件）。")

    @_fragment(run_every=POLL_SECONDS)
    def _panel() -> None:
        rec = jobs.job(job_id)
        if rec is None:
            st.warning("作业记录已不存在。")
            return
        if rec.get("status") in jobs.TERMINAL:
            # 终态：自身不再轮询，请整页重跑一次把静态视图换上来。
            if st.session_state.get("_settled_job") != job_id:
                st.session_state["_settled_job"] = job_id
                _rerun_app()
            return
        rec = jobs.refresh(rec)
        _render_job(rec, live=True)

    _panel()

    rec = jobs.job(job_id) or {}
    if rec.get("status") in jobs.TERMINAL:
        _render_job(rec, live=False)


def _static_job(rec: dict[str, Any]) -> None:
    _render_job(rec, live=False)


def _render_job(rec: dict[str, Any], *, live: bool) -> None:
    st.code(rec.get("display") or "", language="bash")
    status = rec.get("status")
    # 状态在这里写成彩色小标签，正文里就不再重复一个「状态 running」——
    # 同一件事说两遍、其中一遍还没有颜色，读者要先在字符串里找词再回头看标签。
    # 退出码紧挨标签，因为「failed + 退出码 0」这种组合正是要一眼看出来的。
    _tags([(JOB_TONE.get(status, "mute"),
            f"{status or '—'} · 退出码 {rec.get('exit_code')}")])
    st.caption(
        f"cwd `{rec.get('cwd')}` · pid {rec.get('pid')} · "
        f"git {rec.get('git_commit') or '—'}"
        + ("（工作区有未提交改动）" if rec.get("git_dirty") else "")
        + f" · config sha256 {str(rec.get('config_sha256') or '—')[:12]}…"
    )

    rows = jobs.progress(rec)
    if rows:
        agg = jobs.aggregate_progress(rows)
        before = sum(r.before for r in rows)
        added = sum(r.added for r in rows)
        st.progress(min(max(agg["pct"] / 100.0, 0.0), 1.0),
                    text=f"{agg['done']} / {agg['total']}（{agg['pct']:.1f}%）")
        st.caption(
            f"已处理 {agg['done']} / 总计 {agg['total']}"
            f"（本次新增 {added}，此前已完成 {before}）；"
            f"完成卷 {agg['n_done_suites']}/{agg['n_suites']}；累计失败记录 {agg['errors']} 条。"
        )
        st.caption("进度由各卷结果文件的**有效行数**推导，不解析 stdout；"
                   "分母为 min(任务条数, 配置配额)。")
        _df([{
            "卷": r.suite, "阶段": r.phase, "有效": r.done, "应跑": r.total,
            "本次新增": r.added, "此前已完成": r.before,
            "完成度": f"{r.pct * 100:.1f}%", "失败": r.errors,
        } for r in rows], hide_index=True)

        errs = jobs.manifest_err_map(rec)
        if errs:
            st.caption("以下卷产生了 manifest，但 manifest 记录了错误：")
            _df([{"卷": k, **v} for k, v in errs.items()], hide_index=True)

    if live:
        if st.button("停止该作业", key=f"job_stop_{rec['job_id']}"):
            ok, msg = jobs.stop(rec["job_id"])
            (st.success if ok else st.error)(msg)
            _rerun_app()
    else:
        c1, c2 = st.columns(2)
        with c1:
            if st.button("删除该作业目录", key=f"job_clean_{rec['job_id']}"):
                ok, msg = jobs.cleanup_job(rec["job_id"])
                (st.success if ok else st.error)(msg)
                _rerun_app()
        with c2:
            if st.button("强制删除", key=f"job_force_{rec['job_id']}"):
                ok, msg = jobs.cleanup_job(rec["job_id"], force=True)
                (st.success if ok else st.error)(msg)
                _rerun_app()

    log = jobs.tail_log(rec["job_id"])
    st.caption("标准输出末尾（仅供人工判断，程序不做任何解析）：")
    st.code(log or "(空)", language="text")


# ================================================================ 导航与主入口
# 导航项与各自的渲染函数。顺序即侧栏顺序；`NAV_SERVICE` 是原来侧栏里那些
# 常驻内容，改成第 9 个页面后不再占着每一页的侧栏空间。
NAV_ITEMS: tuple[tuple[str, str], ...] = (
    (":material/dashboard: 总览", "overview"),
    (":material/tune: 配置", "config"),
    (":material/play_arrow: 跑批", "run"),
    (":material/description: 报告", "reports"),
    (":material/bar_chart: 对比", "compare"),
    (":material/warning: 坏例", "badcase"),
    (":material/science: 复现", "repro"),
    (":material/work_history: 作业", "jobs"),
    (":material/settings: 服务", "service"),
)
NAV_KEY = "nav"  # 与「跑批」页的 `run_mode` radio 区分：定位靠 key，不靠下标


def _nav_display(label: str) -> str:
    """把带 Material 图标的 radio 值还原为页面标题文本。"""
    return re.sub(r"^:material/[^:]+:\s*", "", label)


def page_service() -> None:
    """服务状态与缓存维护。原 `_sidebar()` 的内容，去掉登录相关部分。"""
    st.subheader("服务状态")
    st.write({
        "解释器": sys.executable,
        "harness 解释器": str(cli.harness_python()),
        "平台": jobs.platform_note(),
    })
    if cli.harness_python_is_fallback():
        st.warning(
            "未找到仓库内 `.venv`，子命令会以当前解释器执行。本机 Windows 上这是必然的"
            "（仓库内的 `.venv` 是 Linux ELF）。结论：本机可跑 smoke / report / "
            "rulecheck / compare / export-badcase，**不可跑真实推理**。"
        )
    lock = jobs.current_lock_job()
    if lock and jobs.lock_active():
        st.info(f"run 锁由 {_lock_note(lock)} 持有。")
    else:
        st.caption("run 锁空闲。")

    st.divider()
    st.caption(f"Streamlit {st.__version__}")
    if st.button("清空缓存并刷新", key="sb_reload"):
        st.cache_data.clear()
        health.clear_cache()
        artifacts.clear_cache()
        st.session_state["_health_token"] = st.session_state.get("_health_token", 0) + 1
        _rerun_app()
    st.caption("清掉全部数据缓存并整页重跑，耗时约 14 秒（等同冷启动）。"
               "新建了报告或结果文件但界面还没看见时用这个。")

    st.divider()
    st.caption(
        "写边界：本控制台只写 `eval/jobs/**`。所有产物写入（报告、对比、坏例材料、"
        "结果文件）都由 CLI 子进程完成（设计 §7.3）。"
    )


_PAGES: dict[str, Any] = {
    "overview": tab_overview,
    "config": tab_config,
    "run": tab_run,
    "reports": tab_reports,
    "compare": tab_compare,
    "badcase": tab_badcase,
    "repro": tab_repro,
    "jobs": tab_jobs,
    "service": page_service,
}


def _render_page(fn: Any) -> None:
    """单页异常隔离。一页出错只让这一页显示错误，不影响侧栏导航与其他页。"""
    try:
        fn()
    except jobs.LockError as e:
        st.error(f"run 锁冲突：{e}")
    except FileNotFoundError as e:
        st.error(f"文件不存在：{e}")
    except Exception as e:  # noqa: BLE001
        st.error(f"该页渲染失败：{type(e).__name__}: {e}")
        with st.expander("详细堆栈"):
            import traceback
            st.code(traceback.format_exc(), language="text")


def main() -> None:
    st.set_page_config(
        page_title="ElderBench 评测控制台",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    theme.inject_theme()

    labels = [label for label, _ in NAV_ITEMS]
    with st.sidebar:
        st.markdown(
            '<div class="eb-brand"><div class="eb-brand-mark">EB</div>'
            '<div><div class="eb-brand-name">ElderBench</div>'
            '<div class="eb-brand-sub">Evaluation Console</div></div></div>',
            unsafe_allow_html=True,
        )
        # `st.radio` 而不是 `st.tabs`：tabs 下所有 builder 每次重跑都执行（只是隐藏），
        # radio 只执行选中页。这是本次改动带来的最大行为差异，好处是单页渲染开销下降，
        # 代价是「作业」页的 2 秒轮询只在选中该页时进行。
        picked = st.radio("页面", labels, key=NAV_KEY, label_visibility="collapsed")
        st.markdown(
            '<div class="eb-sidebar-note">内网只读控制台<br>'
            '产物来源：eval/results</div>',
            unsafe_allow_html=True,
        )

    picked_display = _nav_display(picked)
    st.markdown(
        f'<div class="eb-page-head"><div><div class="eb-eyebrow">'
        f'ElderBench · Evaluation Console</div><h1>{picked_display}</h1></div>'
        f'<div class="eb-page-chip">内网控制台</div></div>',
        unsafe_allow_html=True,
    )
    snapshot_banner()
    _render_page(_PAGES[dict(NAV_ITEMS)[picked]])


main()

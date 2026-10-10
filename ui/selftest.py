#!/usr/bin/env python
"""控制台自测：设计 §8.3 验收表里可机器判定的部分。

用法（用跑控制台的那个解释器执行）：

    .venv-web/bin/python -X utf8 ui/selftest.py             # 全部
    .venv-web/bin/python -X utf8 ui/selftest.py -k argv     # 只跑名字含 argv 的项
    .venv-web/bin/python -X utf8 ui/selftest.py -v          # 打印每项的详细结论

退出码：有 FAIL 返回 1，只有 PASS/SKIP 返回 0。

本文件的定位
------------
`ui/` 为了不导入 `run_eval` 与 `runner`（它们会拖进 pandas / jieba / nltk），
在少数几处**有意重复**了 CLI 的逻辑：`config.py` 的 `env:` 解析、坏例抽样
判据的调用方式、结果行的过滤条件。重复本身不是问题，失去同步才是。因此这里
把这些重复钉在断言上：CLI 一侧的语义一旦分叉，本文件会红。

断言分三类，写法不同：

1. **等价断言**：控制台实现与 CLI 实现对同一输入的输出必须逐键相等。能在子
   进程里跑 CLI 就跨进程真跑（`env-parity`），跑不动就退化为 SKIP 并说明原因，
   不伪造结论。
2. **契约断言**：控制台依赖的 CLI 侧函数签名（参数个数、关键字名）必须不变。
   `report_markdown` 少传一个参数就会在页面上抛异常，这类破坏在签名层拦住。
3. **边界断言**：写边界、依赖边界、密钥不泄露。这三项是纪律，不是功能，用
   静态扫描 + 渲染后文本检索来判定，不依赖"读代码时看起来没问题"。

只读性
------
除 `eval/jobs/**`（控制台唯一的可写根，且只有 `jobs.py` 会写）之外，本文件
不写入任何路径。`secrets` 一项会启动一次 AppTest，AppTest 会把应用整页渲染
一遍；如果此时恰好有待刷新的作业记录，`jobs.refresh` 会更新 `eval/jobs/` 下
的 `job.json`。这是控制台在正常浏览时本就会做的写操作，不影响受保护路径。
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import os
import random
import re
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

# 与 app.py 同样的处理：把 ui/ 放进 sys.path，使扁平的兄弟模块可导入。
# 以 `python ui/selftest.py` 直接执行时 sys.path[0] 已经是 ui/，这行是为了
# `python -m ui.selftest` 与从别处导入这两种用法也能工作。
_UI_DIR = Path(__file__).resolve().parent
if str(_UI_DIR) not in sys.path:
    sys.path.insert(0, str(_UI_DIR))

import artifacts  # noqa: E402
import charts  # noqa: E402
import cli  # noqa: E402
import config  # noqa: E402
import harness  # noqa: E402
import health  # noqa: E402
import jobs  # noqa: E402
import metricmap  # noqa: E402
import paths  # noqa: E402
from harness import metrics, reporting  # noqa: E402


# ================================================================ 检查框架
PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_SYMBOL = {PASS: "✓", FAIL: "✗", SKIP: "·"}


@dataclass
class Outcome:
    status: str
    note: str = ""


class Bag:
    """收集一个检查项下的多条断言。全部通过才算通过。

    不写 `assert` 而是攒起来，是因为自测的价值在于一次跑完看到**全部**失配项，
    而不是在第一处失败就退出，然后靠反复重跑逐个发现。
    """

    def __init__(self) -> None:
        self.bad: list[str] = []
        self.notes: list[str] = []
        self.n = 0

    def eq(self, got: Any, want: Any, what: str) -> bool:
        self.n += 1
        ok = got == want
        if not ok:
            self.bad.append(f"{what}：得到 {got!r}，期望 {want!r}")
        return ok

    def true(self, cond: Any, what: str) -> bool:
        self.n += 1
        ok = bool(cond)
        if not ok:
            self.bad.append(what)
        return ok

    def note(self, text: str) -> None:
        self.notes.append(text)

    def done(self, note: str = "") -> Outcome:
        if self.bad:
            head = "；".join(self.bad[:6])
            tail = f" …（共 {len(self.bad)} 条失配）" if len(self.bad) > 6 else ""
            return Outcome(FAIL, f"{len(self.bad)}/{self.n} 条断言未通过：{head}{tail}")
        return Outcome(PASS, note or "；".join(self.notes) or f"{self.n} 条断言通过")


def _app_modules() -> list[Path]:
    """被静态扫描的模块：`ui/` 下除本文件之外的全部 .py。

    本文件不进扫描范围，原因有二。一是它自己要调 `subprocess` 去跑跨进程断言，
    属于测试脚手架而非应用表面；二是一旦把自己也纳进"不得出现写调用"的规则，
    写规则就得为它开例外，例外会掩盖真正的越界。应用表面是 `ui/` 下其余的模块
    ——不写死数目，加一个页面模块就红一次注释里的数字是纯粹的噪声来源。
    """
    me = Path(__file__).name
    return sorted(p for p in paths.UI_DIR.glob("*.py") if p.name != me)


def _nav_labels() -> list[str]:
    """静态取 `app.py` 的 `NAV_ITEMS` 页面名。

    不 `import app`：`app.py` 末尾是无条件的 `main()`，导入等于在本进程里跑一遍
    Streamlit 脚本，而本进程没有 ScriptRunContext。用 AST 取字面量，与其余静态
    检查同一手法。取不到时返回空表，调用方的断言会退化为"侧栏一个页面都没有"，
    不会因为解析失败而误判为通过。
    """
    src = (paths.UI_DIR / "app.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src, filename="app.py")):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not (isinstance(node.target, ast.Name) and node.target.id == "NAV_ITEMS"):
            continue
        labels: list[str] = []
        for item in getattr(node.value, "elts", []):
            first = getattr(item, "elts", [None])[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                labels.append(first.value)
        return labels
    return []


_REGISTRY: list[tuple[str, str, Callable[[], Outcome]]] = []


def check(name: str, title: str):
    def deco(fn: Callable[[], Outcome]) -> Callable[[], Outcome]:
        _REGISTRY.append((name, title, fn))
        return fn
    return deco


# ================================================================ 1 依赖边界
# 控制台进程不得导入的模块。前四个是 harness 侧的判分/推理依赖，后几个是
# eval/ 下会间接拖进它们的模块（harness.py 把 eval/ 放进了 sys.path，所以
# 这些名字**可导入**，能不能导入不是判据，有没有被导入才是）。
_FORBIDDEN_MODULES = (
    "jieba", "nltk", "httpx",
    "scoring", "adapters", "runner", "run_eval", "detectors", "model_client",
)


@check("deps", "依赖边界：控制台不导入 harness 侧的重依赖")
def check_deps() -> Outcome:
    b = Bag()
    # 正向对照：本进程确实已经把 ui/ 的模块导进来了，否则下面那条会因为
    # "什么都没导入"而平凡成立。
    b.true("artifacts" in sys.modules and "jobs" in sys.modules, "ui/ 模块未被导入")
    b.true("metrics" in sys.modules and "reporting" in sys.modules,
           "harness 注入的 metrics / reporting 未生效")

    leaked = sorted(m for m in _FORBIDDEN_MODULES if m in sys.modules)
    b.eq(leaked, [], "已加载的禁入模块")

    # 静态层：即使某个模块此刻恰好没被触发，也不允许源码里出现 import 语句。
    bad_imports: list[str] = []
    for path in _app_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in _FORBIDDEN_MODULES:
                    bad_imports.append(f"{path.name}:{node.lineno} import {n}")
    b.eq(bad_imports, [], "源码中的禁入 import")

    b.note(f"sys.path 含 eval/ 时禁入模块仍为 0 个：{len(_FORBIDDEN_MODULES)} 个")
    return b.done()


# ================================================================ 2 写边界
# 名字只可能是文件操作的
_MUTATORS = {
    "write_text", "write_bytes", "write_json", "unlink", "rmtree", "makedirs",
    "rmdir", "truncate", "touch", "chmod",
}
# 名字有歧义的：`DataFrame.rename(columns=...)` 是改列名，`str.replace` 是改文本，
# 都不是写文件。只在这些名字作用在 os / shutil / 名字像路径的对象上时才判为写。
_AMBIGUOUS_MUTATORS = {"mkdir", "rename", "replace", "remove"}
_PATH_HINTS = ("os", "shutil", "path", "file", "dir", "root", "target", "dest", "src", "tmp")
# 允许出现写调用的模块：paths.py 定义白名单并实现写入，jobs.py 是唯一使用者。
_WRITE_OK = {"paths.py", "jobs.py"}
_SUBPROCESS_OK = {"cli.py", "jobs.py"}


def _call_name(node: ast.Call) -> Optional[str]:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return None


def _is_subprocess_call(node: ast.Call) -> bool:
    fn = node.func
    return (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
            and fn.value.id == "subprocess")


def _receiver_name(node: ast.Call) -> str:
    """调用表达式的接收者名字（`df.rename` → "df"），没有则空串。"""
    fn = node.func
    if not isinstance(fn, ast.Attribute):
        return ""
    v = fn.value
    if isinstance(v, ast.Name):
        return v.id.lower()
    if isinstance(v, ast.Attribute):
        return v.attr.lower()
    return ""


def _is_write_open(node: ast.Call) -> bool:
    """`open(p, "w")` / `open(p, mode="a")` 这类写模式的调用。"""
    args = list(node.args)
    mode: Optional[str] = None
    if len(args) >= 2 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
        mode = args[1].value
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            mode = kw.value.value
    return mode is not None and bool(set(mode) & set("wax+"))


@check("write-boundary", "写边界：控制台只能写 eval/jobs/**")
def check_write_boundary() -> Outcome:
    b = Bag()

    # --- 运行期：白名单函数本身的行为
    ok_path = paths.assert_writable(paths.JOBS_DIR / "_selftest_probe.json")
    b.eq(ok_path, paths.JOBS_DIR / "_selftest_probe.json", "jobs/ 下的路径应被放行")

    for name, root in sorted(paths.PROTECTED.items()):
        for probe in (root, root / "_selftest_probe.txt"):
            try:
                paths.assert_writable(probe)
            except PermissionError:
                b.n += 1
            except Exception as exc:  # noqa: BLE001
                b.n += 1
                b.bad.append(f"{name} 的 {probe.name} 抛了 {type(exc).__name__} 而不是 PermissionError")
            else:
                b.n += 1
                b.bad.append(f"{name} 下的 {probe.name} 未被拒绝")

    # --- 静态：其余模块不得出现任何写调用或子进程调用
    bad: list[str] = []
    for path in _app_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if path.name not in _WRITE_OK:
                if name in _MUTATORS:
                    bad.append(f"{path.name}:{node.lineno} 调用 {name}()")
                elif name in _AMBIGUOUS_MUTATORS:
                    recv = _receiver_name(node)
                    if any(h in recv for h in _PATH_HINTS):
                        bad.append(f"{path.name}:{node.lineno} 对 {recv} 调用 {name}()")
                elif name == "open" and _is_write_open(node):
                    bad.append(f"{path.name}:{node.lineno} 以写模式调用 open()")
            if path.name not in _SUBPROCESS_OK and _is_subprocess_call(node) and name != "run_sync":
                bad.append(f"{path.name}:{node.lineno} 直接调用 subprocess.{name}()")
    b.eq(sorted(set(bad)), [], "白名单外模块的写 / 子进程调用")

    b.note(f"受保护路径 {len(paths.PROTECTED)} 项，可写根 {paths.JOBS_DIR.name}/")
    return b.done()


# ================================================================ 3 命令行等价
def _argv_tail(cmd: cli.Command) -> list[str]:
    """去掉解释器前缀后的参数列表。

    `_py_cmd` 的前缀是 `[harness_python, "-X", "utf8", "run_eval.py"]`，这里
    只比较第 5 位起的部分，使断言与"用哪个解释器"解耦（解释器由 env-parity
    与 harness_python 自身负责）。
    """
    return list(cmd.argv[4:])


@check("argv", "命令行等价：表单输入生成的 argv 与手写期望逐元素相等")
def check_argv() -> Outcome:
    b = Bag()
    suites = cli.available_suites()
    if not suites:
        return Outcome(SKIP, "eval/data/ 下没有任务文件，无法构造带真实卷名的命令")
    suite = suites[0]
    subject = (config.subject_names() or ["qwen35_4b_base"])[0]

    # --- 前缀：所有 Python 子命令统一为 `<harness_python> -X utf8 run_eval.py`
    c = cli.cmd_audit()
    b.eq(c.argv[:1], [str(cli.harness_python())], "解释器")
    b.eq(c.argv[1:4], ["-X", "utf8", "run_eval.py"], "解释器参数")
    b.eq(c.cwd, paths.EVAL_DIR, "工作目录")

    # --- 各子命令的参数布局。期望值逐字手写，不调用 cli 内部的 _opt 拼装。
    cases: list[tuple[str, cli.Command, list[str]]] = [
        ("run 全参数", cli.cmd_run(suite, subject, limit=20, concurrency=8),
         ["run", "--suite", suite, "--subject", subject, "--limit", "20", "--concurrency", "8"]),
        ("run 可选参数缺省", cli.cmd_run(suite, subject),
         ["run", "--suite", suite, "--subject", subject]),
        ("report 带 run", cli.cmd_report(subject, run="r_20260101_000000"),
         ["report", "--subject", subject, "--run", "r_20260101_000000"]),
        ("rescore 多卷", cli.cmd_rescore(subject, suites=["a", "b"], scorer_version="1.1"),
         ["rescore", "--subject", subject, "--suite", "a", "b", "--scorer-version", "1.1"]),
        ("rulecheck --baseline", cli.cmd_rulecheck(baseline=True),
         ["rulecheck", "--baseline"]),
        ("rulecheck 缺省", cli.cmd_rulecheck(), ["rulecheck"]),
        ("compare 多 subject", cli.cmd_compare(["s1", "s2"], run="r1"),
         ["compare", "--subjects", "s1", "s2", "--run", "r1"]),
        ("export-badcase", cli.cmd_export_badcase(subject, suite, criterion="risk_flag"),
         ["export-badcase", "--subject", subject, "--suite", suite,
          "--criterion", "risk_flag", "--n", "40", "--seed", "20260921"]),
        ("manifest", cli.cmd_manifest(subject),
         ["manifest", "--subject", subject]),
        ("smoke", cli.cmd_smoke(), ["smoke"]),
        ("build 指定卷", cli.cmd_build(["a", "b"]), ["build", "--suites", "a", "b"]),
        ("audit", cli.cmd_audit(), ["audit"]),
    ]
    for label, cmd, want in cases:
        b.eq(_argv_tail(cmd), want, label)

    # --- 批次走 shell 脚本，前缀不同（设计 §4.4：复用 run_suite_all.sh）
    batch = cli.cmd_batch(subject, concurrency=16)
    b.eq(batch.argv, ["bash", cli.BATCH_SCRIPT, subject, "16"], "批次命令 argv")
    b.eq(batch.cwd, paths.REPO_ROOT, "批次命令工作目录")

    # --- `_opt` 的三态语义：None / "" / False 不追加，True 只追加旗标
    b.eq(_argv_tail(cli.cmd_report(subject, run=None)),
         ["report", "--subject", subject], "run=None 不追加")
    b.eq(_argv_tail(cli.cmd_report(subject, run="")),
         ["report", "--subject", subject], "run='' 不追加")
    zero = _argv_tail(cli.cmd_export_badcase(subject, suite, n=0))
    b.true("--n" in zero and zero[zero.index("--n") + 1] == "0",
           f"n=0 是合法值，应作为 --n 0 追加而不是被 _opt 当作假值丢掉：{zero}")

    # --- 可执行性守卫确实存在（临时打开 subject 校验，跑完复原）
    saved = cli.AVAILABLE_SUBJECTS
    try:
        cli.set_available_subjects(["only_this_one"])
        b.true(not cli.cmd_run(suite, "not_a_subject").runnable,
               "未知 subject 应被标记为不可执行")
        b.true(cli.cmd_run(suite, "only_this_one").runnable,
               "已知 subject 应可执行")
    finally:
        cli.AVAILABLE_SUBJECTS = saved
    b.true(not cli.cmd_verify("no_such_run_id").runnable, "未知 run_id 应被标记为不可执行")

    b.note(f"{len(cases)} 条命令布局 + 前缀 + 三态语义")
    return b.done()


# ================================================================ 报告级检查
def _all_reports() -> dict[str, dict[str, Any]]:
    """subject → 最新一份报告的 summary。"""
    out: dict[str, dict[str, Any]] = {}
    for name, ref in artifacts.latest_by_subject().items():
        try:
            out[name] = ref.load()
        except Exception as exc:  # noqa: BLE001
            out[name] = {"_load_error": f"{type(exc).__name__}: {exc}"}
    return out


def _raw_value(summary: dict[str, Any], ind: metricmap.Indicator) -> Any:
    """独立读一遍指标值。

    刻意不复用 `metricmap.value`：本检查要证明的正是"界面显示的数就是报告里
    那个字段"，两条路径必须分开走，否则是自证。
    """
    if ind.kind == "redline":
        return (summary.get("redline") or {}).get(ind.field)
    return ((summary.get("per_suite") or {}).get(ind.suite) or {}).get(ind.field)


@check("metric-values", "数值一致性：界面读数 == 报告原始字段")
def check_metric_values() -> Outcome:
    b = Bag()
    reports = _all_reports()
    b.true(reports, "没有找到任何报告，无法检查")
    if not reports:
        return b.done()
    for subject, s in sorted(reports.items()):
        if "_load_error" in s:
            b.bad.append(f"{subject}：报告加载失败 {s['_load_error']}")
            b.n += 1
            continue
        for ind in metricmap.ALL_INDICATORS:
            got = metricmap.value(s, ind)
            raw = _raw_value(s, ind)
            b.n += 1
            if got is None and raw is None:
                continue          # 该卷未纳入本次评测，两侧都缺席是一致的
            if got is None or raw is None:
                b.bad.append(f"{subject}/{ind.key}：一侧为 None（界面 {got!r} / 报告 {raw!r}）")
            elif abs(float(got) - float(raw)) > 1e-9:
                b.bad.append(f"{subject}/{ind.key}：{got} != {raw}")
        b.note(f"{subject} {len(metricmap.ALL_INDICATORS)} 个指标")
    return b.done(f"{len(reports)} 份报告 × {len(metricmap.ALL_INDICATORS)} 个指标逐字段一致")


@check("dim-projection", "per_dimension(_reference_only) 是 per_suite 的投影")
def check_dim_projection() -> Outcome:
    b = Bag()
    n_cmp = 0
    for subject, s in sorted(_all_reports().items()):
        if "_load_error" in s:
            continue
        pd = s.get("per_dimension") or {}
        ref = s.get("per_dimension_reference_only") or {}
        for ind in metricmap.DIM_INDICATORS:
            if ind.dim_path is None:
                continue
            dim, field = ind.dim_path
            node = ref.get(dim) if not ind.headline else pd.get(dim)
            if not isinstance(node, dict) or field not in node:
                continue          # 历史报告缺该投影时跳过；新报告由 metrics 生成
            a = metricmap.value(s, ind)
            c = node.get(field)
            n_cmp += 1
            b.n += 1
            if a is None or c is None or abs(float(a) - float(c)) > 1e-9:
                b.bad.append(f"{subject}/{ind.key}：per_suite {a!r} != 维度投影 {c!r}")
    b.note(f"比对 {n_cmp} 处")
    return b.done(f"维度投影与 per_suite 在 {n_cmp} 处取值一致")


@check("ci-format", "区间格式：ci_str 与 reporting._fmt_ci 一致")
def check_ci_format() -> Outcome:
    b = Bag()
    n_ci, n_na = 0, 0
    for subject, s in sorted(_all_reports().items()):
        if "_load_error" in s:
            continue
        for ind in metricmap.ALL_INDICATORS:
            got = metricmap.ci_str(s, ind)
            ci = metricmap.ci(s, ind)
            b.n += 1
            if ci is None:
                # 有意分叉：报告里无区间写 "n/a"，表格里写 "—"（避免出现空洞）
                n_na += 1
                if got != "—":
                    b.bad.append(f"{subject}/{ind.key}：无区间时应显示 “—”，实为 {got!r}")
                continue
            n_ci += 1
            want = reporting._fmt_ci(metricmap.k_of(s, ind), metricmap.n_of(s, ind))
            if got != want:
                b.bad.append(f"{subject}/{ind.key}：{got!r} != _fmt_ci {want!r}")
    b.note(f"有区间 {n_ci} 处、无区间 {n_na} 处")
    return b.done(f"{n_ci} 处区间与报告渲染逐字相同，{n_na} 处为有意分叉的 “—”")


@check("k-roundtrip", "k 往返校验：落盘值与反推值一致")
def check_k_roundtrip() -> Outcome:
    b = Bag()
    n_stored, n_derived, n_none = 0, 0, 0
    for subject, s in sorted(_all_reports().items()):
        if "_load_error" in s:
            continue
        for ind in metricmap.ALL_INDICATORS:
            if not ind.is_proportion:
                continue
            rate = metricmap.value(s, ind)
            n = metricmap.n_of(s, ind)
            stored = None
            if ind.k_path is not None:
                node = (s.get("per_suite") or {}).get(ind.suite) or {}
                container = node.get(ind.k_path[0])
                stored = container.get(ind.k_path[1]) if isinstance(container, dict) else None
            derived = metricmap.derive_k(rate, n)
            k = metricmap.k_of(s, ind)
            if stored is not None and derived is not None:
                n_stored += 1
                b.n += 1
                if int(stored) != derived:
                    b.bad.append(f"{subject}/{ind.key}：落盘 k={stored} != 反推 k={derived}（rate={rate}, n={n}）")
                b.n += 1
                if k != int(stored):
                    b.bad.append(f"{subject}/{ind.key}：k_of 应取落盘值 {stored}，实为 {k}")
            elif derived is None and rate is not None:
                # 反推失败只允许发生在非 k/n 形式的指标上（ROUGE 均值、F1）
                n_none += 1
                b.n += 1
                if abs(float(rate) - round(float(rate) * (n or 1) / 100.0) * 100.0 / (n or 1)) < 1e-12:
                    b.bad.append(f"{subject}/{ind.key}：反推失败但数值看起来是 k/n 形式（rate={rate}, n={n}）")
            else:
                n_derived += 1
    b.note(f"两侧都可得 {n_stored} 处、仅反推 {n_derived} 处、反推失败 {n_none} 处")
    return b.done(f"{n_stored} 处落盘 k 与反推 k 相等")


@check("gates", "门禁等价：gates.overall ⟺ 主红线越阈；次级单独成键")
def check_gates() -> Outcome:
    b = Bag()
    n = 0
    for subject, s in sorted(_all_reports().items()):
        if "_load_error" in s:
            continue
        gate = metricmap.report_gate(s)
        if gate is None:
            continue
        n += 1
        hits = [i.key for i in metricmap.PRIMARY_REDLINES
                if metricmap.breached(s, i) is True]
        b.eq(gate, "FAIL" if hits else "PASS", f"{subject} 整体门禁（越阈主红线 {hits or '无'}）")

        sec = metricmap.secondary_gate(s)
        sec_hits = [i.key for i in metricmap.REDLINE_INDICATORS
                    if i.gate_role == "secondary" and metricmap.breached(s, i) is True]
        b.eq(sec, "EXCEEDED" if sec_hits else "OK", f"{subject} 次级结论（越阈 {sec_hits or '无'}）")
        # 次级不得参与整体门禁
        b.n += 1
        if sec_hits and gate == "FAIL" and not hits:
            b.bad.append(f"{subject}：仅次级越阈却判了整体 FAIL")
    return b.done(f"{n} 份报告的门禁与红线逐份一致")


@check("suites", "卷清单：config 声明的卷 == 报告实际纳入的卷")
def check_suites() -> Outcome:
    b = Bag()
    want = health.expected_suites()
    b.eq(len(want), 10, "config.yaml 声明的卷数")
    b.true("elder_safety" in want and "risk_crisis" in want, "抽样配置里应含两个核心卷")
    for subject, s in sorted(_all_reports().items()):
        if "_load_error" in s:
            continue
        got = sorted(s.get("suites_included") or [])
        b.eq(got, want, f"{subject} 纳入的卷")
    return b.done(f"10 卷 × {len(_all_reports())} 份报告一致")


@check("counts", "结果计数：load_counts.valid == 过滤后的有效行数")
def check_counts() -> Outcome:
    b = Bag()
    files = artifacts.result_files()
    b.true(files, "eval/results/ 下没有结果文件")
    n_file = 0
    worst = 0.0
    for (subject, suite), path in sorted(files.items()):
        if not path.exists():
            continue
        n_file += 1
        # 独立复刻 runner.py:97 的过滤条件（不复用 runner，它会拖进 scoring）
        valid = sum(1 for r in paths.iter_jsonl(path) if "task" in r and "error" not in r)
        err = sum(1 for r in paths.iter_jsonl(path) if "error" in r)
        got = artifacts.load_counts(path)
        b.eq(got.valid, valid, f"{subject}/{suite} 有效行数")
        b.eq(got.error, err, f"{subject}/{suite} 错误行数")
        b.eq(got.n_reported, got.valid, f"{subject}/{suite} n_reported 应等于 valid")
        b.true(0 <= got.valid <= got.n_attempted <= got.total,
               f"{subject}/{suite} 行数关系不成立：valid={got.valid} "
               f"attempted={got.n_attempted} total={got.total}")
        if got.n_attempted:
            worst = max(worst, got.error / got.n_attempted)
    if not n_file:
        return Outcome(SKIP, "eval/results/ 下没有可读的结果文件")
    return b.done(f"{n_file} 个结果文件的有效行数与逐行过滤一致，最高网关失败率 {worst:.2%}")


# ================================================================ 4 坏例抽样
# 判读材料的头部把全部要素都写明了，所以不靠文件名正则去猜卷名与 subject
# （文件名里的 subject 本身含下划线，正则难以可靠切分）：
#   来源：`eval/results/results_elder_safety__qwen35_4b_deploy_v2.jsonl`（subject = ...）
#   判据：未指明风险（风险指明=0）
#   抽样：从失败样本中随机抽取 40 条，seed=20260921
#   全卷：500 条中失败 332 条，失败率 66.4%
_BADCASE_SRC = re.compile(r"results_(?P<suite>[A-Za-z0-9_]+?)__(?P<subject>[A-Za-z0-9_]+)\.jsonl")
_BADCASE_CRIT = re.compile(r"^判据：(.+?)\s*$", re.M)
_BADCASE_HEAD = re.compile(r"全卷：(\d+) 条中失败 (\d+) 条")
_BADCASE_SAMP = re.compile(r"随机抽取 (\d+) 条，seed=(\d+)")
_BADCASE_TS = re.compile(r"_(\d{8})\.md$")


@check("badcase", "坏例抽样：与 CLI 生成的判读材料逐条一致")
def check_badcase() -> Outcome:
    b = Bag()
    materials = artifacts.badcase_files()
    if not materials:
        return Outcome(SKIP, "eval/reports/ 下没有 badcase_*.md，无法比对")

    checked = 0
    for md in sorted(materials):
        text = md.read_text(encoding="utf-8")
        src = _BADCASE_SRC.search(text)
        lab = _BADCASE_CRIT.search(text)
        head = _BADCASE_HEAD.search(text)
        samp = _BADCASE_SAMP.search(text)
        if not (src and lab and head and samp):
            b.n += 1
            b.bad.append(f"{md.name}：来源 / 判据 / 抽样 / 全卷 四行未全部匹配，无法比对")
            continue
        suite, subject = src["suite"], src["subject"]
        crits = reporting.BAD_CRITERIA.get(suite)
        if not crits:
            b.n += 1
            b.bad.append(f"{md.name}：BAD_CRITERIA 未覆盖卷 {suite}")
            continue
        # 判据以材料里印出的标签为准，反查判据键。这样比对的是"CLI 用哪条判据
        # 抽的样"，而不是"文件名里第几个下划线之前是什么"。
        keys = [k for k, v in crits.items() if v[0] == lab.group(1)]
        if len(keys) != 1:
            b.n += 1
            b.bad.append(f"{md.name}：判据标签 {lab.group(1)!r} 对应到 {keys}，应为唯一")
            continue
        crit = keys[0]
        ts = _BADCASE_TS.search(md.name)
        b.true(md.name.startswith(f"badcase_{suite}_{subject}_{crit}_") and ts is not None,
               f"{md.name} 的文件名与内容不符（应为 badcase_{suite}_{subject}_{crit}_<日期>.md）")

        n_total_md, n_failed_md = int(head.group(1)), int(head.group(2))
        n_md, seed_md = int(samp.group(1)), int(samp.group(2))

        finder = artifacts.result_files().get((subject, suite))
        if finder is None or not finder.exists():
            b.n += 1
            b.bad.append(f"{md.name}：结果文件 results_{suite}__{subject}.jsonl 不在 eval/results/ 下")
            continue

        rows = [r for r in paths.iter_jsonl(finder) if "task" in r and "error" not in r]
        scored = [(r, metrics.per_task_scores(r)) for r in rows]
        pred = crits[crit][1]
        failed = [(r, sc) for r, sc in scored if sc.get("suite") == suite and pred(sc)]
        want_ids = re.findall(r"- task_id: `([^`]+)`", text)

        b.eq(len(rows), n_total_md, f"{md.name} 全卷条数")
        b.eq(len(failed), n_failed_md, f"{md.name} 失败池条数")
        b.eq(len(want_ids), n_md, f"{md.name} 材料中的样本条数")
        pool = random.Random(seed_md).sample(failed, min(n_md, len(failed)))
        b.eq([r["task"]["task_id"] for r, _ in pool], want_ids, f"{md.name} 样本顺序")
        checked += 1

    if not checked:
        # 有 badcase 文件却一份都没比对成，是有问题，不是"没有可比的"
        return b.done() if b.bad else Outcome(SKIP, "没有可解析的判读材料")
    return b.done(f"{checked} 份判读材料的失败池与抽样顺序逐条一致")


# ================================================================ 5 图表
@check("charts", "图表 schema：三张图可构建且通过 Vega-Lite 校验")
def check_charts() -> Outcome:
    b = Bag()
    reports = _all_reports()
    if not reports:
        return Outcome(SKIP, "没有报告，无法构建图表")
    built: list[str] = []
    for label, fn in (("热力图", charts.heatmap),):
        try:
            c = fn(reports)
        except Exception as exc:  # noqa: BLE001
            b.n += 1
            b.bad.append(f"{label} 构建抛 {type(exc).__name__}: {exc}")
            continue
        b.n += 1
        try:
            c.to_dict(validate=True)
            built.append(label)
        except Exception as exc:  # noqa: BLE001
            b.bad.append(f"{label} schema 校验失败：{type(exc).__name__}: {exc}")
    for subject, s in sorted(reports.items()):
        for label, fn in (("长度分箱", charts.length_bins), ("子标签召回", charts.sublabel_recall)):
            try:
                c = fn(s)
            except Exception as exc:  # noqa: BLE001
                b.n += 1
                b.bad.append(f"{subject}/{label} 构建抛 {type(exc).__name__}: {exc}")
                continue
            b.n += 1
            if c is None:
                continue          # 报告无该结构（口径升级前生成），降级为不画，合法
            try:
                c.to_dict(validate=True)
                built.append(f"{subject}/{label}")
            except Exception as exc:  # noqa: BLE001
                b.bad.append(f"{subject}/{label} schema 校验失败：{type(exc).__name__}: {exc}")
    return b.done(f"{len(built)} 张图通过 Vega-Lite schema 校验")


# ================================================================ 6 设计令牌
# 视觉部分基本无法机器判定（一个色值写错，页面照常渲染），能钉住的只有两处
# 机械事实：色值只有一份、内联 HTML 是良构的。真正好不好看仍靠人眼。
_TOKENS_OWNER = "palette.py"

# 色值字面量允许出现的模块：只有 palette.py。theme.py 顶部有五个 DESIGN.md 未
# 定义的侧栏色阶，逐个豁免——按**常量名**豁免而不是按行号，行号会随编辑漂走，
# 豁免范围就会悄悄扩大或失效。豁免集里只放 theme.py 里真实存在的常量名：放一个
# 别处（如 palette.py，本来就不进这条扫描）的名字，那条豁免永远匹配不到东西，
# 读代码的人却会以为那里有个已经审过的色值。
_HEX_EXEMPT_NAMES = {"_SIDEBAR_INK", "_SIDEBAR_MUTED", "_SIDEBAR_HOVER",
                     "_SIDEBAR_ACTIVE", "_SIDEBAR_ACTIVE_INK"}


def _exempt_hex_values(path: Path) -> set[str]:
    """`path` 里那些豁免常量所持有的色值字面量。"""
    if path.name != "theme.py":
        return set()
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        if not (set(targets) & _HEX_EXEMPT_NAMES):
            continue
        out.update(m.group(0) for m in re.finditer(r"#[0-9A-Fa-f]{6}\b", ast.unparse(node)))
    return out


@check("design-tokens", "设计令牌：色值只有一份，DESIGN.md 的取值逐条落地")
def check_design_tokens() -> Outcome:
    b = Bag()
    import palette, theme

    # 1) 色值字面量不得散在各模块。允许两处：palette.py（唯一定义处）与
    #    theme.py 里被豁免的扩展常量。字符串拼接出来的值也认——`f"rgba(..."` 里的
    #    十六进制同样会被 `ast.unparse` 暴露出来。
    offenders: list[str] = []
    for path in _app_modules():
        if path.name == _TOKENS_OWNER:
            continue
        exempt = _exempt_hex_values(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.lineno <= 1:            # 模块 docstring 里引用旧色值举例
                continue
            for m in re.finditer(r"#[0-9A-Fa-f]{6}\b", node.value):
                if m.group(0) in exempt:
                    continue
                offenders.append(f"{path.name}:{node.lineno} {m.group(0)}")
    b.eq(offenders, [], f"{_TOKENS_OWNER} 之外的色值字面量")

    # 2) theme.py 的 CSS 令牌必须逐个等于 palette.py 的同名常量。这条是防漂移的
    #    主断言：改了 palette.py 而漏改插值，或反过来，都会在这里红。
    #    tint / rgb 分量 / 品牌底白字也在内：它们不是十六进制，第 1 条的色值字面量
    #    扫描看不见（`rgba(79 70 229, 0.1)` 里没有 `#`），只有这条能钉住。
    pairs = [
        ("--eb-brand:", palette.BRAND), ("--eb-brand-hover:", palette.BRAND_HOVER),
        ("--eb-brand-rgb:", palette.BRAND_RGB), ("--eb-brand-tint:", palette.BRAND_TINT),
        ("--eb-on-brand:", palette.ON_BRAND),
        ("--eb-ink:", palette.INK), ("--eb-muted:", palette.MUTED),
        ("--eb-bg:", palette.BG), ("--eb-card:", palette.CARD),
        ("--eb-line:", palette.LINE),
        ("--eb-ok:", palette.OK), ("--eb-ok-bg:", palette.OK_BG),
        ("--eb-warn:", palette.WARN), ("--eb-warn-bg:", palette.WARN_BG),
        ("--eb-bad:", palette.BAD), ("--eb-bad-bg:", palette.BAD_BG),
        ("--eb-hover:", palette.HOVER),
    ]
    for token, want in pairs:
        line = next((l.strip() for l in theme._TOKENS.splitlines() if l.strip().startswith(token)), None)
        b.eq(line, f"{token} {want};", f"CSS 令牌 {token}")

    # 3) DESIGN.md 的四档字号与两档圆角。数值是文档里的原文，改了要同步改文档。
    geometry = (("--eb-r-card:", "12px"), ("--eb-r-tag:", "6px"),
                ("--eb-fs-hero:", "32px"), ("--eb-fs-title:", "16px"),
                ("--eb-fs-body:", "15px"), ("--eb-fs-aux:", "13px"))
    # 间距：8px 为基数的展开（DESIGN.md「间距」一节）。整页的疏密都压在这几个值上，
    # 改一个而不同步文档，看页面只会觉得"哪里不对"却指不出是哪。
    spacing = (("--eb-s1:", "8px"), ("--eb-s2:", "16px"), ("--eb-s3:", "24px"),
               ("--eb-s4:", "32px"), ("--eb-s6:", "48px"), ("--eb-s8:", "64px"))
    for token, want in geometry + spacing:
        b.true(any(l.strip().startswith(f"{token} {want}") for l in theme._TOKENS.splitlines()),
               f"令牌 {token} 不是 {want}")

    # 4) 字体规则必须保留 Material Symbols 图标字体。全局 `*` 覆盖会把
    #    折叠箭头画成 `keyboard_double_arrow_left` 之类的图标名。
    b.true("[data-testid=\"stAppViewContainer\"] * { font-family:" not in theme._THEME_CSS,
           "全局字体规则不得覆盖 Streamlit 图标字体")
    b.true("Material Symbols Rounded" in theme._THEME_CSS,
           "theme.py 应显式恢复 Material Symbols 字体")

    # 5) 图表层取色必须来自同一模块，不得再有第二套。
    from charts import BRAND as CHART_BRAND, INK as CHART_INK, MUTED as CHART_MUTED
    b.eq((CHART_BRAND, CHART_INK, CHART_MUTED), (palette.BRAND, palette.INK, palette.MUTED),
         "charts.py 的色值")
    b.true(all(getattr(palette, n) for n in ("BRAND", "INK", "OK", "WARN", "BAD")),
           "palette 常量缺失")

    # 计数从列表取，不写死数字：加一条令牌断言忘了同步注释里的数，这行就会报出
    # 一个比实际少的值，而断言全绿——成了唯一的假信息源。
    b.note(f"{len(pairs)} 条色令牌与 {len(geometry) + len(spacing)} 项尺寸令牌逐条"
           f"等于 {_TOKENS_OWNER} / DESIGN.md 原文；{_TOKENS_OWNER} 之外的色值字面量 0 处")
    return b.done()


# ================================================================ 7 页面加载项
@check("health-checks", "页面加载路径不含远端探针（设计 §5.2）")
def check_health_scope() -> Outcome:
    b = Bag()
    names = [k for k, _ in health.LOCAL_CHECKS]
    fns = [fn for _, fn in health.LOCAL_CHECKS]
    for probe in ("local_endpoint", "probe_endpoint"):
        b.true(probe not in names, f"{probe} 不应出现在 LOCAL_CHECKS 中")
        b.true(health.check_local_endpoint not in fns, f"{probe} 的函数对象不应出现在 LOCAL_CHECKS 中")
    b.true(callable(health.check_local_endpoint),
           "探针函数不存在，本项检查将平凡成立，需确认它是否被改名")
    checks = health.run_local()
    b.eq(sorted(c.key for c in checks), sorted(names), "run_local 返回的检查项")
    b.note(f"{len(checks)} 项页面加载检查，探针不在其中")
    return b.done(f"{len(checks)} 项本地检查，0 个远端探针")


# ================================================================ 8 签名契约
@check("signatures", "CLI 渲染函数签名：控制台的调用方式仍然成立")
def check_signatures() -> Outcome:
    b = Bag()

    def required(fn: Callable[..., Any]) -> tuple[list[str], list[str]]:
        sig = inspect.signature(fn)
        pos, kw = [], []
        for p in sig.parameters.values():
            if p.default is not inspect.Parameter.empty:
                continue
            if p.kind is p.KEYWORD_ONLY:
                kw.append(p.name)
            elif p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
                pos.append(p.name)
        return pos, kw

    pos, _ = required(reporting.render_markdown)
    b.eq(len(pos), 2, "render_markdown 的位置参数个数（界面按 f(summary, generated_at) 调用）")
    pos, _ = required(reporting.render_compare)
    b.eq(len(pos), 3, "render_compare 的位置参数个数")
    _, kw = required(reporting.render_badcase)
    b.true(set(kw) <= {"subject", "suite", "criterion_label", "n_total", "n_failed",
                       "n_sampled", "seed", "source", "samples"},
           f"render_badcase 的必需关键字参数集合变了：{kw}")
    b.eq(len(inspect.signature(reporting.wilson_ci).parameters), 3, "wilson_ci 参数个数")
    b.eq(len(inspect.signature(reporting.group_subjects).parameters), 2, "group_subjects 参数个数")

    # harness 暴露的名字是界面与 CLI 之间唯一的接口面
    for name in ("metrics", "reporting", "SUITE_METRIC_LABEL", "wilson_ci",
                 "group_subjects", "render_markdown", "render_compare", "bin_of", "bin_sort_key"):
        b.true(hasattr(harness, name), f"harness 未导出 {name}")
    for name in ("DIM_NAMES", "REDLINE_LABELS", "THRESHOLD_SOURCE", "BAD_CRITERIA",
                 "BIN_METRIC_LABEL", "wilson_ci", "_fmt_ci"):
        b.true(hasattr(reporting, name), f"reporting 未定义 {name}")
    for name in ("DIM_INDICATORS", "REDLINE_INDICATORS", "PRIMARY_REDLINES",
                 "ALL_INDICATORS", "LOW_N_THRESHOLD", "by_key"):
        b.true(hasattr(metricmap, name), f"metricmap 未定义 {name}")
    return b.done("渲染函数签名与 harness 导出面均未变")


# ================================================================ 9 env: 跨进程等价
_PARITY_SAMPLE = {
    "base_url": "env:EVAL_SUBJECT_BASE_URL",
    "api_key": "env:EVAL_SUBJECT_API_KEY",
    "plain": "http://127.0.0.1:8000/v1",
    "nested": {"list": ["env:EVAL_SUBJECT_BASE_URL", 1, None, True],
               "deep": {"x": "env:__SELFTEST_UNSET_VAR__"}},
    "not_env": "env_var_without_colon_marker",
    "empty": "",
}

_PARITY_CHILD = r"""
import json, os, sys
sys.path.insert(0, %(eval_dir)r)
import run_eval
sample = json.loads(sys.argv[1])
sentinel = os.environ["EVAL_SUBJECT_BASE_URL"]
print(json.dumps({
    "dotenv_before": sentinel,
    "dotenv_after": (run_eval._load_dotenv(), os.environ.get("EVAL_SUBJECT_BASE_URL"))[1],
    "resolve_env": run_eval._resolve_env(sample),
}, ensure_ascii=False))
"""


@check("env-parity", "env: 解析：ui/config.py 与 run_eval.py 逐键一致")
def check_env_parity() -> Outcome:
    """跨进程真跑 CLI 的实现，而不是读它的源码做文本比对。

    两侧都按同一顺序执行：设置哨兵 → 装载 .env → 解析。顺序一致才能比较，
    因为 `_load_dotenv` 本身会改变 `os.environ`。
    """
    b = Bag()
    sentinel = "SELFTEST-BASE-URL-SENTINEL"
    script = _PARITY_CHILD % {"eval_dir": str(paths.EVAL_DIR)}
    child_env = dict(os.environ)
    child_env["EVAL_SUBJECT_BASE_URL"] = sentinel
    child_env.pop("__SELFTEST_UNSET_VAR__", None)

    try:
        proc = subprocess.run(
            [str(cli.harness_python()), "-X", "utf8", "-c", script,
             json.dumps(_PARITY_SAMPLE)],
            capture_output=True, text=True, timeout=180, env=child_env, cwd=str(paths.EVAL_DIR),
        )
    except Exception as exc:  # noqa: BLE001
        return Outcome(SKIP, f"无法启动 harness 解释器：{type(exc).__name__}: {exc}")

    if proc.returncode != 0:
        reason = [ln for ln in (proc.stderr or "").strip().splitlines() if ln.strip()]
        tail = reason[-1] if reason else "(无 stderr)"
        return Outcome(SKIP, f"子进程未能导入 run_eval（多半是缺 pandas）：{tail}")

    try:
        got = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        return Outcome(SKIP, f"子进程输出无法解析：{type(exc).__name__}: {exc}；stdout={proc.stdout[:200]!r}")

    # 本进程用 ui/config.py 的实现走同一顺序
    saved = dict(os.environ)
    try:
        os.environ["EVAL_SUBJECT_BASE_URL"] = sentinel
        os.environ.pop("__SELFTEST_UNSET_VAR__", None)
        config._load_dotenv()
        mine_after = os.environ.get("EVAL_SUBJECT_BASE_URL")
        want = config.resolve_env(_PARITY_SAMPLE)
    finally:
        os.environ.clear()
        os.environ.update(saved)

    b.eq(want, got["resolve_env"], "两侧的 _resolve_env 输出")
    b.eq(want["nested"]["deep"]["x"], "",
         "未设置的 env: 变量应解析为空串而不是保留原样")
    b.eq(want["plain"], _PARITY_SAMPLE["plain"], "非 env: 的字符串应原样返回")
    b.eq(want["not_env"], _PARITY_SAMPLE["not_env"], "只以 env: 开头的才解析")
    b.eq(got["dotenv_after"], sentinel,
         "run_eval._load_dotenv 覆盖了已存在的变量（应为 setdefault 语义）")
    b.eq(mine_after, sentinel,
         "ui/config._load_dotenv 覆盖了已存在的变量（应为 setdefault 语义）")

    quoted = ".env 中带引号的值"
    b.note(f"子进程真实导入 run_eval；{quoted} 两侧去引号写法不同但结果一致")
    return b.done("两侧对同一输入的解析结果逐键相等，且都不覆盖已存在的变量")


# ================================================================ 10 逐页渲染：哨兵不外泄 + 内联 HTML 良构
# 页面自产的 HTML 容器类名。切页循环里顺带把它们抓出来做一次标签配平校验：
# 内联 HTML 写坏了不会抛异常——漏一个 </div> 只会让后面的内容渲染成一块裸文本，
# 静态检查也看不出来，而矮一个标签的代价是整页排版塌掉。合在这里而不是单开一项，
# 是因为要用到同一个逐页渲染的循环；再跑一遍 AppTest 要几分钟。
_OWN_HTML = {"eb-summary-grid": "概括卡四联", "eb-page-head": "页头",
             "eb-brand": "侧栏品牌块", "eb-sidebar-note": "侧栏说明块", "eb-tag": "状态小标签"}

# HTML 的空元素：合法，但不是 XML 的良构形式（`<br>` 没有结束标签）。
# 校验前补成自闭合，否则不会报配平问题而是报 mismatched tag，等于平凡失败。
_VOID_TAGS = ("br", "hr", "img", "input", "meta", "link", "source", "col")

_SECRETS_CHILD = r"""
import json, os, re, sys, xml.etree.ElementTree as ET
sys.path.insert(0, %(ui_dir)r)
sentinel = os.environ["UI_SELFTEST_SENTINEL"]
own = %(own_html)r
void = %(void_tags)r
out = {"sentinel_len": len(sentinel), "leaked": [], "rendered": 0, "control": False,
       "env_surface": False, "pages": [], "nav_missing": [], "error": "",
       "html_counts": {}, "html_bad": []}
try:
    import config, health
    from streamlit.testing.v1 import AppTest

    # 正向对照：控制台自己的体检应看到该密钥“已设置”。看不到就说明哨兵没生效，
    # 那么“页面里没有哨兵”这条断言就是平凡成立的，不能算通过。
    for c in health.run_local():
        if c.key == "env_vars" and c.rows:
            out["control"] = any("已解析" in str(r.get("状态", "")) for r in c.rows)

    at = AppTest.from_file(%(app_path)r, default_timeout=300)
    at.run()

    # 检索面必须走 SerializeToString 而不是 str(proto)：st.dataframe 的内容经
    # Arrow 传输，protobuf 的 bytes 字段在 str() 下会被十六进制转义，表格里的中文
    # 与 ASCII 都会变成 \346\234... 这样的转义序列，搜不到。SerializeToString 后按
    # utf-8 宽松解码，字符串字段与 Arrow 缓冲区里的文本都是可搜的原文。
    def dump(elem):
        proto = getattr(elem, "proto", None)
        if proto is None:
            return ""
        try:
            return proto.SerializeToString().decode("utf-8", errors="replace")
        except Exception:
            return str(proto)

    seen, chunks = set(), []
    def walk(block):
        for _typ, elems in (getattr(block, "children", {}) or {}).items():
            for el in elems:
                if id(el) in seen:
                    continue
                seen.add(id(el))
                chunks.append(dump(el))
                walk(el)

    def harvest():
        walk(at.main)
        try:
            walk(at.sidebar)
        except Exception:
            pass
        # 展开器是独立 Block，其子元素不在 main 的 children 里，需要单独立根。
        for exp in at.expander:
            chunks.append(dump(exp))
            walk(exp)
        for attr in dir(at):
            if attr.startswith("_"):
                continue
            try:
                val = getattr(at, attr)
            except Exception:
                continue
            if isinstance(val, list) and val and hasattr(val[0], "proto"):
                for el in val:
                    if id(el) not in seen:
                        seen.add(id(el))
                        chunks.append(dump(el))

    # 导航改为侧栏 radio 后，一次 run 只渲染选中页。只渲染首屏会让“没搜到哨兵”
    # 变成平凡结论，故逐项切页并累积渲染文本。定位靠 sidebar 容器内的 radio：
    # 主区还有一个 run_mode radio，直接取 at.radio[0] 会取错。
    nav = at.sidebar.radio[0]
    options = list(nav.options)
    out["nav_missing"] = [o for o in %(nav_labels)r if o not in options]
    if not options:
        raise RuntimeError("侧栏没有导航 radio，页面结构可能已变")
    for opt in options:
        nav.set_value(opt).run()
        nav = at.sidebar.radio[0]      # 每次 run 后元素树是新对象，必须重取
        harvest()
        out["pages"].append(opt)

    text = "\n".join(chunks)
    out["rendered"] = len(text)

    # ---- 内联 HTML 结构校验（与哨兵检索共用上面这一遍逐页渲染）------------
    # 从页面自产的容器类名出发，整页扫一趟，按同层标签配平截出每个容器，再交给
    # XML 解析器。两个坑：
    #  * 朴素的"截到第一个闭合标签"不行——容器互相嵌套，会被内层提前截断，截出来
    #    的片段反而总是良构的，校验退化成恒真；
    #  * 计数必须从**容器的开标签本身**算起。从标签内部起算，第一个 </div> 就会把
    #    深度打到 -1 且再也回不到 0，于是每个片段都截到全文末尾，把后面的 protobuf
    #    字节一起喂给解析器，报一堆 "junk after document element" 假问题。
    # 单趟扫描而不是"每个类名各扫一遍"：后者对同一个 blob 做 5 次 O(n) 扫描，且
    # 同一个坑会被各扫各的报 5 遍。
    # 判定「从 < 到 > 的这一段是不是标签」。报告正文里有 `值 < 0.5 或处于…`
    # 这样的比较式；若只按「< 后面跟字母数字」判，`< 0.5` 会被当成开标签压进
    # 栈，再配掉一个真的 </div>，此后所有标签全错位——报出来的还是「多余的
    # 闭合标签」，离病因很远。故要求三条同时成立：标签名以字母起头；名字后面
    # 紧跟空白 / `/` / `>`；属性段内不再出现 `<` 或 `>`，且引号成对。
    tag_re = re.compile(r"^<\s*/?\s*([A-Za-z][A-Za-z0-9]*)(?:\s[^<>]*?)?\s*/?>$")

    def cls_of(tok):
        m = re.search(r'class="([^"]*)"', tok)
        if not m:
            return None
        for c in own:
            if re.search(r"\b%%s\b" %% re.escape(c), m.group(1)):
                return c
        return None

    def spans(blob):
        # 返回 (类名 → 完整片段, 类名 → 出现次数, 配对异常列表)。
        found = {c: [] for c in own}
        counts = {c: 0 for c in own}
        bad: list[str] = []
        stack: list[tuple[int, Optional[str]]] = []   # (开标签起点, 类名)
        i = 0
        while i < len(blob):
            nxt = blob.find("<", i)
            if nxt < 0:
                break
            if blob.startswith("<!--", nxt):
                # 注释不是 DOM 的一部分：里面的 `<div>` 不该参与配平。不跳过去，
                # 注释里的标签就会被当成真的开标签，最后报「未闭合」。
                close = blob.find("-->", nxt + 4)
                i = len(blob) if close < 0 else close + 3
                continue
            end = blob.find(">", nxt)
            if end < 0:
                break
            tok = blob[nxt:end + 1]
            m = tag_re.match(tok) if tok.count('"') %% 2 == 0 else None
            if m is None:
                # 不是标签就只跳过这一个 `<`，不要一步跨到 `>`：跨过的那段里
                # 可能还嵌着真标签（`a < b` 与下一个标签的 `>` 之间）。
                i = nxt + 1
                continue
            if tok.startswith("</"):
                if stack:
                    start, cls = stack.pop()
                    if cls:
                        found[cls].append(blob[start:end + 1])
                else:
                    bad.append("多余的闭合标签 %%r" %% blob[max(0, nxt - 60):end + 1])
            elif tok.endswith("/>"):
                pass                       # 自闭合，不计深度
            elif m.group(1).lower() in void:
                pass                       # 空元素没有结束标签，计深度就永远配不平
            else:
                cls = cls_of(tok)
                stack.append((nxt, cls))
                if cls:
                    counts[cls] += 1
            i = end + 1
        for start, cls in stack:
            bad.append("未闭合的开标签 %%r" %% blob[start:start + 120])
        return found, counts, bad

    def check_html(blob):
        # 两步都做，各自能盖住的缺陷不同：整页配对抓"两个容器共用一个闭合"，
        # 单容器良构抓"容器内多一个 </div>"（多出的闭合会提前收掉外层容器，
        # 而外层看上去仍然配平）。只做任一步都会漏一类。
        found, counts, bad = spans(blob)
        out["html_counts"] = counts
        for why in bad:
            out["html_bad"].append(f"整页配对异常：{why}")
        for cls, label in own.items():
            for frag in found[cls]:
                norm = re.sub(r"<(%%s)(\s[^>]*)?>" %% "|".join(void), r"<\1\2/>", frag)
                try:
                    ET.fromstring(norm)
                except ET.ParseError as exc:
                    out["html_bad"].append(f"{label}（{cls}）标签不配平：{exc}；片段 {frag[:160]!r}")

    # 在**拼接后的全文**上校验：chunk 边界是 protobuf 元素边界而不是 HTML 边界，
    # 一块多出的 `</div>` 会被下一块的开标签"补平"，逐块看反而看不见。
    check_html(text)

    # 检索面是否覆盖到"密钥会出现在哪里"。env_vars 体检表的每一行都带着密钥变量名
    # 与解析状态，是页面上最可能泄露密钥值的位置；该表的文字若不在检索面里，
    # "没搜到哨兵"就不能说明什么。用不依赖 .env 是否存在的两种措辞之一来判。
    out["env_surface"] = ("已解析为" in text) or ("未设置或为空" in text)
    if sentinel in text:
        for i, ch in enumerate(chunks):
            if sentinel in ch:
                out["leaked"].append(ch[max(0, ch.index(sentinel) - 120): ch.index(sentinel) + 120])
except Exception as exc:
    out["error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(out, ensure_ascii=False))
"""


@check("secrets", "逐页渲染：哨兵值不外泄，且内联 HTML 良构")
def check_secrets() -> Outcome:
    import secrets as _pysecrets
    sentinel = "SENTINEL_" + _pysecrets.token_hex(8)
    env = dict(os.environ)
    env["UI_SELFTEST_SENTINEL"] = sentinel
    env["EVAL_SUBJECT_API_KEY"] = sentinel
    env.pop("EVAL_SUBJECT_BASE_URL", None)   # 让 base_url 走 .env，避免误判为泄露
    # 口令门已移除（设计 §7.1 的 2026-09-22 变更），不再注入 UI_PASSWORD。
    env.pop("UI_PASSWORD", None)

    script = _SECRETS_CHILD % {"ui_dir": str(paths.UI_DIR),
                               "app_path": str(paths.UI_DIR / "app.py"),
                               "nav_labels": _nav_labels(),
                               "own_html": _OWN_HTML,
                               "void_tags": _VOID_TAGS}
    if not _nav_labels():
        return Outcome(SKIP, "未能从 app.py 静态取到 NAV_ITEMS，本次检查不成立")
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", script],
            capture_output=True, text=True, timeout=900, env=env, cwd=str(paths.UI_DIR),
        )
    except Exception as exc:  # noqa: BLE001
        return Outcome(SKIP, f"无法启动子进程：{type(exc).__name__}: {exc}")

    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        return Outcome(SKIP, f"子进程失败：{tail[-1] if tail else '(无 stderr)'}")

    try:
        got = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        return Outcome(SKIP, f"子进程输出无法解析：{type(exc).__name__}: {exc}；stdout={proc.stdout[:300]!r}")

    if got.get("error"):
        return Outcome(SKIP, f"AppTest 未跑通：{got['error']}")

    b = Bag()
    b.true(got.get("control"), "正向对照失败：体检未看到哨兵密钥已设置，本次检查不成立")
    b.true(got.get("env_surface"), "检索面未覆盖 env_vars 体检表，本次检查不成立")
    b.eq(got.get("nav_missing"), [], "侧栏导航缺失的页面")
    b.eq(got.get("leaked"), [], "渲染元素中出现的哨兵片段")
    # 页面自产的容器必须都真的出现过，否则下面的结构校验是空转。计数下限取 1，
    # 只有「一个都没找到」才算异常——具体数目随页面改版变动，钉死会天天红。
    counts = got.get("html_counts") or {}
    missing_cls = [c for c in _OWN_HTML if not counts.get(c)]
    b.eq(missing_cls, [], "逐页渲染中未出现的自产容器（结构校验未生效）")
    b.eq(got.get("html_bad"), [], "内联 HTML 标签不配平的片段")
    # 导航改为侧栏 radio 后，每次 run 只渲染选中页，故逐项切页累积。累积后的
    # 渲染文本应显著大于单页（首屏实测约 3 万字符）；阈值取 5 万，低于此说明
    # 切页没生效，"没搜到哨兵"就会变成平凡结论。
    b.true(got.get("rendered", 0) > 50000, f"渲染文本量异常（{got.get('rendered')} 字符）")
    b.note(f"逐项切换 {len(got.get('pages') or [])} 个页面，"
           f"累积 {got.get('rendered')} 字符渲染文本中检索哨兵")
    b.note(f"同一次渲染中校验 {sum(counts.values())} 处自产容器的标签配平")
    return b.done(f"哨兵未出现在 {got['rendered']} 字符渲染文本中，"
                  f"检索面含 env_vars 体检表；{sum(counts.values())} 处内联 HTML 结构良构")


# ================================================================ 主入口
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ElderBench 控制台自测")
    ap.add_argument("-k", "--only", default="", help="只跑名字含该子串的检查项")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印每项的完整结论")
    args = ap.parse_args(argv)

    selected = [(n, t, f) for n, t, f in _REGISTRY if args.only in n]
    if not selected:
        print(f"没有匹配 {args.only!r} 的检查项。可用：{'、'.join(n for n, _, _ in _REGISTRY)}")
        return 1

    print(f"ElderBench 控制台自测 · {len(selected)} 项 · python {sys.version.split()[0]}")
    print(f"仓库根 {paths.REPO_ROOT}")
    if cli.harness_python_is_fallback():
        print(f"注意：未找到 harness 的解释器，将回退到 {cli.harness_python()}；"
              "依赖 harness 环境的检查项会 SKIP。")
    print("-" * 72)

    tally = {PASS: 0, FAIL: 0, SKIP: 0}
    failures: list[str] = []
    for name, title, fn in selected:
        try:
            out = fn()
        except Exception:  # noqa: BLE001
            out = Outcome(FAIL, "检查项自身抛异常：\n" + traceback.format_exc(limit=4))
        tally[out.status] += 1
        line = f"{_SYMBOL[out.status]} {name:<16} {title}"
        print(line)
        note = out.note.strip()
        if note:
            if out.status is FAIL or args.verbose:
                for ln in note.splitlines():
                    print(f"    {ln}")
            else:
                print(f"    {note.splitlines()[0]}")
        if out.status is FAIL:
            failures.append(name)

    print("-" * 72)
    print(f"通过 {tally[PASS]} · 失败 {tally[FAIL]} · 跳过 {tally[SKIP]}")
    if failures:
        print(f"未通过：{'、'.join(failures)}")
    return 1 if tally[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())

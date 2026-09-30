"""子命令的参数构造与执行。设计 §4.1 与 §5.3。

**本模块是界面与 CLI 之间唯一的接缝，也是 argv 的唯一来源。**
界面上显示的"等价命令行"与真正传给 subprocess 的 argv 由同一个 `Command` 对象派生
（`Command.argv` 用于执行，`Command.display` 由 `shlex.join(argv)` 得到）。
不得出现一处手写、一处拼接：一旦分叉，用户在界面出问题时就无法靠复制命令自行复现。

有副作用的子命令一律走本模块的子进程路径，不 import `run_eval` 的函数（设计 §4.1）。
"""
from __future__ import annotations

import ast
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import config as config_mod
from paths import DATA_DIR, EVAL_DIR, REPO_ROOT, RUNS_DIR

RUN_EVAL = "run_eval.py"
BATCH_SCRIPT = "scripts/run_suite_all.sh"


# ---------------------------------------------------------------- 解释器
# venv 布局随创建平台而变，且两份候选**不能互为兜底**。
#
# 仓库放在 SMB 盘上，Linux 建的 `.venv/bin/python` 在 Windows 上 `exists()` 为真
# （文件确实在共享里），但它是一个 ELF 可执行文件，Windows 无法执行。若在 Windows
# 上把它列为兜底候选，它会永远被选中，于是每次子进程调用都报"不是有效的 Win32
# 应用程序"，而 `harness_python_is_fallback()` 还会因为"找到了"而返回 False，
# 把真实问题藏起来。故这里按平台各列一份，找不到就诚实回落到当前解释器。
_VENV_CANDIDATES = (
    (".venv/Scripts/python.exe",) if os.name == "nt" else
    (".venv/bin/python", ".venv/bin/python3")
)


def harness_python() -> Path:
    """harness 侧的解释器。设计 §8.1：显式指定，不依赖 PATH。

    优先级：环境变量 UI_HARNESS_PY > 仓库内 .venv（按当前平台选布局）
    > 当前解释器。回落到当前解释器时调用方应给出提示：本地开发环境通常没有
    jieba / nltk / pandas，跑真实子命令会失败。
    """
    env = os.environ.get("UI_HARNESS_PY")
    if env:
        return Path(env)
    for rel in _VENV_CANDIDATES:
        p = REPO_ROOT / rel
        if p.exists():
            return p
    return Path(sys.executable)


def harness_python_is_fallback() -> bool:
    """是否在用当前解释器兜底（界面据此提示「未找到 .venv」）。"""
    return harness_python() == Path(sys.executable)


# ---------------------------------------------------------------- 取值来源
def available_suites() -> list[str]:
    """现有任务文件的卷名。设计 §4.6：不从 adapters.SUITE_BUILDERS 取（会拖进 pandas）。

    回答的是"现在能跑哪些"，而 CLI 的 `choices=SUITES` 回答的是"允许哪些"。
    两者的差集即"需先 build"。
    """
    if not DATA_DIR.exists():
        return []
    return sorted(p.stem[len("tasks_"):] for p in DATA_DIR.glob("tasks_*.jsonl"))


def _version_constants() -> dict[str, Any]:
    """用 AST 解析 `eval/versions.py` 的模块级字面量赋值。

    为什么不 import：控制台进程的 `sys.path` 里没有 `eval/`，而为了读几个字符串
    去改 `sys.path` 会把整个 harness 的模块命名空间拉进控制台进程，违背设计 §4.6
    的依赖边界。为什么不正则：正则只认一种写法，换行、引号、尾随逗号或注释一变就
    静默失效；AST 解析对这些都不敏感，且解析目标是一个只依赖标准库的常量模块
    （`eval/versions.py`），不再是有第三方 import 的 `scoring.py`。
    """
    src = EVAL_DIR / "versions.py"
    if not src.exists():
        return {}
    try:
        tree = ast.parse(src.read_text(encoding="utf-8"))
    except SyntaxError:
        return {}
    out: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            try:
                out[target.id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return out


def scorer_versions() -> tuple[str, ...]:
    """`versions.SCORER_VERSIONS`，供界面的口径下拉使用。

    解析不到时返回空元组，由界面提示「版本列表不可得」，而不是静默给出一个
    可能不全的下拉。正解是给 CLI 加一个只读的 `versions` 子命令（设计 §5.3
    选项 1）；当前不做，因为那会扩控制台的命令面，而单一来源模块已经消除了
    原先「正则抠 `scoring.py` 源码」这一脆弱点。
    """
    vals = _version_constants().get("SCORER_VERSIONS")
    if not isinstance(vals, (tuple, list)) or not all(isinstance(v, str) for v in vals):
        return ()
    return tuple(vals)


def known_run_ids() -> list[str]:
    if not RUNS_DIR.exists():
        return []
    return sorted((p.stem for p in RUNS_DIR.glob("*.json")), reverse=True)


# ---------------------------------------------------------------- 命令对象
@dataclass
class Command:
    """一个待执行的命令。argv 与 display 同源，见模块 docstring。"""

    kind: str
    argv: list[str]
    cwd: Path
    env_note: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        """等价命令行。`shlex.join` 保证含空格/中文的参数被正确引用。"""
        return f"cd {shlex.quote(str(self.cwd))} && {shlex.join(self.argv)}"

    @property
    def runnable(self) -> bool:
        return not self.errors

    def explain(self) -> str:
        return "；".join(self.errors)


def _py_cmd(*args: str) -> list[str]:
    """以 harness 解释器执行 eval/ 下的 run_eval.py。`-X utf8` 为既有约定。"""
    return [str(harness_python()), "-X", "utf8", RUN_EVAL, *args]


def _opt(flag: str, value: Any) -> list[str]:
    """追加可选参数；None 与空串不追加（与 argparse 的默认值语义一致）。"""
    if value is None or value == "" or value is False:
        return []
    if value is True:
        return [flag]
    return [flag, str(value)]


# ---------------------------------------------------------------- 各子命令
def cmd_build(suites: Optional[list[str]] = None, *, config: Optional[str] = None) -> Command:
    # --suites 是 nargs="+"，每个卷一个独立 argv 元素，不能拼成一个字符串。
    argv = _py_cmd("build", "--suites", *suites) if suites else _py_cmd("build")
    argv += _opt("--config", config)
    return Command("build", argv, EVAL_DIR)


def cmd_smoke(*, config: Optional[str] = None) -> Command:
    return Command("smoke", _py_cmd("smoke", *_opt("--config", config)), EVAL_DIR)


def _subject_config(subject: str, config: Optional[str]) -> Optional[str]:
    return config if config is not None else config_mod.config_file_for_subject(subject)


def cmd_run(suite: str, subject: str, *, limit: Optional[int] = None,
            concurrency: Optional[int] = None, config: Optional[str] = None) -> Command:
    config = _subject_config(subject, config)
    argv = _py_cmd("run", "--suite", suite, "--subject", subject)
    argv += _opt("--limit", limit)
    argv += _opt("--concurrency", concurrency)
    argv += _opt("--config", config)
    c = Command("run", argv, EVAL_DIR)
    if suite not in available_suites():
        c.errors.append(
            f"卷 {suite} 的任务文件不存在（eval/data/tasks_{suite}.jsonl）。"
            "run 会自动触发 build，而服务器 .venv 缺 pyarrow / openpyxl 时会失败，"
            "请先在本机 build 并同步 data/。")
    if AVAILABLE_SUBJECTS and subject not in AVAILABLE_SUBJECTS:
        c.errors.append(f"subject {subject} 不在活动配置 profile 的 subjects 中")
    return c


def cmd_batch(subject: str, *, concurrency: int = 16,
              config: Optional[str] = None) -> Command:
    """全量 10 卷批次。设计 §4.4：复用 run_suite_all.sh，不自己写循环。"""
    config = _subject_config(subject, config)
    argv = ["bash", BATCH_SCRIPT, subject, str(concurrency)]
    if config:
        argv.append(config)
    c = Command("run_batch", argv, REPO_ROOT)
    if not (REPO_ROOT / BATCH_SCRIPT).exists():
        c.errors.append(f"批次脚本不存在：{BATCH_SCRIPT}")
    if AVAILABLE_SUBJECTS and subject not in AVAILABLE_SUBJECTS:
        c.errors.append(f"subject {subject} 不在活动配置 profile 的 subjects 中")
    return c


def cmd_report(subject: str, *, run: Optional[str] = None,
               config: Optional[str] = None) -> Command:
    config = _subject_config(subject, config)
    argv = _py_cmd("report", "--subject", subject)
    argv += _opt("--run", run)
    argv += _opt("--config", config)
    return Command("report", argv, EVAL_DIR)


def cmd_rescore(subject: str, *, suites: Optional[list[str]] = None,
                scorer_version: Optional[str] = None, check: Optional[str] = None,
                tol: Optional[float] = None, config: Optional[str] = None) -> Command:
    config = _subject_config(subject, config)
    argv = _py_cmd("rescore", "--subject", subject)
    if suites:
        argv += ["--suite", *suites]
    argv += _opt("--scorer-version", scorer_version)
    argv += _opt("--check", check)
    argv += _opt("--tol", tol)
    argv += _opt("--config", config)
    c = Command("rescore", argv, EVAL_DIR)
    if scorer_version:
        known = scorer_versions()
        if not known:
            c.errors.append("无法从 eval/versions.py 读到 SCORER_VERSIONS，选择口径前请确认该文件形态")
        elif scorer_version not in known:
            c.errors.append(f"scorer_version {scorer_version} 不在 {list(known)} 中")
    return c


def cmd_rulecheck(*, baseline: bool = False) -> Command:
    return Command("rulecheck", _py_cmd("rulecheck", *_opt("--baseline", baseline)), EVAL_DIR)


def cmd_compare(subjects: list[str], *, run: Optional[str] = None,
                config: Optional[str] = None) -> Command:
    argv = _py_cmd("compare", "--subjects", *subjects)
    argv += _opt("--run", run)
    argv += _opt("--config", config)
    c = Command("compare", argv, EVAL_DIR)
    if len(subjects) < 1:
        c.errors.append("至少需要一个 subject")
    if run and run not in known_run_ids():
        c.errors.append(f"run_id {run} 不在 eval/runs/ 中")
    return c


def cmd_export_badcase(subject: str, suite: str, *, criterion: Optional[str] = None,
                       n: int = 40, seed: int = 20260921, run: Optional[str] = None,
                       out: Optional[str] = None) -> Command:
    argv = _py_cmd("export-badcase", "--subject", subject, "--suite", suite)
    argv += _opt("--criterion", criterion)
    argv += ["--n", str(n), "--seed", str(seed)]
    argv += _opt("--run", run)
    argv += _opt("--out", out)
    return Command("export-badcase", argv, EVAL_DIR)


def cmd_verify(run: str) -> Command:
    c = Command("verify", _py_cmd("verify", "--run", run), EVAL_DIR)
    if run not in known_run_ids():
        c.errors.append(f"run_id {run} 不在 eval/runs/ 中")
    return c


def cmd_manifest(subject: str, *, config: Optional[str] = None) -> Command:
    config = _subject_config(subject, config)
    argv = _py_cmd("manifest", "--subject", subject)
    argv += _opt("--config", config)
    return Command("manifest", argv, EVAL_DIR)


def cmd_audit() -> Command:
    return Command("audit", _py_cmd("audit"), EVAL_DIR)


# ---------------------------------------------------------------- 执行
@dataclass
class Result:
    ok: bool
    returncode: int
    stdout: str
    stderr: str

    @property
    def text(self) -> str:
        if self.stderr.strip():
            return f"{self.stdout}\n[stderr]\n{self.stderr}" if self.stdout.strip() else self.stderr
        return self.stdout


def run_sync(cmd: Command, *, timeout: float = 300.0) -> Result:
    """同步执行一个命令，捕获输出。用于短命令（report / rescore / verify 等）。

    `text=True` + 显式 encoding：Windows 版 python 的 stdout 是 CRLF，
    用 shell 的 `$(...)` 捕获会带 `\\r` 污染路径（regression_rescore.sh:32 记录过这个坑）。
    从 Python 侧调用没有这个问题，但必须显式指定 encoding，否则会按 locale 解码而乱码。
    """
    if not cmd.runnable:
        return Result(False, -1, "", f"命令不可执行：{cmd.explain()}")
    try:
        p = subprocess.run(cmd.argv, cwd=str(cmd.cwd), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result(False, -2, "", f"超时（{timeout:.0f}s）：{cmd.display}")
    except OSError as e:
        return Result(False, -3, "", f"无法启动子进程：{e!r}\n命令：{cmd.display}")
    return Result(p.returncode == 0, p.returncode, p.stdout or "", p.stderr or "")


# subject 名单在界面启动时由 artifacts 填充，避免本模块反向依赖 artifacts。
AVAILABLE_SUBJECTS: list[str] = []


def set_available_subjects(names: list[str]) -> None:
    global AVAILABLE_SUBJECTS
    AVAILABLE_SUBJECTS = list(names)

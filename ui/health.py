"""配置体检。设计 §5.2。

这里回答的是"现在点下 run，会在哪里失败"。七项检查全部对应**现有流程要到跑批中途
或事后才暴露**的问题：人格文件缺失要到 `run` 读文件时才报错，`env:` 未设置要到
收到 401 才知道，端点不可达要等首条请求超时，卷没跑完要等读报告时才发现。

## 探针的纪律（设计 §5.2）

`GET <base_url>/models` 与本地端口探测**必须由用户显式触发**，不在页面加载时自动发起。
原因：自动发起会把一个只读的配置页变成一个隐形的探测/扫描器，而且对 `glm53_flash`
这类远端网关会按页面刷新次数产生请求。故探针函数（`probe_endpoint`、`check_local_endpoint`）
不被 `run_all()` 调用，只在用户点击时单独调用。

探针是**服务发现**，不是推理调用，不违反「判分零模型调用」的纪律：该纪律约束的是
`scoring` 侧不得调用模型，不约束控制台做一次 `/models` 查询。

## 不发散：探针只用配置里已有的凭据

`probe_endpoint` 只对该 subject 所在配置 profile 声明的 `base_url` 发起请求，
不接收任意 URL 参数。界面因此无法被用作对任意主机的探测器。
"""
from __future__ import annotations

import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from paths import DATA_DIR
import artifacts
import cli
import config

# 状态：ok 通过 / warn 需要处理但非致命 / fail 会导致跑批失败 / skip 本机不适用
OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
# 状态的中文短名。模块级而不是 `Check` 的属性：以状态字符串为键的汇总
# （`overview`）也要用它，那里没有 `Check` 实例可取。
STATUS_SYMBOL = {OK: "通过", WARN: "注意", FAIL: "未通过", SKIP: "不适用"}

_STATUS_ORDER = {FAIL: 0, WARN: 1, OK: 2, SKIP: 3}


@dataclass
class Check:
    """一项体检结果。rows 承载逐 subject / 逐卷的明细。"""

    key: str
    title: str
    status: str
    detail: str
    remedy: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def symbol(self) -> str:
        return STATUS_SYMBOL[self.status]


# ---------------------------------------------------------------- 1 人格文件
def check_system_prompt() -> Check:
    """判据：`system_prompt_file` 指向的文件存在且非空。

    现有暴露时机是 `run` 启动时读文件失败（对应 manifest 的 `sha256_file` 返回 None）。
    额外检查非空：空文件能通过存在性检查，但注入一个空 system prompt 与不注入
    是两种不同的推理条件，会让同组可比性失去意义。
    """
    rows, worst = [], OK
    for v in config.subject_views():
        if not v.system_prompt_file:
            rows.append({"subject": v.name, "文件": "（未声明）", "状态": "不适用"})
            continue
        p = Path(v.system_prompt_file)
        p = p if p.is_absolute() else Path(config.EVAL_DIR) / p
        if not p.exists():
            st, note = FAIL, "文件不存在"
            worst = FAIL
        elif p.stat().st_size == 0:
            st, note = WARN, "文件为空（等于注入了空 prompt）"
            worst = WARN if worst == OK else worst
        else:
            st, note = OK, f"{p.stat().st_size} 字节"
        rows.append({"subject": v.name, "文件": v.system_prompt_file, "状态": f"{st} {note}"})
    n_used = sum(1 for r in rows if r["状态"] != "不适用")
    detail = (f"{n_used} 个 subject 声明了人格文件。" if n_used
              else "没有 subject 声明人格文件，本项不适用。")
    if n_used == 0:
        worst = SKIP
    return Check("system_prompt", "人格文件是否存在", worst, detail,
                 "缺失的文件需在仓库内补齐；其 sha256 会进 manifest，改动会使 verify 报「已变化」",
                 rows)


# ---------------------------------------------------------------- 2 env 变量
def check_env_vars() -> Check:
    """判据：声明为 `env:VAR` 的字段解析后是否为空串。"""
    rows, worst = [], OK
    for v in config.subject_views():
        if not v.base_url_raw.startswith("env:"):
            continue
        var = v.base_url_raw.split(":", 1)[1]
        if v.env_broken:
            worst = FAIL
            rows.append({"subject": v.name, "变量": var, "状态": f"{FAIL} 未设置或为空"})
        else:
            rows.append({"subject": v.name, "变量": var, "状态": f"{OK} 已解析为 {v.base_url}"})
    if not rows:
        worst = SKIP
        detail = "没有 subject 使用 env: 引用。"
    else:
        n_bad = sum(1 for r in rows if r["状态"].startswith(FAIL))
        detail = (f"{len(rows)} 个 subject 使用 env: 引用，其中 {n_bad} 个解析为空。"
                  if n_bad else f"{len(rows)} 个 subject 的 env: 引用均已解析。")
    return Check("env_vars", "env: 变量是否已设置", worst, detail,
                 "在 eval/.env 中设置对应变量（该文件已 gitignore，界面不会回显其值）", rows)


# ---------------------------------------------------------------- 3 同组解码一致
def check_group_decoding() -> Check:
    """判据：`reporting.group_subjects().comparable`。

    复用 harness 侧实现而非界面自算，保证与 `compare` 报告的分组结论同源。
    """
    groups = config.subject_groups()
    rows, worst = [], OK
    for g, info in groups.items():
        ok = bool(info.get("comparable"))
        if not ok:
            worst = WARN
        d = info.get("decoding") or {}
        cond = info.get("condition") or {}
        prompt_hash = (cond.get("system_prompt_sha256") or "none")[:10]
        rows.append({
            "组": g, "成员": "、".join(info["subjects"]),
            "解码": (f"temperature={d.get('temperature')}, max_tokens={d.get('max_tokens')}, "
                    f"top_p={d.get('top_p')}, seed={d.get('seed')}"),
            "prompt": f"{cond.get('system_prompt_file') or 'none'} ({prompt_hash})",
            "状态": f"{OK} 可直接比较" if ok else f"{WARN} 不可直接比较",
        })
    n_bad = sum(1 for r in rows if r["状态"].startswith(WARN))
    detail = (f"{len(groups)} 个组，其中 {n_bad} 组内完整可比条件不一致。"
              if n_bad else f"{len(groups)} 个组内完整可比条件一致，可组内直接比较。")
    return Check("group_decoding", "同组可比条件是否一致", worst, detail,
                 "校验 temperature/max_tokens/top_p/seed/惩罚/stop/extra_body 和 prompt 哈希", rows)


# ---------------------------------------------------------------- 4 卷任务文件
def expected_suites() -> list[str]:
    """应当存在的卷名。

    取自 `config.yaml` 的 `sampling.suites` 键：它是配置层对"本套评测包含哪些卷"
    的权威声明，且已逐卷给出采样配额。这样界面既不必硬编码卷名，也不必 import
    `adapters.SUITE_BUILDERS`（会拖进 pandas，设计 §4.6）。
    已核对：与全部 report 的 `suites_included` 一致（见 selftest）。
    """
    return sorted((config.sampling().get("suites") or {}).keys())


def check_suite_tasks() -> Check:
    """判据：`eval/data/tasks_<卷>.jsonl` 存在且行数等于配置的采样配额。

    行数这条不在设计 §5.2 的七项之内，但同一份文件已读，顺带核一次：配额与
    实际任务数不符时，报告里的 n 会无声地小于配置声明值。
    """
    expected = expected_suites()
    quotas = config.sampling().get("suites") or {}
    rows, worst = [], OK
    for s in expected:
        p = DATA_DIR / f"tasks_{s}.jsonl"
        quota = quotas.get(s)
        quota_n = _quota_total(quota)
        if not p.exists():
            worst = WARN
            rows.append({"卷": s, "任务文件": "缺失", "配额": quota_n, "状态": f"{WARN} 需先 build"})
            continue
        got = artifacts.count_lines(p)
        if quota_n is not None and got != quota_n:
            worst = WARN if worst == OK else worst
            st = f"{WARN} 与配额不符（差 {got - quota_n:+d}）"
        else:
            st = OK
        rows.append({"卷": s, "任务文件": f"{got} 条", "配额": quota_n, "状态": st})
    have = set(cli.available_suites())
    missing = [s for s in expected if s not in have]
    extra = sorted(have - set(expected))
    detail = f"{len(expected)} 个卷的配置声明，{len(expected) - len(missing)} 个任务文件就绪。"
    if missing:
        detail += f" 缺：{'、'.join(missing)}。"
    if extra:
        detail += f" 存在配置未声明的卷：{'、'.join(extra)}。"
    if extra:
        worst = WARN if worst == OK else worst
    return Check("suite_tasks", "卷任务文件是否存在", worst, detail,
                 "缺卷需先 build：`python -X utf8 run_eval.py build`（服务器 .venv 缺 "
                 "pyarrow / openpyxl 时须在本机 build 后同步 data/）", rows)


def _quota_total(quota: Any) -> Optional[int]:
    """采样配额可能是标量，也可能是 {"main":300,"hard":100} 这类分片字典。"""
    if isinstance(quota, int):
        return quota
    if isinstance(quota, dict):
        nums = [v for v in quota.values() if isinstance(v, int)]
        return sum(nums) if nums else None
    return None


# ---------------------------------------------------------------- 5 未跑完的卷
def check_pending_suites(subject: Optional[str] = None) -> Check:
    """判据：结果文件的**有效条数** < 任务文件条数。

    判定必须用报告自己的口径（`runner.load_results`：有 task 且无 error），
    不能用物理行数。glm53_flash 的 elder_refusal 有 554 行，其中 60 行是网关失败，
    报告只读到 494 条；按物理行数会得出"超出任务数"的反向结论，把真实缺口
    （6 条没有任何结果）盖掉。

    `失败` 列单列出来：error 行代表该题尝试过但没拿到回复。它不进报告，
    却直接决定该卷的 n 比任务数少多少，是"数据不全"的具体来源。
    """
    subjects = [subject] if subject else config.subject_names()
    tasks_n = {s: artifacts.count_lines(DATA_DIR / f"tasks_{s}.jsonl") for s in expected_suites()}
    files = artifacts.result_files()
    rows, worst = [], OK
    for sj in subjects:
        for s in expected_suites():
            want = tasks_n.get(s) or 0
            p = files.get((sj, s))
            if p is None:
                st = f"{WARN} 无结果文件" if want else "不适用"
                if want:
                    worst = WARN if worst == OK else worst
                rows.append({"subject": sj, "卷": s, "有效": 0, "失败": 0, "应跑": want, "状态": st})
                continue
            c = artifacts.load_counts(p)
            if not want:
                st = "不适用"
            elif c.valid < want:
                worst = WARN if worst == OK else worst
                st = f"{WARN} 缺 {want - c.valid} 条"
            elif c.valid > want:
                # 任务文件比结果少：说明任务被重采样或缩减过，结果里含旧题。
                worst = WARN if worst == OK else worst
                st = f"{WARN} 有效条数多于任务数（{c.valid - want:+d}）"
            elif c.error:
                # 全部任务都有结果，只是中间有过失败重试，不影响报告。
                st = f"{OK}（另有 {c.error} 条失败重试记录）"
            else:
                st = OK
            rows.append({"subject": sj, "卷": s, "有效": c.valid, "失败": c.error,
                         "应跑": want, "状态": st})
    n_bad = sum(1 for r in rows if str(r["状态"]).startswith(WARN))
    n_err = sum(r["失败"] for r in rows)
    detail = (f"{len(rows)} 个 (subject, 卷) 组合中 {n_bad} 个未跑满。"
              if n_bad else f"{len(rows)} 个 (subject, 卷) 组合均已跑满。")
    if n_err:
        detail += f" 累计 {n_err} 条失败记录（不进报告，重跑该卷会自动重试）。"
    return Check("pending_suites", "是否存在未跑完的卷", worst, detail,
                 "结果文件无时间戳，重跑会覆盖；补齐请对该 (subject, 卷) 直接 run；"
                 "失败行不算已完成，resume 时会自动重试", rows)


# ---------------------------------------------------------------- 6/7 探针
def check_local_endpoint() -> Check:
    """判据：本地端点声明的主机端口是否真的有服务在监听。

    **需用户显式触发**（设计 §5.2）。只看本机 subject，不触碰远端。
    这是 TCP 连接而非 HTTP 请求，不会产生业务日志。
    """
    rows, worst = [], OK
    for v in config.subject_views():
        if not v.local_endpoint:
            continue
        parts = urlsplit(v.base_url)
        host, port = parts.hostname or "127.0.0.1", parts.port or 80
        listening = _tcp_alive(host, port, timeout=0.6)
        if not listening:
            worst = WARN
        rows.append({"subject": v.name, "端点": f"{host}:{port}",
                     "状态": f"{OK} 已监听" if listening else f"{WARN} 无服务监听"})
    if not rows:
        worst = SKIP
        detail = "没有 subject 指向本机端点。"
    else:
        n_bad = sum(1 for r in rows if r["状态"].startswith(WARN))
        detail = (f"{len(rows)} 个本机端点中 {n_bad} 个未监听。"
                  if n_bad else f"{len(rows)} 个本机端点均在监听。")
    return Check("local_endpoint", "本地端点是否指向本机且已监听", worst, detail,
                 "本机端点需先在服务器起 vLLM：`bash scripts/serve_sft.sh` 或 `serve_base.sh`", rows)


def probe_endpoint(subject: str, *, timeout: float = 4.0) -> Check:
    """`GET <base_url>/models` 探活。**需用户显式触发**（设计 §5.2）。

    只探测该 subject 在对应配置 profile 中声明的 base_url，不接收任意 URL，
    以免界面被用作对任意主机的探测器。凭据仅用于本请求，不写入任何产物。
    """
    v = config.subject_view(subject)
    if not v.base_url:
        return Check("probe", f"端点探活（{subject}）", FAIL,
                     "base_url 为空，无法探测。", "先修好 env: 变量，见体检项 2")
    url = v.base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url, method="GET")
    key = (config.load().get("subjects", {}).get(subject, {}) or {}).get("api_key")
    if key:
        # 凭据只进请求头，不回显、不落盘。
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.status
        if 200 <= code < 300:
            return Check("probe", f"端点探活（{subject}）", OK,
                         f"GET {url} 返回 {code}。", "")
        return Check("probe", f"端点探活（{subject}）", WARN,
                     f"GET {url} 返回 {code}。", "非 2xx，服务可能在但路由不符")
    except urllib.error.HTTPError as e:
        hint = "凭据可能无效或缺失" if e.code in (401, 403) else "服务可应答但该路由不可用"
        return Check("probe", f"端点探活（{subject}）", WARN,
                     f"GET {url} 返回 HTTP {e.code}。", hint)
    except (urllib.error.URLError, OSError) as e:
        return Check("probe", f"端点探活（{subject}）", FAIL,
                     f"GET {url} 失败：{e!r}",
                     "端点不可达。本机端点见第 6 项；远端端点核对网络与网关")


def _tcp_alive(host: str, port: int, *, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------- 汇总
# 页面加载时跑的项（全部是本地文件读取与本地 socket，不含远端请求）。
LOCAL_CHECKS = (
    ("system_prompt", check_system_prompt),
    ("env_vars", check_env_vars),
    ("group_decoding", check_group_decoding),
    ("suite_tasks", check_suite_tasks),
    ("pending_suites", lambda: check_pending_suites(None)),
)


@lru_cache(maxsize=8)
def _cached_check(key: str) -> Check:
    # 仅缓存无参检查。带 subject 的检查（pending_suites）由调用方自行决定缓存。
    for k, fn in LOCAL_CHECKS:
        if k == key:
            return fn()
    raise KeyError(key)


def run_local(*, use_cache: bool = True) -> list[Check]:
    """跑五项本地检查（不含探针）。界面加载时调用。"""
    if use_cache:
        return [_cached_check(k) for k, _ in LOCAL_CHECKS]
    return [fn() for _, fn in LOCAL_CHECKS]


def overview(checks: list[Check]) -> dict[str, int]:
    out = {OK: 0, WARN: 0, FAIL: 0, SKIP: 0}
    for c in checks:
        out[c.status] = out.get(c.status, 0) + 1
    return out


def clear_cache() -> None:
    _cached_check.cache_clear()
    artifacts.clear_cache()

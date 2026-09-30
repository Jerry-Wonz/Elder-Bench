"""产物读取层：把 eval/ 下的既有产物读成界面可用的结构。设计 §4.6。

## 只读，且不解析命令行能给出的东西

本模块只读。写入一律交给 CLI 子进程（设计 §7.3，边界由 `paths.assert_writable` 执行）。

## 四类产物与它们的命名

| 类别 | 路径 | 命名 | 备注 |
|---|---|---|---|
| 汇总报告 | `results/report_*.json` | `report_<subject>[_<variant>]_<YYYYMMDD_HHMMSS>.json` | 与 `.md` 同名成对 |
| 单卷汇总 | `results/summary_<suite>__<subject>_<epoch>.json` | 尾串是 **epoch 秒**，不是时间戳 | 无 `subject` 字段，从文件名取 |
| 明细结果 | `results/results_<suite>__<subject>.jsonl` | **无时间戳**，同 subject 重跑即覆盖 | 设计 §2 记为约束 |
| 运行清单 | `runs/<run_id>.json` | `run_id = <subject>_<YYYYMMDD_HHMMSS>` | 带 `kind` 字段区分 run / report |

## 三处不能想当然的地方

1. **`run_id` 不落在汇总报告里**。`report_*.json` 的字段只有
   `n_results / per_suite / per_dimension / redline / gates / subject / suites_included`
   （rescore 产物另加 `scorer_version`）。界面要关联到 `runs/*.json`，只能靠
   **文件名里的 subject 与时间戳**：`report_qwen35_4b_base_20260922_003144.json`
   对应 `runs/qwen35_4b_base_20260922_003144.json`。这是一条约定而非外键，
   故 `ReportRef.run_id` 允许为 None，界面不得假定它存在。

2. **`runs/` 下的 `kind` 不只一种**。`run_eval.py` 的 `run` 与 `report` 两个分支
   都会写清单，`kind` 分别为 `"run"` 与 `"report"`。当前仓库只存在 `kind="report"`
   的一份。界面按 kind 分列，不把 `report` 清单当成完整推理运行的记录。

3. **`n_results` 同一 subject 跨报告会变**（glm53_flash 三份分别为 3910 / 3922 / 3928，
   早期网关失败行未剔除）。因此样本量按**报告**持有，不按 subject 聚合。

## 临时产物一律不列

`results/_dryrun/`、`_limited_*.jsonl`、`_review_*.md` 等都是中间态，名字以 `_` 开头。
本模块的列举函数统一跳过 `_` 前缀，避免把 `--limit 20` 的产物当正式结论展示。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Optional

from paths import ARCHIVE_DIR, REPORTS_DIR, RESULTS_DIR, RUNS_DIR
from paths import read_json

# 报告变体：文件名中 subject 与时间戳之间的部分。
VARIANTS = ("rescore", "dimscore")

_TS_RE = re.compile(r"^(?P<head>.*)_(?P<ts>\d{8}_\d{6})$")
_EPOCH_RE = re.compile(r"^(?P<head>.*)_(?P<ts>\d{9,11})$")


def _is_temp(p: Path) -> bool:
    """临时产物：名字以 `_` 开头，或落在 `_dryrun/` 之类的下划线目录内。"""
    return any(part.startswith("_") for part in p.parts)


def _parse_stem(stem: str) -> tuple[str, Optional[str], Optional[str]]:
    """拆 `report_<subject>[_<variant>]_<ts>` → (subject, variant, ts)。

    先剥时间戳（`YYYYMMDD_HHMMSS`，退回 epoch 秒），再从剩余部分剥变体。
    subject 本身可能以下划线结尾的 `_v2` 之类收尾，故变体只从**白名单**里认，
    不靠"最后一段就是变体"来猜。
    """
    m = _TS_RE.match(stem) or _EPOCH_RE.match(stem)
    if not m:
        return stem, None, None
    head, ts = m.group("head"), m.group("ts")
    variant = None
    for v in VARIANTS:
        if head.endswith(f"_{v}"):
            head, variant = head[: -len(v) - 1], v
            break
    return head, variant, ts


# ---------------------------------------------------------------- 汇总报告
@dataclass(frozen=True)
class ReportRef:
    """一份汇总报告的引用。字段不全从文件名推断，能读 JSON 的就读 JSON。"""

    path: Path
    subject: str
    variant: Optional[str]      # None / "rescore" / "dimscore"
    generated_at: Optional[str]  # 文件名里的 YYYYMMDD_HHMMSS
    mtime: float
    n_results: int
    scorer_version: Optional[str]
    gate: Optional[str]

    @property
    def run_id(self) -> Optional[str]:
        """按命名约定推出的 run_id。仅当 `runs/<run_id>.json` 存在时才有效，
        故调用方应配合 `run_manifest()` 使用，不要假定它指向真实清单。"""
        if not self.generated_at:
            return None
        return f"{self.subject}_{self.generated_at}"

    @property
    def is_rescore(self) -> bool:
        return self.scorer_version is not None

    @property
    def md_path(self) -> Path:
        return self.path.with_suffix(".md")

    @property
    def label(self) -> str:
        bits = [self.generated_at or self.path.stem]
        if self.variant:
            bits.append(f"[{self.variant}]")
        if self.scorer_version:
            bits.append(f"口径{self.scorer_version}")
        return " ".join(bits)

    def load(self) -> dict[str, Any]:
        return load_summary(self.path)


@lru_cache(maxsize=32)
def _load_cached(path_str: str, mtime_ns: int) -> dict[str, Any]:
    """按 (路径, mtime) 缓存。mtime 入键，故 CLI 子进程重写该文件后缓存自然失效。"""
    return read_json(path_str)


def load_summary(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    return _load_cached(str(p), p.stat().st_mtime_ns)


def iter_report_refs(*, only: Optional[Path] = None) -> Iterator[ReportRef]:
    """列出 results/report_*.json。只读顶层，不递归（避开 `_dryrun/`）。"""
    root = only or RESULTS_DIR
    if not root.exists():
        return
    for p in sorted(root.glob("report_*.json")):
        if _is_temp(p.relative_to(root)):
            continue
        stem = p.stem
        if not stem.startswith("report_"):
            continue
        subject_guess, variant, ts = _parse_stem(stem[len("report_"):])
        try:
            body = load_summary(p)
        except (OSError, ValueError):
            # 损坏的半截 JSON 不列入：列出来点开就报错，不如不列。
            continue
        yield ReportRef(
            path=p,
            subject=str(body.get("subject") or subject_guess),
            variant=variant,
            generated_at=ts,
            mtime=p.stat().st_mtime,
            n_results=int(body.get("n_results") or 0),
            scorer_version=body.get("scorer_version"),
            gate=(body.get("gates") or {}).get("overall"),
        )


def report_refs(*, only: Optional[Path] = None) -> list[ReportRef]:
    """全部报告，新的在前。排序键用文件名时间戳，缺失时退回 mtime。"""
    refs = list(iter_report_refs(only=only))
    refs.sort(key=lambda r: (r.generated_at or "", r.mtime), reverse=True)
    return refs


def refs_by_subject(*, only: Optional[Path] = None) -> dict[str, list[ReportRef]]:
    """subject -> 该 subject 的全部报告（新的在前）。界面按 subject 提供选择。"""
    out: dict[str, list[ReportRef]] = {}
    for r in report_refs(only=only):
        out.setdefault(r.subject, []).append(r)
    return out


def latest_by_subject(*, only: Optional[Path] = None) -> dict[str, ReportRef]:
    """subject -> 最新一份报告。总览与热力图用它。"""
    return {k: v[0] for k, v in refs_by_subject(only=only).items() if v}


# ---------------------------------------------------------------- 运行清单
def run_manifest_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


def run_manifests() -> list[dict[str, Any]]:
    """全部运行清单，新的在前。读不到或字段缺失的条目跳过并保留文件名占位。"""
    if not RUNS_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        if _is_temp(p.relative_to(RUNS_DIR)):
            continue
        try:
            body = read_json(p)
        except (OSError, ValueError):
            continue
        body.setdefault("run_id", p.stem)
        body["_path"] = str(p)
        out.append(body)
    out.sort(key=lambda d: str(d.get("run_id") or ""), reverse=True)
    return out


def run_manifest(run_id: str) -> Optional[dict[str, Any]]:
    p = run_manifest_path(run_id)
    if not p.exists():
        return None
    try:
        body = read_json(p)
    except (OSError, ValueError):
        return None
    body.setdefault("run_id", run_id)
    body["_path"] = str(p)
    return body


def manifest_for_report(ref: ReportRef) -> Optional[dict[str, Any]]:
    """按命名约定找报告对应的清单。找不到返回 None 是正常情形（设计 §4.5）。"""
    rid = ref.run_id
    return run_manifest(rid) if rid else None


# ---------------------------------------------------------------- 明细结果
def result_path(subject: str, suite: str) -> Path:
    """`results_<suite>__<subject>.jsonl`。设计 §2：该文件名不含时间戳，
    同 subject 重跑覆盖，故它只有"最新一次"的语义。"""
    return RESULTS_DIR / f"results_{suite}__{subject}.jsonl"


def result_files() -> dict[tuple[str, str], Path]:
    """(subject, suite) -> 明细路径。跳过 `_limited_*` 等临时文件。"""
    out: dict[tuple[str, str], Path] = {}
    if not RESULTS_DIR.exists():
        return out
    for p in sorted(RESULTS_DIR.glob("results_*__*.jsonl")):
        if _is_temp(p.relative_to(RESULTS_DIR)) or p.stem.startswith("results__"):
            continue
        rest = p.stem[len("results_"):]
        suite, _, subject = rest.partition("__")
        if suite and subject:
            out[(subject, suite)] = p
    return out


def suites_for(subject: str) -> list[str]:
    return sorted(s for (sj, s) in result_files() if sj == subject)


@lru_cache(maxsize=256)
def _count_lines_cached(path_str: str, mtime_ns: int, size: int) -> int:
    """按 (路径, mtime, 大小) 缓存。三者任一变化都会另起一条缓存。

    大小也入键：文件正被追加写入时 mtime 可能在秒级内不变，只看 mtime 会让
    进度停顿。加上 size 后，每写入一行都会 miss 并重算。
    """
    n = 0
    with open(path_str, "rb") as f:
        while chunk := f.read(1 << 20):
            n += chunk.count(b"\n")
    return n


def count_lines(path: str | Path) -> int:
    """数明细文件的**物理行数**（含 error 行）。用于作业进度。

    设计 §4.3 的进度靠文件行数而非解析 stdout。作业进行中"已尝试"的条数是
    `ok + err`，与 runner 自己打印的分母一致（runner.py:62-63），故这里必须是
    物理行数，不能剔除 error 行。判断"卷有没有跑完"要用 `load_counts`。

    逐块统计 `\\n` 不 split：glm53_flash 的明细单卷可达数十 MB，
    一次性读入会让进度轮询把内存顶起来。结果按 mtime+size 缓存，反复轮询不重复扫盘。
    """
    p = Path(path)
    if not p.exists():
        return 0
    st = p.stat()
    return _count_lines_cached(str(p), st.st_mtime_ns, st.st_size)


@dataclass(frozen=True)
class ResultCounts:
    """一个明细文件的行分类。口径与 `runner.load_results` 严格一致。"""

    total: int      # 物理行数（含空行）
    valid: int      # 有 task 且无 error：报告与指标真正读到的行
    error: int      # 有 error：网关/推理失败行，报告不计入
    broken: int     # 解不出 JSON 或被截断的行

    @property
    def n_reported(self) -> int:
        """进报告条数。等价于 len(runner.load_results(path))。"""
        return self.valid

    @property
    def n_attempted(self) -> int:
        """已尝试条数，作业进度用。"""
        return self.total - self.broken


@lru_cache(maxsize=256)
def _count_results_cached(path_str: str, mtime_ns: int, size: int) -> ResultCounts:
    valid = err = broken = total = 0
    with open(path_str, encoding="utf-8") as f:
        for line in f:
            total += 1
            s = line.strip()
            if not s:
                continue
            try:
                r = json.loads(s)
            except json.JSONDecodeError:
                broken += 1
                continue
            if not isinstance(r, dict):
                broken += 1
            elif "error" in r:
                err += 1
            elif "task" in r:
                # 与 runner.load_results 同判据：有 task 且无 error 才算有效。
                valid += 1
    return ResultCounts(total=total, valid=valid, error=err, broken=broken)


def load_counts(path: str | Path) -> ResultCounts:
    """按报告口径分类统计明细文件。判断"卷有没有跑完"必须用它。

    不能拿 `count_lines` 当有效条数：glm53_flash 的 elder_refusal 有 554 行，
    其中 60 行是网关失败，报告只读到 494 条。用物理行数会得出"超出任务数"的
    反向结论，掩盖真实缺口。
    """
    p = Path(path)
    if not p.exists():
        return ResultCounts(0, 0, 0, 0)
    st = p.stat()
    return _count_results_cached(str(p), st.st_mtime_ns, st.st_size)


# ---------------------------------------------------------------- 其它产物
def rulecheck_files() -> list[Path]:
    if not REPORTS_DIR.exists():
        return []
    return sorted((p for p in REPORTS_DIR.glob("rulecheck_*.json") if not _is_temp(p.relative_to(REPORTS_DIR))),
                  reverse=True)


def baseline_rulecheck_files() -> list[Path]:
    if not REPORTS_DIR.exists():
        return []
    return sorted(REPORTS_DIR.glob("rulecheck_baseline_*.json"), reverse=True)


def badcase_files() -> list[Path]:
    if not REPORTS_DIR.exists():
        return []
    return sorted(REPORTS_DIR.glob("badcase_*.md"), reverse=True)


def compare_files() -> list[Path]:
    if not RESULTS_DIR.exists():
        return []
    return sorted(RESULTS_DIR.glob("compare_*.md"), reverse=True)


def archive_dir(run_id: str) -> Path:
    """`results/runs/<run_id>/`。该归档目录当前尚未产生（设计 §2 约束），
    读取方需容忍为空。"""
    return ARCHIVE_DIR / run_id


def archived_result_path(run_id: str, suite: str) -> Path:
    return archive_dir(run_id) / f"results_{suite}.jsonl"


def result_path_for(ref: ReportRef, suite: str) -> Path:
    """优先取归档明细，缺失回退到覆盖式命名（设计 §2 的兼容读取约定）。"""
    p = archived_result_path(ref.run_id or "", suite)
    if p.exists():
        return p
    return result_path(ref.subject, suite)


def clear_cache() -> None:
    """丢弃全部缓存。CLI 子进程写过产物后由界面调用，避免读到旧值。

    常规刷新不必调用：读缓存的键含 mtime / size，文件一变即自动失效。
    这里主要给"同一秒内被改写、且大小恰好相同"这类边角情形兜底。
    """
    _load_cached.cache_clear()
    _count_lines_cached.cache_clear()
    _count_results_cached.cache_clear()

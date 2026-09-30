"""路径解析与写入边界。

纪律来源：《评测WebUI设计》§7.3。控制台**只能写 `eval/jobs/**`**，
其余产物目录一律只读，所有写入由 CLI 子进程完成。

本模块是这条纪律的唯一执行点。其他模块不得直接调用 `open(..., "w")`，
一律经 `write_text` / `write_json` / `open_writable` 走边界断言。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

UI_DIR = Path(__file__).resolve().parent
REPO_ROOT = UI_DIR.parent
EVAL_DIR = REPO_ROOT / "eval"

RESULTS_DIR = EVAL_DIR / "results"
REPORTS_DIR = EVAL_DIR / "reports"
RUNS_DIR = EVAL_DIR / "runs"
DATA_DIR = EVAL_DIR / "data"
ARCHIVE_DIR = RESULTS_DIR / "runs"

# 控制台唯一可写目录。设计 §3.4 与 §7.3。
JOBS_DIR = EVAL_DIR / "jobs"
RUN_LOCK = JOBS_DIR / ".run.lock"

# 显式列出受保护路径：断言失败时能直接指出被保护的是什么，
# 而不是只报一句 "不是可写目录"。这些路径名也用于 §8.3 的写边界静态检查。
PROTECTED = {
    "eval/data": DATA_DIR,
    "eval/results": RESULTS_DIR,
    "eval/reports": REPORTS_DIR,
    "eval/runs": RUNS_DIR,
    "eval/config.yaml": EVAL_DIR / "config.yaml",
    "eval/config.deploy.yaml": EVAL_DIR / "config.deploy.yaml",
    "eval/config.history.yaml": EVAL_DIR / "config.history.yaml",
    "eval/.env": EVAL_DIR / ".env",
    "benchmark/external": REPO_ROOT / "benchmark" / "external",
}

_WRITABLE_ROOTS = (JOBS_DIR,)


def _protected_hit(path: Path) -> str | None:
    """该路径是否落在受保护目录内。返回被命中项的名字，便于报错。"""
    for name, root in PROTECTED.items():
        if path == root or root in path.parents:
            return name
    return None


def assert_writable(path: str | os.PathLike[str]) -> Path:
    """确认路径可写，返回解析后的绝对路径。不可写则抛 PermissionError。

    两道判断，顺序有意为之：
    1. 先查受保护路径。这样即使将来有人误把某产物目录加进白名单，
       报错信息仍会指出「这是受保护路径」而不是「不在白名单」。
    2. 再查白名单。只允许 eval/jobs/** 之下。
    """
    p = Path(path).resolve()
    hit = _protected_hit(p)
    if hit:
        raise PermissionError(
            f"拒绝写入受保护路径 {hit}：{p}\n"
            "控制台只写 eval/jobs/**；产物目录由 CLI 子进程写入（设计 §7.3）。")
    if not any(p == r or r in p.parents for r in _WRITABLE_ROOTS):
        raise PermissionError(
            f"拒绝写入白名单外的路径：{p}\n"
            f"允许的根目录：{[str(r) for r in _WRITABLE_ROOTS]}")
    return p


def safe_join(root: Path, *parts: str) -> Path:
    """在 root 下拼路径，并确保结果仍在 root 之内（挡住 `..` 与绝对路径片段）。

    用于 job_id、suite、subject 这类来自界面输入或文件名的片段。
    界面输入不进 shell（§7.2），但会进文件系统，所以这里也要挡一次。
    """
    p = root.joinpath(*parts)
    rp, rr = Path(os.path.abspath(p)), Path(os.path.abspath(root))
    if rp != rr and rr not in rp.parents:
        raise ValueError(f"路径逃逸：{parts} 解析到 {rp}，不在 {rr} 之内")
    return rp


# ---------------------------------------------------------------- 受控写入
def open_writable(path: str | os.PathLike[str], mode: str = "w", **kw):
    """带边界断言的 open。

    统一 `newline="\\n"`：与 runner.py:59 同一纪律。不指定时 Windows 会把 `\\n`
    写成 `\\r\\n`，同一份产物在两个平台上字节不一致。作业记录虽然分机器存储，
    但保持同一纪律的成本为零，且能让 `verify` 一类工具跨端比对。
    """
    p = assert_writable(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    kw.setdefault("encoding", "utf-8")
    if "b" not in mode:
        kw.setdefault("newline", "\n")
    return open(p, mode, **kw)


def write_text(path: str | os.PathLike[str], text: str) -> Path:
    with open_writable(path, "w") as f:
        f.write(text)
    return Path(path).resolve()


def write_json(path: str | os.PathLike[str], obj: Any, *, indent: int = 2) -> Path:
    with open_writable(path, "w") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=indent))
    return Path(path).resolve()


# ---------------------------------------------------------------- 只读访问
def read_json(path: str | os.PathLike[str]) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_text(path: str | os.PathLike[str]) -> str:
    return Path(path).read_text(encoding="utf-8")


def iter_jsonl(path: str | os.PathLike[str]):
    """逐行产出 JSON 对象，跳过损坏行。

    损坏行跳过而非抛错，与 runner.load_results 的处理一致：结果文件是追加写入的，
    读到半行是正常情形（进程正在写），不应让整个页面崩掉。
    """
    p = Path(path)
    if not p.exists():
        return
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

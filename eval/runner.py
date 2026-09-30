"""评测执行器：加载统一任务 -> 并发推理 -> 客观判分 -> 增量落盘 -> 聚合。

特性：
- 断点续跑：results jsonl 中已存在的 task_id 自动跳过；
- 并发控制：asyncio.Semaphore 限流被测模型调用；
- 失败隔离：单条任务失败不影响整体，错误写入该行 result；
- 判分零网络调用：scoring.grade() 为纯规则/统计实现（v2.0 取消 LLM-judge）。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from detectors import RULE_VERSION
from metrics import aggregate, save_summary
from model_client import ChatResult
from schema import EvalTask, load_tasks
from scoring import SCORER_VERSION, grade


async def run_suite(tasks_path: str, model, results_path: str,
                    concurrency: int = 8) -> dict[str, int]:
    """执行一卷评测。返回本轮计数，供 manifest 记录 outcome。

    计数分两个口径，不混为一谈：
    - done_before：此前已在结果文件中、本次跳过的条数（断点续跑）；
    - ok / err：本次实际请求的条数中，成功与失败的条数。
    当轮成功数 = done_before + ok（结果文件中的有效行总数）。
    """
    tasks: list[EvalTask] = load_tasks(tasks_path)
    done = _load_done_ids(results_path)
    pending = [t for t in tasks if t.task_id not in done]
    print(f"[run] {Path(tasks_path).name}: {len(tasks)} tasks, {len(done)} done, "
          f"{len(pending)} pending -> {results_path}")

    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    counter = {"ok": 0, "err": 0}
    t0 = time.time()

    async def one(task: EvalTask) -> None:
        nonlocal counter
        async with sem:
            record: dict[str, Any] = {"task_id": task.task_id, "task": task.to_dict(),
                                      "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            try:
                result = await model.chat(task.messages)
                if isinstance(result, str):
                    # 兼容旧 FakeModel / 自定义测试模型。
                    result = ChatResult(content=result)
                response = result.content
                record["response"] = response
                record["generation"] = result.metadata()
                if hasattr(model, "metadata"):
                    record["client"] = model.metadata()
                record["verdict"] = grade(task, response, scorer_version=SCORER_VERSION)
                record["scorer_version"] = SCORER_VERSION
                record["rule_version"] = RULE_VERSION
                counter["ok"] += 1
            except Exception as e:  # 失败隔离
                record["error"] = repr(e)
                counter["err"] += 1
            async with lock:
                # newline="\n" 关闭平台换行转换：不加时 Windows 会把 \n 写成 \r\n，
                # 同一份评测在两个平台上产出字节不一致的结果文件（本仓库历史上
                # glm 列为 CRLF、两列 4B 为 LF 即由此而来）。逐字节可比是横向比较的前提。
                with open(results_path, "a", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                if (counter["ok"] + counter["err"]) % 50 == 0:
                    rate = (counter["ok"] + counter["err"]) / max(time.time() - t0, 1)
                    print(f"  progress {counter['ok'] + counter['err']}/{len(pending)} ({rate:.1f}/s)")

    await asyncio.gather(*(one(t) for t in pending))
    elapsed = time.time() - t0
    print(f"[run] finished ok={counter['ok']} err={counter['err']} in {elapsed:.0f}s")
    return {"n_tasks": len(tasks), "done_before": len(done), "n_pending": len(pending),
            "ok": counter["ok"], "err": counter["err"], "elapsed_s": round(elapsed, 1)}


def _load_done_ids(results_path: str) -> set[str]:
    path = Path(results_path)
    if not path.exists():
        return set()
    ids = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                # 错误行不算 done：resume 时自动重试（如 judge 端点 503 瞬时故障）
                if "error" not in r:
                    ids.add(r["task_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return ids


def load_result_rows(results_path: str | Path) -> list[dict[str, Any]]:
    """读取全部有效 JSON 行，保留网关错误行供覆盖率与缺失原因统计。"""
    rows = []
    with open(results_path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and "task" in r:
                rows.append(r)
    return rows


def load_results(results_path: str | Path) -> list[dict[str, Any]]:
    return [r for r in load_result_rows(results_path) if "error" not in r]


def regrade_results(rows: list[dict[str, Any]], scorer_version: str) -> list[dict[str, Any]]:
    """按指定判分口径重放已有 response，保留原行字段。

    格式解析类模式在 scorer 1.1 下仍读全文；读正文类模式先剥离推理轨迹，
    与在线 grade() 完全同一入口。错误行与无 response 行原样保留。
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if "error" in row or row.get("response") is None:
            out.append(dict(row))
            continue
        task = EvalTask.from_dict(row["task"])
        regraded = dict(
            row,
            verdict=grade(task, row["response"], scorer_version=scorer_version),
            scorer_version=scorer_version,
            rule_version=RULE_VERSION,
        )
        out.append(regraded)
    return out


def load_scored_results(results_path: str | Path, scorer_version: str) -> list[dict[str, Any]]:
    """读取成功行并按指定口径离线重判。错误行不进入指标分母。"""
    return regrade_results(load_results(results_path), scorer_version)


def summarize(results_path: str, summary_path: str, redline: dict | None = None,
              coverage: dict | None = None, scorer_version: str = SCORER_VERSION) -> dict:
    results = load_scored_results(results_path, scorer_version)
    summary = aggregate(results, redline=redline, coverage=coverage)
    summary["scorer_version"] = scorer_version
    summary["rule_version"] = RULE_VERSION
    save_summary(summary, summary_path)
    print(f"[summary] -> {summary_path}  overall={summary.get('gates', {}).get('overall')}")
    return summary

"""CLI 入口。

用法示例：
  # 1. 构建统一评测集（默认全部 10 卷；幂等，固定种子）
  python run_eval.py build
  python run_eval.py build --suites risk_crisis elder_safety

  # 2. 干跑全链路（FakeModel，不需要任何服务；判分为纯规则/统计，无需 judge）
  python run_eval.py smoke

  # 3. 跑某一卷（需先起被测模型的 OpenAI 兼容服务）
  python run_eval.py run --suite risk_crisis --subject qwen35_4b_base_iso --limit 100

  # 4. 汇总某 subject 的全部已跑卷 -> JSON + Markdown 报告
  python run_eval.py report --subject qwen35_4b_base_iso

  # 5. 离线重判分：读已落盘 response 重跑 scoring + aggregate，不覆盖历史报告
  python run_eval.py rescore --subject qwen35_4b_base_iso
  python run_eval.py rescore --subject qwen35_4b_base_iso
  python run_eval.py rescore --subject qwen35_4b_base_iso_v2 --suite elder_safety --scorer-version 1.1

  # 6. 规则检测器校准（vs 数据集人工标注/官方示范，输出逐维 kappa，不调模型）
  python run_eval.py rulecheck

  # 7. 查看构建审计
  python run_eval.py audit
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

import adapters
import manifest as manifest_mod
import reporting
from detectors import RULE_VERSION
from model_client import ChatClient, SystemPromptChatClient
from metrics import aggregate, per_task_scores, save_summary
from runner import (load_result_rows, load_scored_results, regrade_results, run_suite,
                    summarize)
from schema import load_tasks
from scoring import SCORER_VERSION, SCORER_VERSIONS
from versions import RENDERER_VERSION

EVAL_DIR = Path(__file__).parent
RESULTS = EVAL_DIR / "results"
ARCHIVE = manifest_mod.RUNS_RESULTS_DIR
SUITES = list(adapters.SUITE_BUILDERS.keys())
PROFILE_CONFIGS = ("config.deploy.yaml", "config.history.yaml")


def _load_dotenv() -> None:
    """加载 eval/.env（存在则注入环境变量，不覆盖已有值）；密钥不入库（.gitignore）。"""
    env_file = EVAL_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path: str | None = None) -> dict:
    """加载配置；子配置可通过顶层 `extends: config.yaml` 继承公共段。

    子文件的 `subjects` 段会整体覆盖父文件，而不是与父文件 subjects 合并，
    这样部署态和历史配置不会泄漏进默认评测对象列表。
    """
    return _load_config(path, set())


def _load_config(path: str | None, seen: set[Path]) -> dict:
    _load_dotenv()
    cfg_path = Path(path) if path else (EVAL_DIR / "config.yaml")
    if not cfg_path.is_absolute() and not cfg_path.exists():
        candidate = EVAL_DIR / cfg_path
        if candidate.exists():
            cfg_path = candidate
    resolved = cfg_path.resolve()
    if resolved in seen:
        raise SystemExit(f"config extends cycle: {resolved}")
    seen = set(seen)
    seen.add(resolved)
    with open(cfg_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    extends = raw.pop("extends", None)
    if extends:
        base = _load_config(str(cfg_path.parent / str(extends)), seen)
        base.update(raw)
        return _resolve_env(base)
    return _resolve_env(raw)


def _resolve_env(node: Any) -> Any:
    """递归解析 "env:VAR" -> 环境变量（未设置则空串）。"""
    if isinstance(node, str) and node.startswith("env:"):
        return os.environ.get(node[4:], "")
    if isinstance(node, dict):
        return {k: _resolve_env(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_env(v) for v in node]
    return node


def load_visible_config() -> dict:
    """默认能力组 + 独立部署态配置，供命令面同时展示五个活动 subject。"""
    cfg = load_config()
    subjects = dict(cfg.get("subjects") or {})
    deploy = EVAL_DIR / "config.deploy.yaml"
    if deploy.exists():
        subjects.update((load_config(str(deploy)).get("subjects") or {}))
    cfg["subjects"] = subjects
    return cfg


def resolve_subject_config(subject: str, explicit: str | None = None) -> str | None:
    """显式配置优先；否则只在非默认 profile 中查找该 subject。"""
    if explicit:
        return explicit
    if subject in (load_config().get("subjects") or {}):
        return None
    for rel in PROFILE_CONFIGS:
        path = EVAL_DIR / rel
        if path.exists() and subject in (load_config(str(path)).get("subjects") or {}):
            return rel
    return None


def _make_subject(cfg: dict, name: str):
    subjects = cfg.get("subjects", {})
    if name not in subjects:
        raise SystemExit(f"unknown subject '{name}'; configured: {list(subjects)}")
    s = subjects[name]
    client = ChatClient(
        base_url=s["base_url"], api_key=s.get("api_key") or "none", model=s["model"],
        temperature=s.get("temperature", 0.2), max_tokens=s.get("max_tokens", 1024),
        top_p=s.get("top_p"), seed=s.get("seed"),
        presence_penalty=s.get("presence_penalty"),
        frequency_penalty=s.get("frequency_penalty"), stop=s.get("stop"),
        extra_body=s.get("extra_body"), max_model_len=s.get("max_model_len"),
        model_revision=s.get("model_revision"))
    sysfile = s.get("system_prompt_file")
    if sysfile:
        prompt = (EVAL_DIR / sysfile).read_text(encoding="utf-8").strip()
        return SystemPromptChatClient(client, prompt)
    return client


def results_path(suite: str, subject: str) -> Path:
    return RESULTS / f"results_{suite}__{subject}.jsonl"


def _suite_of(path: Path) -> str:
    return path.stem.split("__")[0][len("results_"):]


def subject_result_files(subject: str, suites: list[str] | None = None,
                         run_id: str | None = None) -> list[Path]:
    """某 subject 的既有结果文件，report / rescore / manifest 共用同一选取口径。

    默认读 results/ 下的平铺文件（该 subject 的最新结果）。指定 run_id 时改读
    results/runs/<run_id>/ 下该次运行的快照，用于复核历史运行而不受后续重跑影响。

    排除 smoke 干跑目录与 `_` 前缀的调试残留（如 run --limit 生成的 _limited_*.jsonl）。
    """
    base = (ARCHIVE / run_id) if run_id else RESULTS
    files = sorted(base.glob(f"results_*__{subject}.jsonl"))
    files = [f for f in files if "_dryrun" not in str(f) and not f.name.startswith("_")]
    if suites:
        keep = set(suites)
        files = [f for f in files if _suite_of(f) in keep]
    if not files:
        scope = f"run '{run_id}'" if run_id else f"subject '{subject}'"
        raise SystemExit(f"no results for {scope}"
                         + (f" in suites {suites}" if suites else ""))
    return files


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _error_kind(err: str) -> str:
    text = str(err or "")
    if "400" in text:
        return "http_400"
    if "429" in text:
        return "http_429"
    if "timeout" in text.lower():
        return "timeout"
    return "other"


def _coverage_for_files(files: list[Path]) -> dict[str, dict[str, Any]]:
    """按卷复算 expected/scored/error/missing 与正例覆盖。

    结果文件可能包含多轮错误行，因此 unique task_id 是覆盖率分母；scored_rows 仍
    记录原始成功行数，便于发现重复成功行。
    """
    coverage: dict[str, dict[str, Any]] = {}
    for path in files:
        suite = _suite_of(path)
        rows = load_result_rows(path)
        ok = [r for r in rows if "error" not in r]
        errs = [r for r in rows if "error" in r]
        ok_ids = {r.get("task_id") for r in ok if r.get("task_id") is not None}
        err_ids = {r.get("task_id") for r in errs if r.get("task_id") is not None}

        task_path = EVAL_DIR / "data" / f"tasks_{suite}.jsonl"
        tasks = load_tasks(str(task_path)) if task_path.exists() else []
        expected_ids = {t.task_id for t in tasks}
        expected_pos = {t.task_id for t in tasks
                        if int((t.reference or {}).get("is_crisis") or 0) == 1}
        scored_pos = {r.get("task_id") for r in ok if r.get("task_id") in expected_pos}

        n_expected = len(expected_ids) or None
        n_expected_pos = len(expected_pos) or None
        missing = sorted(expected_ids - ok_ids)
        coverage[suite] = {
            "result_file": path.resolve().relative_to(EVAL_DIR.parent.resolve()).as_posix(),
            "result_sha256": _sha256_file(path),
            "result_rows": len(rows),
            "expected_n": n_expected,
            "scored_rows": len(ok),
            "scored_unique_n": len(ok_ids),
            "error_rows": len(errs),
            "error_task_ids": len(err_ids),
            "error_kinds": dict(sorted(Counter(_error_kind(r.get("error")) for r in errs).items())),
            "missing_n": len(missing) if n_expected else None,
            "missing_task_ids": missing,
            "coverage_rate": round(len(ok_ids) / n_expected, 4) if n_expected else None,
            "expected_positive_n": n_expected_pos,
            "scored_positive_n": len(scored_pos),
            "positive_coverage_rate": round(len(scored_pos) / n_expected_pos, 4)
            if n_expected_pos else None,
            "expected_by_lang": dict(sorted(Counter(t.lang for t in tasks).items())),
        }
    return coverage


def _source_provenance(files: list[Path]) -> dict[str, Any]:
    """报告所依赖的结果文件与任务文件 sha256，写入 summary 供独立核对。"""
    out: dict[str, Any] = {"result_files": [], "task_files": []}
    seen_suites: set[str] = set()
    for path in files:
        suite = _suite_of(path)
        out["result_files"].append({
            "suite": suite,
            "path": path.resolve().relative_to(EVAL_DIR.parent.resolve()).as_posix(),
            "sha256": _sha256_file(path),
            "n_rows": len(load_result_rows(path)),
        })
        if suite not in seen_suites:
            task_path = EVAL_DIR / "data" / f"tasks_{suite}.jsonl"
            out["task_files"].append({
                "suite": suite,
                "path": task_path.relative_to(EVAL_DIR.parent).as_posix(),
                "sha256": _sha256_file(task_path),
                "n_tasks": len(load_tasks(str(task_path))) if task_path.exists() else None,
            })
            seen_suites.add(suite)
    return out


def _subject_meta(cfg: dict, subject: str) -> tuple[str | None, list[str]]:
    """取 subject 的分组与标签；缺失归入 ungrouped（向后兼容旧 config）。"""
    s = cfg.get("subjects", {}).get(subject, {})
    return s.get("group"), list(s.get("tags") or [])


def _archive_run(results_paths: list[Path], run_id: str) -> Path | None:
    """把本次运行的结果文件快照归档到 results/runs/<run_id>/。

    平铺文件保持"该 subject 最新结果"语义不变，归档用于消除静默覆盖：
    同一 subject 重跑后，靠 run_id 仍能取回当时的那一份。
    """
    if not results_paths:
        return None
    dest = ARCHIVE / run_id
    dest.mkdir(parents=True, exist_ok=True)
    for p in results_paths:
        shutil.copy2(p, dest / p.name)
    return dest


def _write_manifest(cfg: dict, subject: str, *, suites: list[str] | None = None,
                    kind: str = "run", run_id: str | None = None,
                    started: str | None = None,
                    timing: dict | None = None,
                    outcome: dict | None = None,
                    config_path: str | None = None,
                    derived_from_run: str | None = None,
                    scorer_version: str = SCORER_VERSION,
                    renderer_version: str = RENDERER_VERSION) -> dict:
    """组装并落盘 manifest。kind 区分 run 与 report 两种来源，避免同秒碰撞时混淆。"""
    group, tags = _subject_meta(cfg, subject)
    m = manifest_mod.build_manifest(
        cfg["subjects"][subject], subject, suites=suites, started=started,
        timing=timing, outcome=outcome, group=group, tags=tags,
        sampling_seed=(cfg.get("sampling") or {}).get("seed"),
        config_path=config_path, scorer_version=scorer_version,
        renderer_version=renderer_version)
    if run_id:
        m["run_id"] = run_id
    m["kind"] = kind
    if kind == "report":
        m["report_id"] = m["run_id"]
        m["derived_from_run"] = derived_from_run
    path = manifest_mod.save(m)
    print(f"[manifest] -> {path.relative_to(EVAL_DIR)}")
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description="养老陪护模型三维度评测 harness（全客观判分）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    bp = sub.add_parser("build", help="构建统一评测任务集")
    bp.add_argument("--suites", nargs="+", default=None, choices=SUITES)
    bp.add_argument("--config", default=None)

    sp = sub.add_parser("smoke", help="FakeModel 全链路干跑")
    sp.add_argument("--config", default=None)

    rp = sub.add_parser("run", help="执行评测")
    rp.add_argument("--suite", required=True, choices=SUITES)
    rp.add_argument("--subject", required=True)
    rp.add_argument("--limit", type=int, default=None, help="截断任务数（调试用）")
    rp.add_argument("--concurrency", type=int, default=None)
    rp.add_argument("--config", default=None)

    pp = sub.add_parser("report", help="汇总某 subject 全部已跑卷 -> 报告")
    pp.add_argument("--subject", required=True)
    pp.add_argument("--run", default=None,
                    help="改读 results/runs/<run_id>/ 的历史快照（默认读最新平铺结果）")
    pp.add_argument("--config", default=None)

    xp = sub.add_parser("rescore", help="离线重判分：读落盘 response 重算，不覆盖历史报告")
    xp.add_argument("--subject", required=True)
    xp.add_argument("--suite", nargs="+", default=None, choices=SUITES)
    xp.add_argument("--scorer-version", default=None, choices=list(SCORER_VERSIONS),
                    help=f"判分口径版本，默认 {SCORER_VERSION}（现行跑批口径）")
    xp.add_argument("--check", default=None,
                    help="与给定历史报告 JSON 逐字段比对，有差异则以退出码 1 结束（回归门禁）")
    xp.add_argument("--tol", type=float, default=0.0,
                    help="比对容差（报告数值单位），仅用于吸收聚合层平局点舍入敏感性；"
                         "容差内的差异仍逐条打印。默认 0 = 严格逐位")
    xp.add_argument("--config", default=None)

    kp = sub.add_parser("rulecheck", help="规则检测器校准（vs 人工标注/官方示范，不调模型）")
    kp.add_argument("--baseline", action="store_true",
                    help="额外输出校准基线与阈值敏感性表（供红线阈值重定标使用）")

    cp = sub.add_parser("compare", help="多 subject 并排对比（按 group 分组，同组内断言解码一致）")
    cp.add_argument("--subjects", nargs="+", required=True)
    cp.add_argument("--run", default=None, help="改读 results/runs/<run_id>/ 的历史快照")
    cp.add_argument("--config", default=None)

    ep = sub.add_parser("export-badcase", help="导出失败样本人工判读材料")
    ep.add_argument("--subject", required=True)
    ep.add_argument("--suite", required=True, choices=list(reporting.BAD_CRITERIA))
    ep.add_argument("--criterion", default=None,
                    help="失败判据，默认该卷的第一项（见 reporting.BAD_CRITERIA）")
    ep.add_argument("--n", type=int, default=40, help="抽样条数")
    ep.add_argument("--seed", type=int, default=20260921)
    ep.add_argument("--run", default=None, help="改读 results/runs/<run_id>/ 的历史快照")
    ep.add_argument("--out", default=None, help="输出路径，默认 eval/reports/")

    vp = sub.add_parser("verify", help="复现校验：重算某次 run 记录的数据/模板/版本哈希")
    vp.add_argument("--run", required=True, help="run_id（见 eval/runs/<run_id>.json）")

    mp = sub.add_parser("manifest", help="打印某 subject 的配置快照与历史 run 列表")
    mp.add_argument("--subject", required=True)
    mp.add_argument("--config", default=None)

    ap2 = sub.add_parser("audit", help="打印构建审计")

    rr = sub.add_parser("rerender", help="用当前渲染器重渲染历史 report_*.json")
    rr.add_argument("--include-rescore", action="store_true",
                    help="同时重渲染 results/scorer_* 下的现行口径报告")

    args = ap.parse_args()
    _load_dotenv()
    RESULTS.mkdir(exist_ok=True)

    if args.cmd == "build":
        adapters.build_all(cfg=load_config(args.config), suites=args.suites)

    elif args.cmd == "smoke":
        import smoke_test
        smoke_test.main()

    elif args.cmd == "run":
        args.config = resolve_subject_config(args.subject, args.config)
        cfg = load_config(args.config)
        tasks_path = EVAL_DIR / "data" / f"tasks_{args.suite}.jsonl"
        if not tasks_path.exists():
            adapters.build_all(cfg=cfg, suites=[args.suite])
        subject = _make_subject(cfg, args.subject)
        out = results_path(args.suite, args.subject)
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        run_id = f"{args.subject}_{time.strftime('%Y%m%d_%H%M%S')}"
        if args.limit:
            from schema import load_tasks, save_tasks
            tasks = load_tasks(str(tasks_path))[: args.limit]
            tmp = RESULTS / f"_limited_{args.suite}.jsonl"
            save_tasks(tasks, str(tmp))
            tasks_path = tmp
        stats = asyncio.run(run_suite(str(tasks_path), subject, str(out),
                                      concurrency=args.concurrency or cfg.get("concurrency", 8)))
        coverage = _coverage_for_files([out])
        summarize(str(out), str(RESULTS / f"summary_{args.suite}__{args.subject}_{int(time.time())}.json"),
                  redline=cfg.get("redline"), coverage=coverage, scorer_version=SCORER_VERSION)
        # 归档顺序在 summarize 之后：即使 --limit 调试运行也归档，避免身份断链。
        dest = _archive_run([out], run_id)
        if dest:
            print(f"[archive] -> {dest.relative_to(EVAL_DIR)}")
        _write_manifest(cfg, args.subject, suites=[args.suite], kind="run", run_id=run_id,
                        started=started, outcome=dict(stats, suite=args.suite,
                                                      archived=bool(dest), limited=bool(args.limit)),
                        config_path=args.config)

    elif args.cmd == "report":
        args.config = resolve_subject_config(args.subject, args.config)
        cfg = load_config(args.config)
        files = subject_result_files(args.subject, run_id=args.run)
        coverage = _coverage_for_files(files)
        all_results: list[dict] = []
        for f in files:
            all_results.extend(load_scored_results(f, SCORER_VERSION))
        summary = aggregate(all_results, redline=cfg.get("redline"), coverage=coverage)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        report_id = f"{args.subject}_report_{stamp}"
        source = _source_provenance(files)
        summary.update({
            "report_id": report_id,
            "run_id": args.run,
            "subject": args.subject,
            "suites_included": [_suite_of(f) for f in files],
            "scorer_version": SCORER_VERSION,
            "rule_version": RULE_VERSION,
            "renderer_version": RENDERER_VERSION,
            "source": source,
            "code": manifest_mod.git_revision(),
        })
        summary["publication_status"] = (
            "ELIGIBLE" if summary["code"].get("dirty") is False else "NOT_RELEASABLE_DIRTY_WORKTREE")
        if len(summary["suites_included"]) < len(SUITES):
            summary["coverage_note"] = (
                "仅覆盖红线三卷，不构成三维度结论。"
                if set(summary["suites_included"]) == {"elder_safety", "risk_crisis", "risk_response"}
                else f"仅覆盖 {len(summary['suites_included'])}/{len(SUITES)} 卷，不构成完整三维度结论。")
        m = _write_manifest(cfg, args.subject, suites=summary["suites_included"], kind="report",
                            run_id=report_id, outcome={"report_id": report_id,
                                                      "source": source,
                                                      "gates": summary["gates"]},
                            config_path=args.config, derived_from_run=args.run,
                            scorer_version=SCORER_VERSION)
        summary["manifest_id"] = m["run_id"]
        save_summary(summary, RESULTS / f"report_{args.subject}_{stamp}.json")
        _write_md(summary, RESULTS / f"report_{args.subject}_{stamp}.md")
        print(f"[report] -> results/report_{args.subject}_{stamp}.md "
              f"overall={summary['gates']['overall']}")

    elif args.cmd == "rescore":
        args.config = resolve_subject_config(args.subject, args.config)
        sys.exit(_rescore(args))

    elif args.cmd == "rulecheck":
        import rulecheck
        rulecheck.main(baseline=args.baseline)

    elif args.cmd == "compare":
        _compare(load_config(args.config) if args.config else load_visible_config(),
                 args.subjects, args.run)

    elif args.cmd == "export-badcase":
        _export_badcase(args)

    elif args.cmd == "verify":
        _verify(args.run)

    elif args.cmd == "manifest":
        args.config = resolve_subject_config(args.subject, args.config)
        _show_manifest(load_config(args.config), args.subject)

    elif args.cmd == "audit":
        audit_path = EVAL_DIR / "data" / "build_audit.json"
        print(audit_path.read_text(encoding="utf-8") if audit_path.exists()
              else "no audit; run build first")

    elif args.cmd == "rerender":
        _rerender_reports(include_rescore=args.include_rescore)


def _rerender_reports(*, include_rescore: bool = False) -> None:
    """用当前指标与渲染器重建历史报告，不覆盖原始 Markdown。

    仅从旧 summary 复制文本会丢失 k/n、分层和溯源字段；这里按该报告声明的
    scorer_version 对原始结果重聚合，并沿用旧阈值，输出 *_rerendered.md。
    """
    paths = list(RESULTS.glob("report_*.json"))
    if include_rescore:
        paths += list((RESULTS / "scorer_*").glob("report_*.json"))
    cache: dict[tuple[str, tuple[str, ...], str], dict[str, Any]] = {}
    n = 0
    for path in sorted(paths):
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        subject = old.get("subject")
        if not subject:
            continue
        ver = str(old.get("scorer_version") or "1.0")
        suites = tuple(old.get("suites_included") or [_suite_of(f) for f in subject_result_files(subject)])
        key = (subject, suites, ver)
        if key not in cache:
            files = subject_result_files(subject, list(suites))
            rows: list[dict[str, Any]] = []
            for f in files:
                rows.extend(load_scored_results(f, ver))
            old_thresholds = (old.get("redline") or {}).get("thresholds") or {}
            rebuilt = aggregate(rows, redline=old_thresholds or None,
                                coverage=_coverage_for_files(files))
            rebuilt.update({
                "subject": subject,
                "suites_included": [_suite_of(f) for f in files],
                "scorer_version": ver,
                "rule_version": RULE_VERSION,
                "renderer_version": RENDERER_VERSION,
                "source": _source_provenance(files),
                "code": manifest_mod.git_revision(),
                "publication_status": "LEGACY_RERENDER",
            })
            if ver == "1.0":
                rebuilt.setdefault("redline", {})["threshold_sources"] = {
                    "crisis_miss_rate_max": "历史经验值；本次仅重渲染，未重算",
                    "crisis_fpr_max": "原 1.0 报告无假阳门禁；重渲染按当前 FPR=30 约束展示",
                    "grandguard_omit_rate_max": "历史经验值；本次仅重渲染，未重算",
                    "response_crisis_miss_max": "历史经验值；本次仅重渲染，未重算",
                }
            cache[key] = rebuilt
        summary = dict(cache[key])
        summary["report_id"] = f"{path.stem}_rerendered"
        generated_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
        dst = path.with_name(path.stem + "_rerendered.md")
        _write_md(summary, dst)
        print(f"[rerender] {path.relative_to(EVAL_DIR)} -> {dst.relative_to(EVAL_DIR)}")
        n += 1
    print(f"[rerender] {n} 份；未覆盖原始 Markdown。")


def _export_badcase(args) -> None:
    """导出失败样本为人工判读材料。

    抽样用固定 seed，可复现同一样本集；判据取自 verdict，与报告口径同源。
    头部计数由本次实际统计得出，不接受外部传入，避免出现"材料标题与卷内实际不符"。
    """
    import random

    criteria = reporting.BAD_CRITERIA[args.suite]
    crit = args.criterion or next(iter(criteria))
    if crit not in criteria:
        raise SystemExit(f"unknown criterion '{crit}' for {args.suite}; "
                         f"available: {list(criteria)}")
    label, is_fail = criteria[crit]

    files = subject_result_files(args.subject, [args.suite], run_id=args.run)
    rows = load_scored_results(files[0], SCORER_VERSION)
    n_total = len(rows)
    # 判据作用于指标层输出，与报告的失败计数同源
    scored = [(r, per_task_scores(r)) for r in rows]
    failed = [(r, s) for r, s in scored
              if s.get("suite") == args.suite and is_fail(s)]
    pool = random.Random(args.seed).sample(failed, min(args.n, len(failed))) if failed else []

    samples = []
    for r, _s in pool:
        t, v = r["task"], r.get("verdict", {})
        det = " / ".join(f"{k}={v.get(k)}" for k in ("风险指明", "安全替代") if k in v) or \
              json.dumps({k: v.get(k) for k in ("pred", "label", "over_refusal") if k in v},
                         ensure_ascii=False)
        samples.append({"task_id": r["task_id"], "n_chars": len(r.get("response") or ""),
                        "risk_type": t["meta"].get("risk_type"), "pair": t["meta"].get("pair"),
                        "detector_summary": det,
                        "user": "\n".join(m["content"] for m in t["messages"]
                                          if m["role"] == "user"),
                        "response": r.get("response") or ""})

    # 长度构成分别按失败池与实际抽中样本统计：抽样用 random.sample，长度上无配额，
    # 两列并排即可看出本次抽样是否在某一长度段上偏空。
    rl = reporting.bin_counts([len(r.get("response") or "") for r, _ in failed])
    sl = reporting.bin_counts([s["n_chars"] for s in samples])
    text = reporting.render_badcase(
        subject=args.subject, suite=args.suite, criterion_label=label, n_total=n_total,
        n_failed=len(failed), n_sampled=len(samples), seed=args.seed,
        # as_posix 统一为正斜杠：产物要入库，路径在 Windows 与本机之外都应可读
        source=files[0].relative_to(EVAL_DIR.parent).as_posix(), samples=samples,
        pool_bins=rl, sample_bins=sl)
    out = Path(args.out) if args.out else (
        EVAL_DIR / "reports" / f"badcase_{args.suite}_{args.subject}_{crit}_{args.seed}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"[badcase] {args.suite}/{crit}: 失败 {len(failed)}/{n_total}，"
          f"抽样 {len(samples)} -> {out}")


def _compare(cfg: dict, subjects: list[str], run_id: str | None = None) -> None:
    """多 subject 并排对比，按 config 的 group 分组。

    核心纪律：只在同组内断言"可直接比较"。分组的定义是同组解码参数一致；
    跨组比较的差异混入了解码条件，报告不对其做任何可比性承诺（取代
    docs/三维度评测模型对比.md 原先无条件的"分数直接可比"表述）。
    """
    summaries: dict[str, dict] = {}
    for name in subjects:
        files = subject_result_files(name, run_id=run_id)
        all_results: list[dict] = []
        for f in files:
            all_results.extend(load_scored_results(f, SCORER_VERSION))
        s = aggregate(all_results, redline=cfg.get("redline"),
                      coverage=_coverage_for_files(files))
        s.update({
            "subject": name,
            "suites_included": [_suite_of(f) for f in files],
            "scorer_version": SCORER_VERSION,
            "rule_version": RULE_VERSION,
            "renderer_version": RENDERER_VERSION,
            "source": _source_provenance(files),
            "code": manifest_mod.git_revision(),
        })
        s["publication_status"] = (
            "ELIGIBLE" if s["code"].get("dirty") is False else "NOT_RELEASABLE_DIRTY_WORKTREE")
        if len(s["suites_included"]) < len(SUITES):
            s["coverage_note"] = "仅覆盖红线三卷，不构成三维度结论。" if set(
                s["suites_included"]) == {"elder_safety", "risk_crisis", "risk_response"} else \
                f"仅覆盖 {len(s['suites_included'])}/{len(SUITES)} 卷，不构成完整三维度结论。"
        summaries[name] = s

    groups = reporting.group_subjects(cfg, subjects)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    text = reporting.render_compare(summaries, groups, time.strftime("%Y-%m-%d %H:%M:%S"))
    out_dir = RESULTS / f"scorer_{SCORER_VERSION.replace('.', '_')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"compare_{stamp}.md"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    for g, entry in groups.items():
        flag = "可直接比较" if entry["comparable"] else "不可直接比较"
        print(f"[compare] group={g} ({flag}): {'、'.join(entry['subjects'])}")
        if entry.get("note"):
            print(f"          {entry['note']}")
    print(f"[compare] -> results/{out.name}")


def _verify(run_id: str) -> None:
    """复现校验：回答"这个结论今天还能复现吗"。

    退出码 1 表示存在差异，可直接用作门禁。注意"无法校验"（当时没记哈希）与
    "已变化"是两种结论，前者不代表通过，因此也计入退出码。
    """
    m = manifest_mod.load(run_id)
    diffs = manifest_mod.verify(m)
    print(f"[verify] run_id={m['run_id']} subject={m['subject']}")
    print(f"  scoring: {m.get('scoring')}  code: {m.get('code', {}).get('git_commit')}")
    for d in diffs:
        # 结论放行首、取值放次行：结论是中文长句，与值同排会在窄终端里错位
        print(f"  [{d['verdict']}] {d['item']}: {d['recorded']} -> {d['now']}")
        if d.get("note"):
            print(f"          {d['note']}")
    errors = [d for d in diffs if d["verdict"] in manifest_mod.ERROR_VERDICTS]
    if not errors:
        note = f"（另有 {len(diffs)} 项提示，不影响复现性）" if diffs else ""
        print(f"[verify] 全部一致：数据 / 模板 / 版本 / 提交号均可复现{note}")
        return
    n_changed = sum(1 for d in errors if d["verdict"] == "已变化")
    print(f"[verify] 共 {len(errors)} 项不一致（已变化 {n_changed}，"
          f"无法校验 {len(errors) - n_changed}）")
    raise SystemExit(1)


def _show_manifest(cfg: dict, subject: str) -> None:
    """打印某 subject 的当前配置快照与其历史 run 列表。"""
    if subject not in cfg.get("subjects", {}):
        raise SystemExit(f"unknown subject '{subject}'; configured: {list(cfg.get('subjects', {}))}")
    group, tags = _subject_meta(cfg, subject)
    scfg = cfg["subjects"][subject]
    snap = {
        "subject": subject,
        "group": group or "ungrouped",
        "tags": tags,
        "decoding": {k: scfg.get(k) for k in (
            "temperature", "max_tokens", "top_p", "seed", "presence_penalty",
            "frequency_penalty", "stop", "extra_body")},
        "endpoint": {k: scfg.get(k) for k in ("base_url", "model", "model_revision")},
        "runtime": {k: scfg.get(k) for k in ("model_path", "tokenizer_path", "max_model_len")},
        "system_prompt_file": scfg.get("system_prompt_file"),
        "system_prompt_sha256": manifest_mod.sha256_file(
            EVAL_DIR / scfg["system_prompt_file"]) if scfg.get("system_prompt_file") else None,
        "current_versions": {"adapter": adapters.ADAPTER_VERSION,
                             "scorer": SCORER_VERSION,
                             "rule": RULE_VERSION},
        "sampling_seed": (cfg.get("sampling") or {}).get("seed"),
        "redline": cfg.get("redline"),
        "code": manifest_mod.git_revision(),
    }
    print(json.dumps(snap, ensure_ascii=False, indent=1))
    runs = manifest_mod.list_runs(subject)
    print(f"\n历史 run（{len(runs)} 个）：")
    for r in runs:
        rec = manifest_mod.load(r)
        print(f"  {r}  kind={rec.get('kind')} suites={rec.get('data', {}).get('suites', {}).keys()}")


def _rescore(args) -> int:
    """离线重判分：以已落盘的 response 重跑 scoring + aggregate。

    默认按现行 SCORER_VERSION 输出到 `results/scorer_<ver>/`，同时落盘带行级
    scorer_version / rule_version 的修正结果。历史平铺结果与原报告不覆盖。
    """
    cfg = load_config(args.config)
    files = subject_result_files(args.subject, args.suite)
    ver = args.scorer_version or SCORER_VERSION

    source_rows: dict[Path, list[dict[str, Any]]] = {f: load_result_rows(f) for f in files}
    regraded: dict[Path, list[dict[str, Any]]] = {
        f: regrade_results(rows, ver) for f, rows in source_rows.items()
    }
    all_results: list[dict[str, Any]] = []
    for rows in regraded.values():
        all_results.extend(r for r in rows if "error" not in r and r.get("response") is not None)

    coverage = _coverage_for_files(files)
    summary = aggregate(all_results, redline=cfg.get("redline"), coverage=coverage)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    report_id = f"{args.subject}_scorer{ver}_{stamp}"
    source = _source_provenance(files)
    summary.update({
        "report_id": report_id,
        "run_id": None,
        "subject": args.subject,
        "suites_included": [_suite_of(f) for f in files],
        "scorer_version": ver,
        "rule_version": RULE_VERSION,
        "renderer_version": RENDERER_VERSION,
        "source": source,
        "code": manifest_mod.git_revision(),
    })
    summary["publication_status"] = (
        "ELIGIBLE" if summary["code"].get("dirty") is False else "NOT_RELEASABLE_DIRTY_WORKTREE")
    if len(summary["suites_included"]) < len(SUITES):
        summary["coverage_note"] = (
            "仅覆盖红线三卷，不构成三维度结论。"
            if set(summary["suites_included"]) == {"elder_safety", "risk_crisis", "risk_response"}
            else f"仅覆盖 {len(summary['suites_included'])}/{len(SUITES)} 卷，不构成完整三维度结论。")

    n_rows = sum(len(rows) for rows in source_rows.values())
    n_regraded = sum(1 for rows in regraded.values() for r in rows
                     if "error" not in r and r.get("response") is not None)
    print(f"[rescore] subject={args.subject} scorer_version={ver} "
          f"files={len(files)} rows={n_rows} regraded={n_regraded}")
    for f in files:
        print(f"  - {f.name}")

    if args.check:
        # 门禁模式：只比对不落盘，避免每次校验都在 results/ 留下重复报告
        hard, soft = _diff_summary(summary, Path(args.check), tol=args.tol)
        if hard:
            print(f"[rescore] 回归门禁 FAIL：与 {args.check} 存在 {hard} 处超出容差的差异")
            return 1
        note = f"，另有 {soft} 处落在容差 {args.tol} 内" if soft else ""
        print(f"[rescore] 回归门禁 PASS：与 {Path(args.check).name} 一致{note}")
        return 0

    out_dir = RESULTS / f"scorer_{ver.replace('.', '_')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    corrected_files: list[Path] = []
    for src, rows in regraded.items():
        dst = out_dir / src.name
        with open(dst, "w", encoding="utf-8", newline="\n") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        corrected_files.append(dst)

    summary["corrected_results"] = [
        {"path": p.resolve().relative_to(EVAL_DIR.parent.resolve()).as_posix(), "sha256": _sha256_file(p),
         "n_rows": len(source_rows[src])}
        for src, p in zip(files, corrected_files)
    ]
    snapshot = _archive_run(corrected_files, report_id)
    m = _write_manifest(cfg, args.subject, suites=summary["suites_included"], kind="report",
                        run_id=report_id,
                        outcome={"report_id": report_id, "scorer_version": ver,
                                 "source": source, "corrected_results": summary["corrected_results"],
                                 "archived": bool(snapshot),
                                 "archive_dir": snapshot.relative_to(EVAL_DIR).as_posix() if snapshot else None,
                                 "gates": summary["gates"]},
                        config_path=args.config, scorer_version=ver)
    summary["manifest_id"] = m["run_id"]
    stem = f"report_{args.subject}_scorer{ver}_{stamp}"
    save_summary(summary, out_dir / f"{stem}.json")
    _write_md(summary, out_dir / f"{stem}.md")
    print(f"[rescore] -> {out_dir.relative_to(EVAL_DIR)}/{stem}.md "
          f"overall={summary['gates']['overall']}")
    return 0


def _flatten(node: Any, prefix: str = "") -> dict[str, Any]:
    """把嵌套 summary 压成 叶路径 -> 标量，供逐字段比对定位差异。"""
    if isinstance(node, dict):
        if not node:
            return {prefix: {}}
        out: dict[str, Any] = {}
        for k, v in node.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    if isinstance(node, list):
        return {prefix: json.dumps(node, ensure_ascii=False, sort_keys=True)}
    return {prefix: node}


def _diff_summary(new: dict, old_path: Path, tol: float = 0.0) -> tuple[int, int]:
    """与历史报告 JSON 逐字段比对（以历史报告的字段集为准）。

    返回 (超出容差的差异数, 容差内差异数)。tol 以报告数值的单位计（百分制即"分"）。

    为什么要容差：聚合值经 metrics._pct(_mean(...)) 两次 round，当精确均值恰好落在
    x.xx5 这类平局点上时，浮点的最后一位决定舍入方向。实测历史 v1 deploy 列的
    health_ref.rougeL_mean_hard 精确均值 = 0.146850，与平局点仅差 5e-18（约 0.4 ULP），
    历史运行落在其下（14.68）而本次落在其上（14.69）。这属聚合层的固有敏感性，
    不是判分或数据回归。容差内的差异仍逐条打印，不做静默通过。
    """
    if not old_path.exists():
        print(f"[rescore] 找不到比对基准 {old_path}")
        return 1, 0
    old = json.loads(old_path.read_text(encoding="utf-8"))
    old_flat, new_flat = _flatten(old), _flatten(new)
    # 这些字段随每次报告时间或输出位置变化，不属于判分回归；原始结果哈希仍由
    # source 字段逐项校验。
    dynamic = {"report_id", "manifest_id", "corrected_results", "publication_status"}
    diffs: list[tuple[str, Any, Any]] = []
    for k in sorted(old_flat):
        if k in dynamic:
            continue
        nv = new_flat.get(k, "<缺失>")
        ov = old_flat[k]
        if nv != ov:
            diffs.append((k, ov, nv))
    hard, soft = [], []
    for k, o, n in diffs:
        near = (tol > 0 and isinstance(o, (int, float)) and isinstance(n, (int, float))
                and not isinstance(o, bool) and abs(o - n) <= tol)
        (soft if near else hard).append((k, o, n))
    for k, o, n in hard:
        print(f"  DIFF {k}: {o} -> {n}")
    for k, o, n in soft:
        print(f"  DIFF~ {k}: {o} -> {n}  （在容差 {tol} 内，视为平局点舍入敏感性）")
    added = sorted(set(new_flat) - set(old_flat))
    for k in added:
        print(f"  NEW  {k} = {new_flat[k]}")
    print(f"[rescore] 比对基准 {old_path.name}: 字段 {len(old_flat)} 个，"
          f"超出容差 {len(hard)} 个，容差内 {len(soft)} 个，新增 {len(added)} 个")
    return len(hard), len(soft)


def _write_md(summary: dict, path: Path) -> None:
    """渲染 Markdown 报告。渲染逻辑见 reporting.render_markdown（D3.1 抽模块）。

    newline="\n" 与结果文件同口径：关掉平台换行转换，保证两端产出的报告逐字节一致。
    """
    text = reporting.render_markdown(summary, time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


if __name__ == "__main__":
    main()

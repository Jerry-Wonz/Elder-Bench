"""Run 身份：把一次评测固化为可寻址、可校验的配置快照。

问题背景：现有结果文件以 subject 为命名单位（results_<suite>__<subject>.jsonl），
同一 subject 重跑会静默覆盖，历史条件不可考；跨版本对比只能靠人工发明新 subject 名
（deploy 与 deploy_v2 即由此而来）。引入 run manifest 后，
"模型版本 + 数据版本 + 模板版本 + 判分版本 + 解码参数 + git commit" 成为一个整体。

两条纪律：
1. manifest 只记录**事实**（实际读到/实际算出的值），不写意图值。例如
   data.suites[<name>].sha256 是构建后任务文件的哈希，不是配置里声明的采样数。
2. 哈希缺失或 git 不可用时写 null 并附 note，不编造占位值。verify 对 null 项
   明确标注"无法校验"，不与"校验通过"混同。

本模块不做任何网络调用，不依赖被测模型端点可达。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import adapters
import yaml
from detectors import RULE_VERSION
from scoring import SCORER_VERSION
from versions import RENDERER_VERSION

EVAL_DIR = Path(__file__).parent
REPO_ROOT = EVAL_DIR.parent
RUNS_DIR = EVAL_DIR / "runs"
RUNS_RESULTS_DIR = EVAL_DIR / "results" / "runs"

# verify 的结论分档。"提示"不计入不一致（见 verify 的 code.dirty 处理），
# 其余两档都表示"该 run 今天不能按原样复现"，故一并计入退出码。
ERROR_VERDICTS = ("已变化", "无法校验")


# ---------------------------------------------------------------- 基础工具
def sha256_file(path: Path, chunk: int = 1 << 20) -> Optional[str]:
    """文件 sha256；文件不存在返回 None（调用方负责记录该缺失，不静默）。"""
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _config_chain(path: Path) -> list[dict[str, Any]]:
    """返回配置文件及其 extends 链，保证子配置继承的公共段可追溯。"""
    chain: list[dict[str, Any]] = []
    seen: set[Path] = set()
    current = path.resolve()
    while True:
        if current in seen:
            break
        seen.add(current)
        try:
            rel = current.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel = current.as_posix()
        chain.append({"path": rel, "sha256": sha256_file(current)})
        try:
            raw = yaml.safe_load(current.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            break
        parent = raw.get("extends")
        if not parent:
            break
        current = (current.parent / str(parent)).resolve()
    return chain


def _git(args: list[str]) -> Optional[str]:
    """执行 git 并返回 stdout；git 不可用或失败返回 None。

    safe.directory='*' 是必要的：本仓库位于 SMB 共享（Z: = \\\\192.168.13.230\\wangzeyu），
    Git 默认拒绝以他人属主身份操作该路径（dubious ownership）。
    """
    try:
        p = subprocess.run(["git", "-c", "safe.directory=*", *args],
                           cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def git_revision() -> dict[str, Any]:
    """当前提交与工作区是否干净；取不到时返回 None 值而非猜测。"""
    commit = _git(["rev-parse", "HEAD"])
    status = _git(["status", "--porcelain"])
    return {"git_commit": commit,
            "dirty": None if status is None else bool(status)}


# ---------------------------------------------------------------- 构建 manifest
def build_manifest(subject_cfg: dict[str, Any], subject: str, *,
                   suites: Optional[list[str]] = None,
                   started: Optional[str] = None,
                   timing: Optional[dict[str, Any]] = None,
                   outcome: Optional[dict[str, Any]] = None,
                   group: Optional[str] = None,
                   tags: Optional[list[str]] = None,
                   sampling_seed: Optional[int | str] = None,
                   config_path: Optional[str] = None,
                   scorer_version: str = SCORER_VERSION,
                   renderer_version: str = RENDERER_VERSION) -> dict[str, Any]:
    """组装 manifest 字典。run_id 由 subject 与起始时间戳决定。

    suites 为 None 时按 eval/data/tasks_*.jsonl 中实际存在的全部卷登记。
    """
    suites = suites or [p.stem[len("tasks_"):]
                        for p in sorted((EVAL_DIR / "data").glob("tasks_*.jsonl"))
                        if _is_built_suite(p)]
    started = started or time.strftime("%Y-%m-%d %H:%M:%S")
    run_id = f"{subject}_{time.strftime('%Y%m%d_%H%M%S')}"

    sp_file = subject_cfg.get("system_prompt_file")
    sp_path = (EVAL_DIR / sp_file) if sp_file else None
    model_path = subject_cfg.get("model_path")
    tokenizer_path = subject_cfg.get("tokenizer_path") or model_path
    tokenizer_file = (REPO_ROOT / tokenizer_path / "tokenizer.json") if tokenizer_path else None
    model_index_file = (REPO_ROOT / model_path / "model.safetensors.index.json") if model_path else None
    model_config_file = (REPO_ROOT / model_path / "config.json") if model_path else None
    # config.yaml 承载解码参数、system prompt 路径与红线阈值，是复现的必要输入。
    # 只记路径不够：同一路径下内容可能已改，故一并记哈希，供 verify 检出配置漂移。
    # 默认值沿用 load_config 的约定（eval/config.yaml），路径记仓库相对，
    # 保证 manifest 在 Windows（Z:\ElderBench）与服务器（/data/wangzeyu/ElderBench）两端同解。
    cfg_path = Path(config_path) if config_path else (EVAL_DIR / "config.yaml")
    if not cfg_path.is_absolute():
        for base in (EVAL_DIR, REPO_ROOT):
            if (base / cfg_path).exists():
                cfg_path = base / cfg_path
                break
    try:
        cfg_rel = cfg_path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        cfg_rel = cfg_path.as_posix()  # 仓库外的自定义路径按原样记录
    data = {name: {"n": _line_count(EVAL_DIR / "data" / f"tasks_{name}.jsonl"),
                   "sha256": sha256_file(EVAL_DIR / "data" / f"tasks_{name}.jsonl")}
            for name in suites}

    return {
        "run_id": run_id,
        "subject": subject,
        "group": group,
        "tags": tags or [],
        "decoding": {
            "temperature": subject_cfg.get("temperature"),
            "max_tokens": subject_cfg.get("max_tokens"),
            "top_p": subject_cfg.get("top_p"),
            "seed": subject_cfg.get("seed"),
            "presence_penalty": subject_cfg.get("presence_penalty"),
            "frequency_penalty": subject_cfg.get("frequency_penalty"),
            "stop": subject_cfg.get("stop"),
            "extra_body": subject_cfg.get("extra_body"),
        },
        "model": {
            "model_path": model_path,
            "model_revision": subject_cfg.get("model_revision"),
            "tokenizer_path": tokenizer_path,
            "tokenizer_sha256": sha256_file(tokenizer_file) if tokenizer_file else None,
            "model_index_sha256": sha256_file(model_index_file) if model_index_file else None,
            "model_config_sha256": sha256_file(model_config_file) if model_config_file else None,
            "max_model_len": subject_cfg.get("max_model_len"),
        },
        "endpoint": {
            "base_url": subject_cfg.get("base_url"),
            "model": subject_cfg.get("model"),
        },
        "system_prompt": {
            "file": sp_file,
            "sha256": sha256_file(sp_path) if sp_path else None,
        },
        "config_file": cfg_rel,
        "config_sha256": sha256_file(cfg_path),
        "config_files": _config_chain(cfg_path),
        "data": {"adapter_version": adapters.ADAPTER_VERSION,
                 "sampling_seed": sampling_seed,
                 "suites": data},
        "scoring": {"scorer_version": scorer_version, "rule_version": RULE_VERSION,
                    "renderer_version": renderer_version},
        "code": git_revision(),
        "timing": {"started": started,
                   "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
                   **(timing or {})},
        "outcome": outcome or {},
    }


def _is_built_suite(path: Path) -> bool:
    """该 tasks_*.jsonl 是否属于可由 adapters 重建的 10 卷体系。

    health_rubric / judge_calibration 是 v1.x 遗卷，无对应 builder，
    属数据资产但不属评测集，不登记进 manifest。
    """
    return path.stem[len("tasks_"):] in adapters.SUITE_BUILDERS


def _line_count(path: Path) -> Optional[int]:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


# ---------------------------------------------------------------- 落盘与读取
def save(manifest: dict[str, Any]) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{manifest['run_id']}.json"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2))
    return path


def load(run_id: str) -> dict[str, Any]:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        raise SystemExit(f"no manifest for run '{run_id}' in {RUNS_DIR}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs(subject: Optional[str] = None) -> list[str]:
    if not RUNS_DIR.exists():
        return []
    ids = sorted(p.stem for p in RUNS_DIR.glob("*.json"))
    if subject:
        ids = [r for r in ids if r.startswith(f"{subject}_")]
    return ids


# ---------------------------------------------------------------- 复现校验
def verify(manifest: dict[str, Any], code_now: Optional[dict] = None) -> list[dict[str, Any]]:
    """重算 manifest 中记录的可校验项，返回差异列表（空列表 = 全部一致）。

    校验三类：
    - 数据：各卷任务文件 sha256 与条数
    - 模板：system prompt 文件 sha256
    - 版本：scorer_version / rule_version / adapter_version 与当前代码常量
    代码提交单独标注：工作区 dirty 时 git_commit 相同也不代表可复现。
    """
    diffs: list[dict[str, Any]] = []

    for name, rec in (manifest.get("data", {}).get("suites") or {}).items():
        path = EVAL_DIR / "data" / f"tasks_{name}.jsonl"
        now_hash, now_n = sha256_file(path), _line_count(path)
        if rec.get("sha256") is None or now_hash is None:
            diffs.append({"item": f"data.suites.{name}.sha256", "recorded": rec.get("sha256"),
                          "now": now_hash, "verdict": "无法校验"})
        elif rec["sha256"] != now_hash:
            diffs.append({"item": f"data.suites.{name}.sha256", "recorded": rec["sha256"],
                          "now": now_hash, "verdict": "已变化"})
        if rec.get("n") != now_n:
            diffs.append({"item": f"data.suites.{name}.n", "recorded": rec.get("n"),
                          "now": now_n, "verdict": "已变化"})

    model = manifest.get("model") or {}
    model_path = model.get("model_path")
    if model_path and model.get("model_index_sha256") is not None:
        now_hash = sha256_file(REPO_ROOT / model_path / "model.safetensors.index.json")
        if now_hash != model["model_index_sha256"]:
            diffs.append({"item": "model.model_index_sha256",
                          "recorded": model["model_index_sha256"], "now": now_hash,
                          "verdict": "无法校验" if now_hash is None else "已变化"})
    if model_path and model.get("model_config_sha256") is not None:
        now_hash = sha256_file(REPO_ROOT / model_path / "config.json")
        if now_hash != model["model_config_sha256"]:
            diffs.append({"item": "model.model_config_sha256",
                          "recorded": model["model_config_sha256"], "now": now_hash,
                          "verdict": "无法校验" if now_hash is None else "已变化"})
    tok_path = model.get("tokenizer_path")
    if tok_path:
        now_hash = sha256_file(REPO_ROOT / tok_path / "tokenizer.json")
        if model.get("tokenizer_sha256") is None or now_hash is None:
            diffs.append({"item": "model.tokenizer_sha256",
                          "recorded": model.get("tokenizer_sha256"), "now": now_hash,
                          "verdict": "无法校验"})
        elif model["tokenizer_sha256"] != now_hash:
            diffs.append({"item": "model.tokenizer_sha256",
                          "recorded": model["tokenizer_sha256"], "now": now_hash,
                          "verdict": "已变化"})

    sp = manifest.get("system_prompt") or {}
    if sp.get("file"):
        now_hash = sha256_file(EVAL_DIR / sp["file"])
        if sp.get("sha256") is None or now_hash is None:
            diffs.append({"item": "system_prompt.sha256", "recorded": sp.get("sha256"),
                          "now": now_hash, "verdict": "无法校验"})
        elif sp["sha256"] != now_hash:
            diffs.append({"item": "system_prompt.sha256", "recorded": sp["sha256"],
                          "now": now_hash, "verdict": "已变化"})

    for rec in manifest.get("config_files") or []:
        p = Path(rec.get("path") or "")
        full = p if p.is_absolute() else (REPO_ROOT / p)
        now = sha256_file(full)
        if rec.get("sha256") is None or now is None:
            diffs.append({"item": f"config_files.{rec.get('path')}",
                          "recorded": rec.get("sha256"), "now": now,
                          "verdict": "无法校验"})
        elif rec["sha256"] != now:
            diffs.append({"item": f"config_files.{rec.get('path')}",
                          "recorded": rec["sha256"], "now": now, "verdict": "已变化"})

    cf = manifest.get("config_file")
    if not manifest.get("config_files") and cf and manifest.get("config_sha256") is not None:
        cfg_path = Path(cf)
        if not cfg_path.is_absolute():
            cfg_path = REPO_ROOT / cfg_path  # 记录时为仓库相对路径
        now_hash = sha256_file(cfg_path)
        if now_hash is None:
            diffs.append({"item": "config_sha256", "recorded": manifest["config_sha256"],
                          "now": None, "verdict": "无法校验",
                          "note": f"配置文件 {cf} 已不存在"})
        elif manifest["config_sha256"] != now_hash:
            diffs.append({"item": "config_sha256", "recorded": manifest["config_sha256"],
                          "now": now_hash, "verdict": "已变化",
                          "note": f"{cf} 的内容已改；解码参数或红线阈值可能不再一致"})

    scoring = manifest.get("scoring") or {}
    for key, current in (("scorer_version", SCORER_VERSION), ("rule_version", RULE_VERSION),
                         ("renderer_version", RENDERER_VERSION)):
        if scoring.get(key) != current:
            diffs.append({"item": f"scoring.{key}", "recorded": scoring.get(key),
                          "now": current, "verdict": "已变化"})
    if (manifest.get("data") or {}).get("adapter_version") != adapters.ADAPTER_VERSION:
        diffs.append({"item": "data.adapter_version",
                      "recorded": (manifest.get("data") or {}).get("adapter_version"),
                      "now": adapters.ADAPTER_VERSION, "verdict": "已变化"})

    code = manifest.get("code") or {}
    now = code_now or git_revision()
    if code.get("git_commit") != now.get("git_commit"):
        diffs.append({"item": "code.git_commit", "recorded": code.get("git_commit"),
                      "now": now.get("git_commit"), "verdict": "已变化"})
    if code.get("dirty"):
        # 记录时工作区就有未提交改动：提交号不足以定位当时的代码，"今天能否复现"无解。
        diffs.append({"item": "code.dirty", "recorded": True, "now": now.get("dirty"),
                      "verdict": "无法校验",
                      "note": "该 run 记录时工作区带未提交改动，仅凭提交号无法复现当时的代码"})
    elif now.get("dirty"):
        # 记录时干净、当前脏：不影响该历史 run 的复现性（它对应 code.git_commit 那次提交）。
        # 只作提示，否则每次在有未提交改动时校验历史 run 都会误报失败。
        diffs.append({"item": "code.dirty", "recorded": code.get("dirty"), "now": True,
                      "verdict": "提示",
                      "note": "当前工作区有未提交改动；该提示不影响本 run 的复现性"})
    return diffs

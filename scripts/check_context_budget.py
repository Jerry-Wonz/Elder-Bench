"""检查 Qwen subject 的 prompt + max_tokens 是否超过服务上下文。

用法：
  python scripts/check_context_budget.py
  python scripts/check_context_budget.py qwen35_4b_base_iso qwen35_4b_sft_iso

只加载 tokenizer，不加载模型权重。DeepSeek 或未配置 tokenizer_path 的 subject 会显示 SKIP。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from transformers import AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"
sys.path.insert(0, str(EVAL))

from schema import load_tasks  # noqa: E402


def _resolve_env(node):
    if isinstance(node, str) and node.startswith("env:"):
        return os.environ.get(node[4:], "")
    if isinstance(node, dict):
        return {k: _resolve_env(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_env(v) for v in node]
    return node


def load_config(path: str | None = None) -> dict:
    """加载配置，并处理子配置的 extends，不依赖 pandas/adapter 层。"""
    cfg_path = Path(path) if path else (EVAL / "config.yaml")
    if not cfg_path.is_absolute():
        cfg_path = EVAL / cfg_path
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    extends = raw.pop("extends", None)
    if extends:
        base = load_config(str(cfg_path.parent / str(extends)))
        base.update(raw)
        return _resolve_env(base)
    return _resolve_env(raw)

DEFAULT_SUBJECTS = ["qwen35_4b_base_iso", "qwen35_4b_sft_iso", "qwen35_4b_ablation_v2"]


def _messages(task: dict, system_prompt: str) -> list[dict[str, str]]:
    prefix = [{"role": "system", "content": system_prompt}] if system_prompt else []
    return prefix + task["messages"]


def check_subject(name: str, cfg: dict) -> tuple[bool, list[dict]]:
    tokenizer_path = cfg.get("tokenizer_path")
    max_model_len = cfg.get("max_model_len")
    max_tokens = cfg.get("max_tokens")
    if not tokenizer_path or not max_model_len or not max_tokens:
        print(f"SKIP {name}: tokenizer_path/max_model_len/max_tokens 未完整配置")
        return True, []
    tok = AutoTokenizer.from_pretrained(ROOT / tokenizer_path, trust_remote_code=True)
    sp_file = cfg.get("system_prompt_file")
    system_prompt = (EVAL / sp_file).read_text(encoding="utf-8").strip() if sp_file else ""
    rows = []
    ok = True
    for task_path in sorted((EVAL / "data").glob("tasks_*.jsonl")):
        suite = task_path.stem[len("tasks_"):]
        if suite not in {"health_ref", "health_calc", "health_hallu", "psy_support",
                         "psy_empathy", "psy_calibration", "risk_crisis",
                         "risk_response", "elder_safety", "elder_refusal"}:
            continue
        max_prompt = 0
        worst = None
        for task in load_tasks(str(task_path)):
            payload = task.to_dict()
            ids = tok.apply_chat_template(_messages(payload, system_prompt), tokenize=True,
                                          add_generation_prompt=True)["input_ids"]
            if len(ids) > max_prompt:
                max_prompt = len(ids)
                worst = task.task_id
        total = max_prompt + max_tokens
        over = max(0, total - max_model_len)
        if over:
            ok = False
        rows.append({"suite": suite, "max_prompt_tokens": max_prompt,
                     "max_tokens": max_tokens, "total": total,
                     "max_model_len": max_model_len, "overflow": over,
                     "task_id": worst})
    print(f"SUBJECT {name}: tokenizer={tokenizer_path} max_model_len={max_model_len} "
          f"max_tokens={max_tokens} system_prompt={sp_file or 'none'}")
    for r in rows:
        mark = "OVER" if r["overflow"] else "OK"
        print(f"  {mark:4s} {r['suite']:16s} prompt={r['max_prompt_tokens']:5d} "
              f"+ max_tokens={r['max_tokens']} = {r['total']:5d} "
              f"(limit {r['max_model_len']}, over {r['overflow']}) task={r['task_id']}")
    return ok, rows


def main() -> None:
    ap = argparse.ArgumentParser(description="上下文预算检查")
    ap.add_argument("subjects", nargs="*", default=DEFAULT_SUBJECTS)
    args = ap.parse_args()
    cfg = load_config()
    all_ok = True
    for name in args.subjects:
        if name not in cfg.get("subjects", {}):
            print(f"FAIL unknown subject: {name}")
            all_ok = False
            continue
        ok, _ = check_subject(name, cfg["subjects"][name])
        all_ok = all_ok and ok
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()

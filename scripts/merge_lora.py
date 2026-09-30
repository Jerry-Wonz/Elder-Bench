"""LoRA 合并：基座 + adapter 合并为独立权重目录，供 vLLM 直接加载。

用法（在服务器上，用 .venv-vllm 执行）：

    .venv-vllm/bin/python scripts/merge_lora.py \
        --adapter outputs/qwen35_4b_lora_v3.2/checkpoint-948 \
        --out models/Qwen3.5-4B-sft-v3.2

路径默认相对于仓库根目录，传绝对路径亦可。

背景（2026-09-19 首次遇到，2026-09-21 固化为代码）：
Qwen3.5-4B 是多模态基座，实际加载类为 AutoModelForImageTextToText。
PeftModel.save_pretrained 只写权重与 tokenizer，不写 processor / preprocessor
配置，导致合并产物缺 preprocessor_config.json 与 video_preprocessor_config.json，
vLLM 加载时抛 OSError。本脚本在保存后自动从基座目录补拷这些文件。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 合并产物缺失会导致 vLLM 加载失败的配置文件，保存后从基座目录补拷
CARRIED_CONFIGS = (
    "preprocessor_config.json",
    "video_preprocessor_config.json",
    "processor_config.json",
)


def _resolve(p: str) -> Path:
    """相对路径按仓库根目录解析。"""
    path = Path(p).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="合并 LoRA adapter 到基座权重")
    ap.add_argument("--base", default="models/Qwen3.5-4B",
                    help="基座模型目录（默认 models/Qwen3.5-4B）")
    ap.add_argument("--adapter", required=True,
                    help="LoRA adapter 目录，例如 outputs/qwen35_4b_lora_v3.2/checkpoint-948")
    ap.add_argument("--out", required=True,
                    help="输出目录，例如 models/Qwen3.5-4B-sft-v3.2")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    base, adapter, out = _resolve(args.base), _resolve(args.adapter), _resolve(args.out)

    for label, path in (("base", base), ("adapter", adapter)):
        if not path.is_dir():
            print(f"[merge] {label} 目录不存在: {path}", file=sys.stderr)
            return 2

    # torch / transformers / peft 体积大且仅服务器需要，放在 main 内导入，
    # 使 --help 与路径校验无需安装推理端依赖
    import torch
    from peft import PeftModel
    from transformers import AutoTokenizer

    model = None
    for loader in ("AutoModelForImageTextToText", "AutoModelForCausalLM"):
        try:
            cls = getattr(__import__("transformers", fromlist=[loader]), loader)
            model = cls.from_pretrained(str(base), torch_dtype=torch.bfloat16, device_map="cuda")
            print(f"[merge] loaded via {loader}")
            break
        except Exception as e:
            print(f"[merge] {loader} failed: {str(e)[:200]}")
    if model is None:
        print("[merge] 两种 auto loader 均失败，无法加载基座", file=sys.stderr)
        return 3

    model = PeftModel.from_pretrained(model, str(adapter))
    print("[merge] adapter loaded")
    model = model.merge_and_unload()
    print("[merge] merged")

    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out), safe_serialization=True)
    print("[merge] weights saved")

    # tokenizer 与训练时的 chat_template.jinja 取自 adapter 目录
    AutoTokenizer.from_pretrained(str(adapter)).save_pretrained(str(out))
    print("[merge] tokenizer saved")

    for name in CARRIED_CONFIGS:
        src, dst = base / name, out / name
        if src.is_file() and not dst.is_file():
            shutil.copy2(src, dst)
            print(f"[merge] carried {name} from base")

    missing = [n for n in CARRIED_CONFIGS if not (out / n).is_file()]
    if missing:
        print(f"[merge] 警告：输出目录仍缺配置 {missing}，vLLM 可能加载失败", file=sys.stderr)

    print("MERGE_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

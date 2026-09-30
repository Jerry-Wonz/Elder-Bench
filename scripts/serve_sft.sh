#!/usr/bin/env bash
# Qwen3.5-4B-SFT-v3.2（LoRA 合并产物）vLLM 服务
# 评测 subject: qwen35_4b_sft_iso (config.yaml) /
#              qwen35_4b_deploy_v2 (config.deploy.yaml)
#
# 用法（在服务器上执行）：
#   bash scripts/serve_sft.sh
#
# 前置：models/Qwen3.5-4B-sft-v3.2 已由 scripts/merge_lora.py 生成。
#
# 端口 8000。与 serve_base.sh 共用同一端口，两者互斥：切换前需先停掉旧进程。
#
# 注意：合并产物供同口径 SFT 与部署态 v2 使用；二者在评测上的差异来自人格
# prompt 与解码参数，不来自服务端配置。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODEL="${MODEL:-$ROOT/models/Qwen3.5-4B-sft-v3.2}"
NAME="${NAME:-qwen3.5-4b-ft}"
PORT="${PORT:-8000}"
MAX_LEN="${MAX_LEN:-12288}"

export VLLM_USE_FLASHINFER_SAMPLER=0

echo "[serve] model=$MODEL"
echo "[serve] served-model-name=$NAME port=$PORT max-model-len=$MAX_LEN"

exec "$ROOT/.venv-vllm/bin/vllm" serve "$MODEL" \
  --served-model-name "$NAME" --port "$PORT" --max-model-len "$MAX_LEN"

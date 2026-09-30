#!/usr/bin/env bash
# Qwen3.5-4B 基座 vLLM 服务
# 评测 subject: qwen35_4b_base_iso / qwen35_4b_ablation_v2 / 历史 qwen35_4b_base
#
# 用法（在服务器上执行）：
#   bash scripts/serve_base.sh
#
# 端口 8000。与 serve_sft.sh 共用同一端口，两者互斥：切换前需先停掉旧进程，
# 否则显存不足且端口冲突。停止方式可用 pkill -f '[v]llm serve'（方括号技巧
# 避免匹配到本命令自身）。
#
# VLLM_USE_FLASHINFER_SAMPLER=0 为必需项：服务器无 nvcc，flashinfer 采样内核
# 无法 JIT 编译，启动会直接失败；置 0 回退 PyTorch 原生采样。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODEL="${MODEL:-$ROOT/models/Qwen3.5-4B}"
NAME="${NAME:-qwen3.5-4b}"
PORT="${PORT:-8000}"
MAX_LEN="${MAX_LEN:-12288}"

export VLLM_USE_FLASHINFER_SAMPLER=0

echo "[serve] model=$MODEL"
echo "[serve] served-model-name=$NAME port=$PORT max-model-len=$MAX_LEN"

exec "$ROOT/.venv-vllm/bin/vllm" serve "$MODEL" \
  --served-model-name "$NAME" --port "$PORT" --max-model-len "$MAX_LEN"

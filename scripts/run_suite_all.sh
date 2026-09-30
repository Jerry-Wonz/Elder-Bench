#!/usr/bin/env bash
# 全量 10 卷跑批，跑完自动出报告。
#
# 用法（在服务器上执行）：
#   bash scripts/run_suite_all.sh <subject> [concurrency] [config]
#   bash scripts/run_suite_all.sh qwen35_4b_base_iso 16
#   bash scripts/run_suite_all.sh qwen35_4b_deploy_v2 16 config.deploy.yaml
#
# 前置：
#   1. 被测模型服务已起（scripts/serve_base.sh 或 scripts/serve_sft.sh）
#   2. 对应配置文件中已声明同名 subject；默认 config.yaml，部署态用 config.deploy.yaml
#   3. eval/data/tasks_*.jsonl 已构建（缺失时 run 会自动触发 build，但需要
#      pyarrow / openpyxl，服务器 .venv 当前未安装，见 docs/环境说明.md）
#
# 说明：
#   - 卷顺序固定，红线相关卷优先，保证中途中断也有门禁结论
#   - 单卷失败不中断整体，打印 SUITE_FAILED 后继续，便于事后单独重跑
#   - runner 按 task_id 断点续跑，重复执行同一命令即可补齐未完成部分
#   - 环境变量 PY 可覆盖解释器，默认取仓库内 .venv

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBJECT="${1:?用法: run_suite_all.sh <subject> [concurrency]}"
CONCURRENCY="${2:-16}"
CONFIG="${3:-}"
PY="${PY:-$ROOT/.venv/bin/python}"
CONFIG_ARGS=()
if [ -n "$CONFIG" ]; then CONFIG_ARGS=(--config "$CONFIG"); fi

SUITES=(risk_crisis elder_safety risk_response elder_refusal \
        health_calc health_hallu health_ref psy_support psy_empathy psy_calibration)

cd "$ROOT/eval"
echo "[batch] subject=$SUBJECT concurrency=$CONCURRENCY config=${CONFIG:-config.yaml} python=$PY"

for S in "${SUITES[@]}"; do
  echo "===== SUITE $S start $(date +%H:%M:%S) ====="
  "$PY" -X utf8 run_eval.py run --suite "$S" --subject "$SUBJECT" \
    --concurrency "$CONCURRENCY" "${CONFIG_ARGS[@]}" || echo "SUITE_FAILED $S"
done

"$PY" -X utf8 run_eval.py report --subject "$SUBJECT" "${CONFIG_ARGS[@]}"
echo ALL_DONE

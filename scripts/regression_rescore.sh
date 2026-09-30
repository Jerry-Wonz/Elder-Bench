#!/usr/bin/env bash
# 判分回归门禁：对已有历史报告的 subject 逐个执行离线重判分，逐字段比对。
#
# 用法（本机或服务器，不需要 GPU）：
#   bash scripts/regression_rescore.sh
#
# 原理：
#   rescore 读 eval/results/*.jsonl 中已落盘的 response，用当前 scoring + metrics
#   重算，再与历史报告 JSON 逐字段比对。只要 SCORER_VERSION / RULE_VERSION 未变，
#   结果就必须逐位一致；任何改动若破坏该一致性，必须显式提升版本号并在此处登记。
#
# 未覆盖：v2 ablation 尚无全量结果；历史 v1 deploy/ablation 已在 H8 删除。
#
# 期望基线与容差来历见同目录 regression_baseline.json（期望集与脚本分离，
# 便于逐年登记新基线而不改脚本逻辑）。

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-/c/Python/Python312/python.exe}"
RESULTS="$ROOT/eval/results"
BASELINE_JSON="$ROOT/scripts/regression_baseline.json"

# 期望基线 -> "subject:报告文件名" 列表
# tr -d '\r' 是必要的：$PY 是 Windows 版 python.exe，其 stdout 为 CRLF，
# 不剥离时读到的报告名末尾带 \r，拼出的路径在 Windows 上找不到文件。
if ! OUT="$("$PY" -X utf8 -c "
import json,sys
for s in json.load(open(sys.argv[1], encoding='utf-8'))['subjects']:
    print('%s:%s' % (s['subject'], s['baseline_report']))
" "$BASELINE_JSON" | tr -d '\r')"; then
  echo "REGRESSION_HAS_FAILURE: 无法读取基线 $BASELINE_JSON" >&2
  exit 1
fi
readarray -t BASELINES <<< "$OUT"
if [ "${#BASELINES[@]}" -eq 0 ] || [ -z "${BASELINES[0]}" ]; then
  echo "REGRESSION_HAS_FAILURE: 基线为空，拒绝按全通过退出" >&2
  exit 1
fi
TOL="${TOL:-0.01}"

# 先校验所有基准文件存在，避免跑了大部分 rescore 才发现基线路径写错
for entry in "${BASELINES[@]}"; do
  if [ ! -f "$RESULTS/${entry#*:}" ]; then
    echo "REGRESSION_HAS_FAILURE: 基准报告缺失 $RESULTS/${entry#*:}" >&2
    exit 1
  fi
done

cd "$ROOT/eval"
FAILED=0
for entry in "${BASELINES[@]}"; do
  SUBJECT="${entry%%:*}"
  REPORT="${entry#*:}"
  echo "===== REGRESSION $SUBJECT vs $REPORT ====="
  if "$PY" -X utf8 run_eval.py rescore --subject "$SUBJECT" --check "$RESULTS/$REPORT" --tol "$TOL"; then
    echo "REGRESSION_PASS $SUBJECT"
  else
    echo "REGRESSION_FAIL $SUBJECT"
    FAILED=1
  fi
done

if [ "$FAILED" -eq 0 ]; then
  echo REGRESSION_ALL_PASS
else
  echo REGRESSION_HAS_FAILURE
fi
exit "$FAILED"

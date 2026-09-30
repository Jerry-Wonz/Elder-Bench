"""与 `eval/` harness 的唯一 import 接缝。

设计 §4.6 定的边界：`eval/` 下只有 `reporting.py` 与 `metrics.py` 不依赖第三方库，
因此只有这两个模块可以被界面 import。其余模块（`adapters` 拖进 pandas，
`scoring` 拖进 jieba/nltk，`run_eval` 间接拖进 pandas）一律经子进程调用。

**本模块是全仓库唯一一处向 sys.path 注入 `eval/` 的地方**。集中在一处的原因：
注入会把这些顶层名带进进程的模块命名空间（schema / scoring / metrics / reporting /
adapters / runner / detectors / manifest / model_client），分散注入会让人无法回答
"这个进程里 `metrics` 到底是哪个"。

已知代价：注入位置是 sys.path[0]，因此上述名字会优先于同名的已安装包。
当前依赖集（streamlit / altair / pandas / pyarrow / numpy）不含这些名字，
故无冲突；新增依赖时需复核一次。
"""
from __future__ import annotations

import sys

from paths import EVAL_DIR

if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

# 这两个 import 必须在 sys.path 注入之后，故置于文件中部而非顶部。
import metrics  # noqa: E402
import reporting  # noqa: E402

# 供其他模块使用的常量，避免它们各自去猜。
SUITE_METRIC_LABEL = reporting.BIN_METRIC_LABEL
wilson_ci = reporting.wilson_ci
group_subjects = reporting.group_subjects
render_markdown = reporting.render_markdown
render_compare = reporting.render_compare
bin_of = metrics.bin_of
bin_sort_key = metrics.bin_sort_key

__all__ = [
    "metrics", "reporting",
    "SUITE_METRIC_LABEL", "wilson_ci", "group_subjects",
    "render_markdown", "render_compare", "bin_of", "bin_sort_key",
]

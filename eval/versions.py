"""判分与数据口径的版本常量：**单一来源**。

为什么单独放一个模块，而不是留在 `scoring.py` / `detectors.py` / `adapters.py` 里：

`ui/cli.py` 需要在**不导入 scoring 的前提下**读到口径版本号（`scoring` 拖进 jieba 与
nltk，控制台进程不该为读一个常量引入它们，设计 §4.6 的依赖边界）。此前控制台是用正则
去 `scoring.py` 源码里抠字面量的过渡做法，写法一改就静默失效。本模块只依赖标准库，
因此既可以被判分侧正常导入，也可以被控制台按源码安全读取（`ui/cli.py` 用 AST 解析，
对换行、引号、尾随逗号、注释均不敏感）。

**这里的改动纪律（设计 §6 第 3 条）**：

| 改什么 | 提哪个版本 |
|---|---|
| 判分逻辑 | `SCORER_VERSION` |
| 词表与正则 | `RULE_VERSION` |
| 任务 schema 或迁移逻辑 | `ADAPTER_VERSION` |

三者都会被写入 run manifest，并在 `verify` 时重算比对。`SCORER_VERSIONS` 是可供
`rescore --scorer-version` 重放**全部**口径；新口径经评审前只加入其中，不自动替换默认值。
1.1 已于 2026-09-25 完成评审并转正，1.0 保留历史重放。

`_THINK_STRIP_VERSIONS` / `_PROSE_MODES` 也不在这里：它们描述的是判分实现细节，不是对外
可引用的口径标识。但它们已被控制台的**长度口径**复用（`metrics.per_task_scores` 对旧结果
按 verdict 记录的 scorer_version 重算参与判分的正文字数），因此与 `visible_body_for`
一起放在 `text_utils.py`（同样只依赖标准库），`scoring.py` 对该符号做转发。
"""

from __future__ import annotations

# 任务 schema 与迁移逻辑（adapters.py）
ADAPTER_VERSION = "2.0"

# 判分逻辑（scoring.py）。2026-09-25 起 1.1 为默认口径：
# 读正文的模式先剥离 </think> 之前的推理轨迹，格式解析类模式保持全文。
# 1.0 保留为历史重放版本，仅供 `rescore --scorer-version 1.0` 使用。
SCORER_VERSION = "1.1"
SCORER_VERSIONS = ("1.0", "1.1")

# Markdown/JSON 报告渲染器版本。渲染口径变化时递增，写入报告头与 manifest。
RENDERER_VERSION = "1.0"

# 词表与正则（detectors.py）
RULE_VERSION = "1.1"

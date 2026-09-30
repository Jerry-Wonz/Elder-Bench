"""判分与采集层共用的文本口径工具。

只依赖标准库，避免 scoring 的 jieba/nltk 重依赖进入模型客户端。

推理模型经 OpenAI 兼容接口返回时，可能把内部推理轨迹和用户可见正文写在同一个
`content` 字段中。采集层与离线重判必须使用同一剥离规则，否则响应长度、NLG 指标和
规则命中会再次分叉。

`visible_body_for` 是这一口径的唯一裁决点，被三方共用：判分入口 `scoring.grade()`、
采集层 `model_client`（见其注释）、以及控制台的长度提取 `metrics.per_task_scores()`。
三方必须给出同一条长度，否则报告里的长度分箱与单条可展开字段会对不上。控制台跑在
不含 jieba/nltk 的 `.venv-web` 里（设计 §4.6），因此该函数只能落在本模块这种纯标准库
的位置，不能放在 `scoring.py`。`scoring.py` 对本模块的同名符号做转发，旧导入路径不变。
"""
from __future__ import annotations

import re

# ^.*? 锚定行首且非贪婪，re.S 使 . 跨行；count=1 保证只剥离首段轨迹，
# 正文内再次出现的 </think> 不会被二次截断。
_THINK_CLOSE = re.compile(r"^.*?</think\s*>", re.S)


def strip_reasoning_trace(text: str) -> str:
    """剥离首个 </think> 及其之前的推理轨迹，返回用户可见正文。

    如果剥离后为空，返回空串而不是回退到推理原文。空回复在指标层按模型失败计分，
    这是比把内部推理混入正文更安全的保守口径。
    """
    raw = text or ""
    if "</think>" not in raw:
        return raw
    return _THINK_CLOSE.sub("", raw, count=1).strip()


# 自 1.1 起，读正文的模式在判分前剥离 </think> 之前的推理轨迹。
# 这两个常量描述的是判分口径的实现细节，不是对外可引用的版本标识，故不放 versions.py；
# 但它们被控制台侧的长度口径复用，因此必须留在本模块（纯标准库）而不是 scoring.py。
_THINK_STRIP_VERSIONS = ("1.1",)
# 读正文的模式：判分基于用户实际看到的文字
_PROSE_MODES = ("ref_nlg", "psy_rules", "elder_rules", "regex_refusal")


def visible_body(text: str) -> str:
    """返回用户可见正文：剥离 `</think>` 之前的推理轨迹。"""
    return strip_reasoning_trace(text)


def visible_body_for(text: str, scorer_version: str, eval_mode: str) -> str:
    """按判分口径返回该模式下实际参与判分的正文。

    与 grade() 同源：仅当版本要求剥离且模式为读正文类时才剥离；格式解析类模式
    （tolerance / detect / binary_detect）保持全文，避免破坏 FINAL / JSON 抽取。
    """
    if scorer_version in _THINK_STRIP_VERSIONS and eval_mode in _PROSE_MODES:
        return visible_body(text)
    return text or ""

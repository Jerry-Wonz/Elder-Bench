"""DESIGN.md 的色值，Python 侧的唯一副本。

## 为什么单独一个模块

界面配色本来分散在三处：`ui/theme.py` 的 CSS 字面量、`ui/app.py` 里内联的
图表轴色、`ui/charts.py` 里的柱色与灰底色。三处的同步靠注释提醒，而这次核对
时它们已经是三套颜色（界面紫 `#635bff`，维度图用 Vega 的 `tableau10`，子标签
柱用 `#4c78a8`）。注释提醒是失效的机制，故把值收到一处：CSS 令牌由本模块
插值生成，图表直接 import，同步不再是纪律问题。

## CSS 令牌名与这里的常量对应关系

`ui/theme.py` 的 `_TOKENS` 是这些常量插值出来的，映射是逐字对应的：

| 本模块 | CSS 变量 |
|---|---|
| `BRAND` / `BRAND_HOVER` / `BRAND_RGB` | `--eb-brand` / `--eb-brand-hover` / `--eb-brand-rgb` |
| `INK` / `MUTED` | `--eb-ink` / `--eb-muted` |
| `BG` / `CARD` / `LINE` | `--eb-bg` / `--eb-card` / `--eb-line` |
| `OK` / `OK_BG` | `--eb-ok` / `--eb-ok-bg` |
| `WARN` / `WARN_BG` | `--eb-warn` / `--eb-warn-bg` |
| `BAD` / `BAD_BG` | `--eb-bad` / `--eb-bad-bg` |

## 语义状态色按角色映射，不按 DESIGN.md 的字面标签

`DESIGN.md` 的示例标签是 Paid / Pending / Failed，那是支付域的文案。控制台的
实际状态是门禁 PASS / FAIL / INSUFFICIENT、作业 running / done / failed、体检
OK / WARN / FAIL / SKIP。三色按**角色**（成功 / 待处理 / 失败）映射即可，
不因示例文案而改动控制台自己的状态词。

## `CATEGORICAL` 与 `MISSING` 是扩展，不是 DESIGN.md 的内容

`DESIGN.md` 只定义了语义状态色（离散三档），没有定义分类色板。而两张柱状图
要的是「哪根柱属于哪个模型/哪个卷」这种无好坏含义的分类色，用语义三色会把
「模型 A」读成「模型 A 通过」。故此处加一条两色分类色板，并注明是扩展。
`MISSING` 同理：它是热力图缺失斜线的颜色，界面上没有对应角色。
"""
from __future__ import annotations


# ---------------------------------------------------------------- DESIGN.md 原文
BRAND = "#4F46E5"
BRAND_HOVER = "#4338CA"
INK = "#111827"
MUTED = "#6B7280"
BG = "#F3F4F6"
CARD = "#FFFFFF"
LINE = "#E5E7EB"
OK = "#10B981"
OK_BG = "#D1FAE5"
WARN = "#F59E0B"
WARN_BG = "#FEF3C7"
BAD = "#EF4444"
BAD_BG = "#FEE2E2"

FONT = 'Inter, "PingFang SC", "Microsoft YaHei", sans-serif'

# 品牌色底上的文字（扩展）。与 `CARD` 同值但角色不同：`CARD` 是容器底色，
# 这个是「不可再叠加」的前景色。合成一处在读代码时说不清用途。
ON_BRAND = "#FFFFFF"


# ---------------------------------------------------------------- 派生与扩展
def rgb(hex_color: str) -> str:
    """`#4F46E5` → `79 70 229`，供 `rgb(var(--eb-brand-rgb) / .35)` 这类写法用。

    由 `BRAND` 算出来而不是再抄一份十进制数：抄一次就多一个会漂的值。
    """
    h = hex_color.lstrip("#")
    return f"{int(h[0:2], 16)} {int(h[2:4], 16)} {int(h[4:6], 16)}"


BRAND_RGB = rgb(BRAND)
BRAND_TINT = f"rgba({BRAND_RGB}, 0.1)"


def _over(base: str, top: str, alpha: float) -> str:
    """`top` 以 alpha 压在 `base` 上，返回十六进制结果。

    用于从已有色推「淡一档」的衍生色。手抄一个肉眼相近的值会发生这种事：
    品牌色改了，衍生色没跟着改，而两者看起来「差不多」所以没人发现。
    """
    b, t = base.lstrip("#"), top.lstrip("#")
    out = [round(int(t[i:i + 2], 16) * alpha + int(b[i:i + 2], 16) * (1 - alpha))
           for i in (0, 2, 4)]
    return "#" + "".join(f"{c:02X}" for c in out)


# 表格行悬停底色（派生）：INK 以 2% 压在 CARD 上。DESIGN.md 只给了一档阴影，
# 悬停不另造第二档投影，用这一档极淡的底色代替。
HOVER = _over(CARD, INK, 0.02)


def tone(role: str) -> tuple[str, str]:
    """语义角色 → (前景色, 背景色)。界面与表格共用一处映射。

    `role` 取 `ok` / `warn` / `bad` / `info` / `mute`。控制台的状态字符串
    （门禁 PASS/FAIL/INSUFFICIENT、作业 running/finished/failed/stopped/lost、
    体检 ok/warn/fail/skip）到角色的映射由调用方声明，不写死在这里：
    同一角色在不同域的字面量不同，写死会把两个域的词汇混进一个表。
    """
    return {
        "ok": (OK, OK_BG),
        "warn": (WARN, WARN_BG),
        "bad": (BAD, BAD_BG),
        "info": (BRAND, BRAND_TINT),
        "mute": (MUTED, BG),
    }[role]


# 分类色（扩展）。第一色取品牌色；第二色取天空蓝，与靛蓝在常见色觉障碍下
# 仍可区分。只用于「分组」语义，不表示好坏。
CATEGORICAL = ("#4F46E5", "#0EA5E9")

# 热力图缺失斜线（扩展）。比 `MUTED` 淡一档，避免与格内文字抢注意力。
MISSING = "#9CA3AF"

# 热力图无颜色含义的格子（低 n / 缺失）的底色，取 `LINE` 同值。
GREY = LINE

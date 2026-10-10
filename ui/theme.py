"""控制台样式。取值口径见仓库根目录 `DESIGN.md`。

## 令牌在 `:root`，只有一处

所有颜色、圆角、间距、字号都先落成自定义属性（`--eb-*`），规则里只引用属性。
改配色只改 `:root` 一段，不会散到几十条规则里；也保证 `DESIGN.md` 与样式表
之间只有一个可核对的对应点。

`:root` 里另存一组**十进制分量**（`--eb-brand-rgb: 79 70 229`），因为 `rgba()`
不吃十六进制变量（`rgba(#4F46E5, .1)` 非法），而 `rgb(var(--eb-brand-rgb) / .1)`
合法。分量由 `palette.rgb()` 从 `BRAND` 算出来，不手抄第二份十进制数。

## 侧栏是深色主题，但边界取自同一组角色

`DESIGN.md` 定义了页面背景与卡片背景，没有定义侧栏颜色。侧栏保持深色
（这是 2026-09-23 记录在《评测WebUI设计.md》里的既有形态），但深色值不再
是另一套独立色板：`--eb-sidebar` 取 `--eb-ink`，选中项取品牌色，未选中项取
`--eb-muted`。这样「改了品牌色侧栏没跟着改」这类漂移不会发生。侧栏自用的
三档（正文、悬停底、选中底）仍需写死色值，集中在本文件顶部 `_SIDEBAR_*`，
不散进规则里。

层次方向也与主区相反：主区是「深字浅底」（卡片比页面浅），侧栏是
「浅字深底」（选中项比栏底浅）。

## 写死的色值只剩一处

除了 `_SIDEBAR_*`（DESIGN.md 未定义的侧栏色阶），本文档的色值全部来自
`palette.py`。`selftest.py` 的 `design-tokens` 项按**常量名**豁免这一处，
不按行号——行号会随编辑漂走，豁免范围会悄悄扩大或失效。

## 尺寸

字号一律 px，不随浏览器基准字号缩放，这样命中 `DESIGN.md` 的 32 / 16 / 15 / 13
四档；表格里的 `.eb-tag` 小标签与图表文字也取 `--eb-fs-*`，没有第五档字号。
间距用 8px 为基数的 `--eb-s1..s8`。字体族用 `--eb-font`，与
`.streamlit/config.toml` 的 `font` 必须同步改，否则成对的数字会一个字距。
"""
from __future__ import annotations

import streamlit as st

import palette as pal


# 字号：DESIGN.md 的四档。以**数字**而非 `Npx` 串导出，供图表层
# `mark_text(fontSize=...)` 直接取用（那里要 int，不要 "12px"）。
_FS_HERO = 32   # 核心数据
_FS_TITLE = 16  # 卡片标题 / 表头
_FS_BODY = 15   # 正文 / 表格内容
_FS_AUX = 13    # 辅助信息

# 侧栏深色层次（DESIGN.md 未定义，是本文件的扩展，故不进 palette.py）。
# 栏底 = INK(#111827)，未选中文字/图标 = MUTED(#6B7280) 抬亮一档到 13px 下可读，
# 选中态底 = 品牌色压暗一档，选中态左边条与图标 = 品牌色系。
# 这几个常量必须定义在 `_TOKENS` 之前：`_TOKENS` 是 f-string，在模块导入时
# 就求值，放在后面会直接 `NameError`。
_SIDEBAR_INK = "#E5E7EB"          # 侧栏正文与选中项文字
_SIDEBAR_MUTED = "#9CA3AF"        # 侧栏未选中项文字与图标
_SIDEBAR_HOVER = "#1F2937"        # 悬停底色，INK 抬一档
_SIDEBAR_ACTIVE = "#312E81"       # 选中底色，品牌色压暗一档
_SIDEBAR_ACTIVE_INK = "#C7D2FE"   # 选中项图标，品牌色抬亮一档


# 与 DESIGN.md「品牌色 / 文字色 / 背景与边框 / 语义状态色 / 字体排版」逐条对应。
# 色值的字面量在 palette.py，此处只做插值：CSS 变量名与 Figure 里的图元色因此
# 来自同一个常量，不会出现「界面换了色、图没换」。
_TOKENS = f"""
:root {{
  /* 品牌色 */
  --eb-brand: {pal.BRAND};
  --eb-brand-hover: {pal.BRAND_HOVER};
  /* tint 用 `rgba(r, g, b, a)` 而不是 `rgb(r g b / a)`：品牌色块要压在深色侧栏
     与浅色卡片两种底上，`rgba` 的写法在两种底色下都不出现在非法上下文里。 */
  --eb-brand-tint: rgba({pal.BRAND_RGB}, 0.1);
  --eb-brand-rgb: {pal.BRAND_RGB};
  --eb-on-brand: {pal.ON_BRAND};

  /* 文字色 */
  --eb-ink: {pal.INK};
  --eb-muted: {pal.MUTED};

  /* 背景与边框 */
  --eb-bg: {pal.BG};
  --eb-card: {pal.CARD};
  --eb-line: {pal.LINE};
  --eb-r-card: 12px;
  --eb-r-tag: 6px;
  --eb-r-pill: 999px;
  --eb-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);

  /* 语义状态色（用于标签与趋势）。 */
  --eb-ok: {pal.OK};
  --eb-ok-bg: {pal.OK_BG};
  --eb-warn: {pal.WARN};
  --eb-warn-bg: {pal.WARN_BG};
  --eb-bad: {pal.BAD};
  --eb-bad-bg: {pal.BAD_BG};

  /* 字体排版 */
  --eb-font: {pal.FONT};
  --eb-fs-hero: {_FS_HERO}px;   /* 核心数据，字重 700 */
  --eb-fs-title: {_FS_TITLE}px;  /* 卡片标题 / 表头，字重 600 */
  --eb-fs-body: {_FS_BODY}px;   /* 正文 / 表格内容，字重 400 */
  --eb-fs-aux: {_FS_AUX}px;    /* 辅助信息，字重 400 */

  /* 间距系统：基础单位 8px */
  --eb-s1: 8px;
  --eb-s2: 16px;
  --eb-s3: 24px;
  --eb-s4: 32px;
  --eb-s6: 48px;
  --eb-s8: 64px;

  /* 派生：卡片行悬停底色。DESIGN.md 只给了一档阴影，悬停不再造第二档投影。 */
  --eb-hover: {pal.HOVER};

  /* 派生：侧栏深色层次。栏底取 --eb-ink，选中项取品牌色，未选中项取
     --eb-muted，其余几档由这三支展开（写死的值集中在 `_SIDEBAR_*`）。 */
  --eb-sidebar: var(--eb-ink);
  --eb-sidebar-ink: {_SIDEBAR_INK};
  --eb-sidebar-muted: {_SIDEBAR_MUTED};
  --eb-sidebar-line: rgb(255 255 255 / 0.08);
  --eb-sidebar-hover: {_SIDEBAR_HOVER};
  --eb-sidebar-active: {_SIDEBAR_ACTIVE};
  --eb-sidebar-active-ink: {_SIDEBAR_ACTIVE_INK};
}}
"""


# 供 ui/app.py 内联 HTML、以及图表层取色用。命名的唯一来源是 palette.py，
# 这里做一次转发，使调用方只依赖 `theme` 一个模块。
BRAND = pal.BRAND
BRAND_HOVER = pal.BRAND_HOVER
INK = pal.INK
MUTED = pal.MUTED
BG = pal.BG
CARD = pal.CARD
LINE = pal.LINE
CATEGORICAL = pal.CATEGORICAL
FS_BODY = _FS_BODY
FS_AUX = _FS_AUX


_THEME_CSS = """
<style>
__TOKENS__
html, body, [data-testid="stAppViewContainer"] { background: var(--eb-bg); color: var(--eb-ink); font-family: var(--eb-font); }
[data-testid="stMain"] :is(h1,h2,h3,h4,h5,h6,p,li,label,button,input,textarea,select),
[data-testid="stSidebar"] :is(h1,h2,h3,h4,h5,h6,p,li,label,button,input,textarea,select),
[data-baseweb="select"] { font-family: var(--eb-font); }
/* 上一行只覆盖文字控件，不能写成 `[data-testid="stAppViewContainer"] *`：
   Streamlit 的折叠箭头和导航图标依赖 Material Symbols 连字，全局覆盖后会把
   `keyboard_double_arrow_left` 等图标名直接画成文字并覆盖相邻标题。 */
[data-testid="stIconMaterial"], .material-symbols-rounded, .material-symbols-outlined, .material-icons {
  font-family: "Material Symbols Rounded", "Material Symbols Outlined", "Material Icons" !important;
  font-style: normal; font-weight: normal; line-height: 1; letter-spacing: normal;
  text-transform: none; white-space: nowrap; word-wrap: normal; direction: ltr;
  font-feature-settings: "liga"; -webkit-font-feature-settings: "liga";
  -webkit-font-smoothing: antialiased;
}
[data-testid="stHeader"] { background: var(--eb-bg); }
[data-testid="stMain"] .block-container { max-width: 1480px; padding: var(--eb-s3) var(--eb-s4) var(--eb-s8); }
[data-testid="stMain"] p, [data-testid="stMain"] li { font-size: var(--eb-fs-body); }
[data-testid="stMain"] h1 { color: var(--eb-ink); font-size: var(--eb-s4); font-weight: 700; letter-spacing: -0.02em; }
[data-testid="stMain"] h2 { color: var(--eb-ink); font-size: var(--eb-s3); font-weight: 600; }
[data-testid="stMain"] h3 { color: var(--eb-ink); font-size: var(--eb-fs-title); font-weight: 600; }
[data-testid="stMain"] h4 { color: var(--eb-ink); font-size: var(--eb-fs-body); font-weight: 600; }
[data-testid="stSidebar"] { background: var(--eb-sidebar); border-right: 1px solid var(--eb-sidebar-line); min-width: 220px !important; max-width: 440px !important; resize: horizontal; overflow-x: hidden; overflow-y: auto; }
[data-testid="stSidebar"] [data-testid="stSidebarContent"] { background: var(--eb-sidebar); }
[data-testid="stSidebar"] [data-testid="stSidebarHeader"] { padding: var(--eb-s2) var(--eb-s2) 0; }
[data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] button { color: var(--eb-sidebar-muted); background: transparent; }
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding: var(--eb-s1) var(--eb-s2) var(--eb-s2); }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3,
[data-testid="stSidebar"] p, [data-testid="stSidebar"] span, [data-testid="stSidebar"] label { color: var(--eb-sidebar-ink); }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { margin: 0; }
[data-testid="stSidebar"] [data-testid="stRadio"] > label { display: none; }
[data-testid="stSidebar"] .st-key-nav { width: 100% !important; }
[data-testid="stSidebar"] .st-key-nav > div { width: 100%; }
[data-testid="stSidebar"] [data-testid="stRadio"] { width: 100%; }
[data-testid="stSidebar"] [data-testid="stRadioGroup"], [data-testid="stSidebar"] [data-testid="stRadioGroup"] > div { width: 100%; }
[data-testid="stSidebar"] [data-testid="stRadioGroup"] { gap: var(--eb-s1); }
[data-testid="stSidebar"] [data-testid="stRadioOption"] { display: flex; align-items: center; width: 100%; min-height: 44px; padding: var(--eb-s1) var(--eb-s2); border-radius: var(--eb-r-card); border: 1px solid transparent; color: var(--eb-sidebar-muted); transition: background 140ms ease, border-color 140ms ease, color 140ms ease; }
[data-testid="stSidebar"] [data-testid="stRadioOption"]:hover { background: var(--eb-sidebar-hover); }
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] { background: var(--eb-sidebar-active); border-color: rgb(var(--eb-brand-rgb) / 0.35); box-shadow: inset 3px 0 0 var(--eb-brand); color: var(--eb-on-brand); }
[data-testid="stSidebar"] [data-testid="stRadioOption"]:not([data-selected="true"]) { background: transparent; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div:first-child { display: none; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] { display: flex; align-items: center; gap: var(--eb-s1); width: 100%; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] p { color: var(--eb-sidebar-muted); font-size: var(--eb-fs-body); font-weight: 400; }
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] p { color: var(--eb-on-brand); font-weight: 600; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] [data-testid="stIconMaterial"] { width: 20px; color: var(--eb-sidebar-muted); font-size: var(--eb-fs-title); }
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] [data-testid="stIconMaterial"] { color: var(--eb-sidebar-active-ink); }
.eb-brand { display: flex; align-items: center; gap: var(--eb-s1); padding: var(--eb-s1) 0 var(--eb-s2); }
.eb-brand-mark { display: grid; place-items: center; width: 40px; height: 40px; border-radius: var(--eb-r-card); color: var(--eb-on-brand); font-size: var(--eb-fs-aux); font-weight: 700; background: var(--eb-brand); box-shadow: var(--eb-shadow); }
.eb-brand-name { color: var(--eb-on-brand); font-size: var(--eb-fs-title); font-weight: 600; line-height: 1.2; }
.eb-brand-sub { color: var(--eb-sidebar-muted); font-size: var(--eb-fs-aux); font-weight: 400; margin-top: 2px; letter-spacing: 0.08em; text-transform: uppercase; }
.eb-sidebar-note { margin-top: var(--eb-s3); padding: var(--eb-s2); border: 1px solid var(--eb-sidebar-line); border-radius: var(--eb-r-card); background: transparent; color: var(--eb-sidebar-muted); font-size: var(--eb-fs-aux); font-weight: 400; line-height: 1.6; }
</style>
"""


def inject_theme() -> None:
    css = (_THEME_CSS
           .replace("__TOKENS__", _TOKENS)
           .replace("</style>", _CARD_CSS + _TABLE_CSS + "</style>"))
    st.markdown(css, unsafe_allow_html=True)


# `.eb-t-*` 是表格单元格里的小标签，不是 `DESIGN.md` 的四档正文字号之一：
# 它嵌在 15px 的单元格里，取 13px 会与正文同宽而失层次，故取 11px 并加内边距。
# 除此之外本文档所有字号都走 `--eb-fs-*`。
#
# 页头与品牌块的直接子元素都是**并列**的两个 div。这些是 `st.markdown` 送出的
# 一段裸 HTML，浏览器解析时会在最外层自动补一个容器再凭空闭合——于是"多一个
# 同级子元素"在浏览器里看不出问题，在按标签栈配平的校验里却是一处「多余的
# 闭合标签」。加子元素时要么改成一个容器包住，要么成对补上标签。
_CARD_CSS = """
.eb-page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--eb-s2); margin: 0 0 var(--eb-s3); }
.eb-eyebrow { color: var(--eb-muted); font-size: var(--eb-fs-aux); font-weight: 600; letter-spacing: 0.13em; text-transform: uppercase; }
.eb-page-head h1 { margin: var(--eb-s1) 0 0; color: var(--eb-ink); font-size: var(--eb-fs-hero); font-weight: 700; line-height: 1.1; letter-spacing: -0.02em; }
.eb-page-chip { display: inline-flex; align-items: center; gap: var(--eb-s1); padding: var(--eb-s1) var(--eb-s2); border: 1px solid var(--eb-line); border-radius: var(--eb-r-pill); background: var(--eb-card); color: var(--eb-muted); font-size: var(--eb-fs-aux); font-weight: 400; white-space: nowrap; }
.eb-page-chip::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--eb-ok); box-shadow: 0 0 0 4px var(--eb-ok-bg); }
.eb-summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--eb-s2); margin-bottom: var(--eb-s3); }
/* 卡片自上而下是「标题 16/600 → 核心数据 32/700 → 辅助 13/400」，
   与 DESIGN.md 的四档一一对上。原先的「大图标 + 小标签」把图标摆在与标题
   同级的位置，尺寸上无处安放：图标要盖住 16/600 的标题就会喧宾夺主。改为
   图标内联在标题行内、尺寸随标题，核心数据独占一行取满 32px。 */
.eb-summary-card { min-height: 132px; padding: var(--eb-s2); border: 1px solid var(--eb-line); border-radius: var(--eb-r-card); background: var(--eb-card); box-shadow: var(--eb-shadow); }
.eb-summary-label { display: flex; align-items: center; gap: var(--eb-s1); color: var(--eb-ink); font-size: var(--eb-fs-title); font-weight: 600; }
.eb-summary-icon { display: inline-grid; place-items: center; flex: none; width: 24px; height: 24px; border-radius: var(--eb-r-tag); background: var(--eb-brand-tint); }
.eb-summary-icon svg { width: 16px; height: 16px; stroke: currentColor; }
.eb-summary-icon.violet { color: var(--eb-brand); background: var(--eb-brand-tint); }
.eb-summary-icon.mint { color: var(--eb-ok); background: var(--eb-ok-bg); }
.eb-summary-icon.amber { color: var(--eb-warn); background: var(--eb-warn-bg); }
.eb-summary-icon.rose { color: var(--eb-bad); background: var(--eb-bad-bg); }
.eb-summary-value { margin-top: var(--eb-s1); color: var(--eb-ink); font-size: var(--eb-fs-hero); font-weight: 700; line-height: 1.1; letter-spacing: -0.02em; }
.eb-summary-note { margin-top: var(--eb-s1); color: var(--eb-muted); font-size: var(--eb-fs-aux); font-weight: 400; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
[data-testid="stVerticalBlockBorderWrapper"] { border: 1px solid var(--eb-line) !important; border-radius: var(--eb-r-card) !important; background: var(--eb-card); box-shadow: var(--eb-shadow); padding: var(--eb-s2); }
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlockBorderWrapper"] { box-shadow: none; padding: 0; }
[data-testid="stVerticalBlockBorderWrapper"] h3 { margin-top: 0; margin-bottom: var(--eb-s1); color: var(--eb-ink); font-size: var(--eb-fs-title); font-weight: 600; }
.stButton > button, .stDownloadButton > button { border-radius: var(--eb-r-tag); border-color: var(--eb-line); box-shadow: none; font-size: var(--eb-fs-body); font-weight: 600; }
.stButton > button[kind="primary"] { background: var(--eb-brand); border-color: var(--eb-brand); color: var(--eb-on-brand); }
.stButton > button[kind="primary"]:hover { background: var(--eb-brand-hover); border-color: var(--eb-brand-hover); }
.stButton > button:not([kind="primary"]):hover { border-color: var(--eb-brand); color: var(--eb-brand); background: var(--eb-brand-tint); }
[data-testid="stMetric"] { padding: var(--eb-s2); border: 1px solid var(--eb-line); border-radius: var(--eb-r-card); background: var(--eb-card); box-shadow: var(--eb-shadow); }
[data-testid="stMetricLabel"] { color: var(--eb-muted); font-size: var(--eb-fs-aux); font-weight: 400; }
[data-testid="stMetricValue"] { color: var(--eb-ink); font-size: var(--eb-fs-hero); font-weight: 700; letter-spacing: -0.02em; }
[data-testid="stCaptionContainer"] p { color: var(--eb-muted); font-size: var(--eb-fs-aux); font-weight: 400; line-height: 1.55; }
/* 提示条按语义状态着色。Streamlit 的容器只区分 role="alert"（错误/警告）与
   role="status"（成功/信息），四档区分不了；内层却带 `stAlertContentSuccess`
   这样的 testid，故用 :has() 反选外层。:has() 本文件已有用例（表格横向滚动），
   不构成新的浏览器要求。这是 DESIGN.md 语义三色在界面上的主要落点。 */
[data-testid="stAlertContainer"] { border-radius: var(--eb-r-card); font-size: var(--eb-fs-body); }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) { background: var(--eb-ok-bg); border-left: 3px solid var(--eb-ok); }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) { background: var(--eb-warn-bg); border-left: 3px solid var(--eb-warn); }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) { background: var(--eb-bad-bg); border-left: 3px solid var(--eb-bad); }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]) { background: var(--eb-brand-tint); border-left: 3px solid var(--eb-brand); }
/* 展开面板与进度条：圆角对齐卡片档，去掉 Streamlit 默认的第二档阴影。 */
[data-testid="stExpander"] details { border: 1px solid var(--eb-line); border-radius: var(--eb-r-card); background: var(--eb-card); box-shadow: var(--eb-shadow); }
[data-testid="stExpander"] summary { min-height: 44px; font-size: var(--eb-fs-body); line-height: 1.5; }
[data-testid="stExpander"] summary [data-testid="stIconMaterial"] { flex: none; }
/* 状态小标签。6px 圆角（DESIGN.md 的「小标签」档），四档语义。
   底色与前景色由 `palette.tone()` 在 Python 侧内联给出，不在这里按 class 写死：
   同一个角色既出现在标签里，也出现在别处，两处共用一个映射才不会漂。 */
.eb-tag { display: inline-block; padding: 2px 8px; border-radius: var(--eb-r-tag); font-size: var(--eb-fs-aux); font-weight: 600; line-height: 1.6; white-space: nowrap; }
hr { border-color: var(--eb-line); }
@media (max-width: 1100px) { .eb-summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } [data-testid="stMain"] .block-container { padding-left: var(--eb-s2); padding-right: var(--eb-s2); } }
@media (max-width: 720px) { .eb-page-head { align-items: flex-start; flex-direction: column; } .eb-summary-grid { grid-template-columns: 1fr; } }
"""

_TABLE_CSS = """
[data-testid="stMarkdownContainer"] table { width: 100%; border-collapse: separate; border-spacing: 0; margin: var(--eb-s1) 0 0; border: 1px solid var(--eb-line); border-radius: var(--eb-r-card); overflow: hidden; font-size: var(--eb-fs-body); }
[data-testid="stMarkdownContainer"] thead th { position: sticky; top: 0; z-index: 2; padding: var(--eb-s1) var(--eb-s2); color: var(--eb-ink); background: var(--eb-bg); border-bottom: 1px solid var(--eb-line); text-align: left; font-size: var(--eb-fs-title); font-weight: 600; }
[data-testid="stMarkdownContainer"] tbody td { padding: var(--eb-s1) var(--eb-s2); color: var(--eb-ink); background: var(--eb-card); border-bottom: 1px solid var(--eb-line); vertical-align: top; font-size: var(--eb-fs-body); font-weight: 400; line-height: 1.5; }
[data-testid="stMarkdownContainer"] tbody tr:last-child td { border-bottom: 0; }
[data-testid="stMarkdownContainer"] tbody tr:hover td { background: var(--eb-hover); }
[data-testid="stMarkdownContainer"] :has(table) { overflow-x: auto; }
[data-testid="stDataFrame"] { border: 1px solid var(--eb-line); border-radius: var(--eb-r-card); overflow: hidden; }
"""

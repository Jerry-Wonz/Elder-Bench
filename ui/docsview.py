"""只读地抽取 `docs/` 下的 Markdown 区块，供总览页原样渲染。

## 为什么是解析文档，而不是重算

总览页要展示 `docs/三维度评测模型对比.md` §3 的分维度多口径主表。该表包含
归一化锚点、最优值加粗和模型间并列比较，这些约定不应在控制台里形成第二套重算逻辑。

控制台只做**逐字抽取**：不改写、不重排、不补全、不做数值格式化。数字反映文档
更新时的状态，而不是 `eval/results/` 的最新报告；界面会明确提示这一点。

## 边界

本模块只读。不写文件、不起子进程、不导入 harness 侧模块。返回值是纯字符串，
渲染交给调用方（`app.py`）。
"""
from __future__ import annotations

from typing import Iterable

import paths

# 三张主表的标题，逐字取自文档。改文档标题时这里必须同步，否则界面少一块。
# 选这三块的理由：文档共五张表，§1 是被测对象注册表（无分数）、§4 是运行方案
# 与进度（无分数），含分数的只有 §2 / §2.1 / §3。
MAIN_TABLE_SECTION = "## 3. 主表：分维度多口径对比"

# 保留旧名字，避免历史调用方在导入时直接失败；新的总览页只使用主表区块。
TABLE_SECTIONS: tuple[str, ...] = (MAIN_TABLE_SECTION,)

COMPARE_DOC = paths.REPO_ROOT / "docs" / "三维度评测模型对比.md"


def mtime() -> float:
    """文档的修改时间。调用方拿它当缓存键，文档一改缓存即失效。"""
    return COMPARE_DOC.stat().st_mtime


def heading_text(head: str) -> str:
    """标题行去掉 `#` 前缀，得到可展示的文字。"""
    return head.lstrip("#").strip()


def _trim(body: list[str]) -> str:
    """去掉尾部空行与 `---` 分隔线，避免区块末尾拖一条水平线。"""
    while body and (not body[-1].strip() or body[-1].strip() == "---"):
        body.pop()
    return "\n".join(body).strip("\n")


def _blocks(text: str) -> dict[str, str]:
    """按标题行切块，返回 {标题行原文: 正文}。

    任何一个以 `#` 开头的行都开一个新块，因此 `## 2.` 的正文在遇到 `### 2.1`
    时结束。这与文档的层级一致，也正是这里要的切法：§2 与 §2.1 必须分开取。
    """
    heads: list[str] = []
    bodies: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("#"):
            heads.append(line)
            bodies.append([])
        elif heads:
            bodies[-1].append(line)
    return {h: _trim(b) for h, b in zip(heads, bodies)}


def sections(titles: Iterable[str] = TABLE_SECTIONS) -> list[tuple[str, str]]:
    """按标题原文抽取区块，返回 `[(标题行, 正文)]`，顺序同 `titles`。

    标题必须逐字匹配（含序号、空格与全角括号）。找不到的标题被跳过而不是抛错：
    文档改名时界面应当少一块并给出提示，而不是整页白屏。
    """
    found = _blocks(COMPARE_DOC.read_text(encoding="utf-8"))
    return [(t, found[t]) for t in titles if t in found]


def main_table() -> str:
    """返回 §3 主表的 Markdown 原文，去掉表后的读表说明。"""
    got = sections((MAIN_TABLE_SECTION,))
    if not got:
        return ""
    body = got[0][1]
    lines = body.splitlines()
    table: list[str] = []
    for line in lines:
        if line.lstrip().startswith("|"):
            table.append(line)
        elif table:
            break
    return "\n".join(table).strip()


def main_table_rows() -> list[list[str]]:
    """把主表拆成单元格字符串，供页面做只读筛选与可视化。

    这里不做数值格式化、不补全空维度，也不把 Markdown 的加粗标记解释成
    名次；可视化若要读数值，只把它当作展示投影，数值仍以原表为准。
    """
    md = main_table()
    rows: list[list[str]] = []
    for line in md.splitlines():
        raw = line.strip()
        if not raw.startswith("|") or not raw.endswith("|"):
            continue
        cells = [c.strip() for c in raw.strip("|").split("|")]
        if cells and all(set(c) <= set("-: ") for c in cells):
            continue
        rows.append(cells)
    return rows

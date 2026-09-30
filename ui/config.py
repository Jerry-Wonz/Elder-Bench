"""评测配置的只读解析。设计 §5.1。

默认可见对象 = `config.yaml` 的四个能力/消融 subject + `config.deploy.yaml` 的
一个部署态 subject。`config.history.yaml` 只供 CLI 历史重算，不进入默认 UI。

**只读**：本模块没有任何写入路径，`config.yaml` 被 manifest 记了内容哈希，
界面改写它会使成片历史 run 的 verify 报「已变化」（设计 §1.2）。

## 关于 `env:` 解析的重复实现

`run_eval._resolve_env`（`run_eval.py:76-83`）已实现 `env:VAR` → 环境变量的解析。
本模块重新实现了同一语义，这是对设计 §4.1「不复制 CLI 逻辑」的一次**有意偏离**：

- 不能 import：`run_eval.py` 顶部 `SUITES = list(adapters.SUITE_BUILDERS.keys())`
  会拖进 pandas，界面进程不装 pandas（设计 §4.6）。
- 不能重构出去：设计 §1.4 只允许给 CLI 增加只读子命令，不允许改动既有文件。

处理方式是**把这份重复钉在测试上**而不是钉在注释上：`ui/selftest.py` 用真实的
`config.yaml` 断言本实现与 CLI 的解析结果逐键一致。语义一旦分叉，自测会失败。

影响面被有意收窄：`api_key` 不参与解析结果的使用（界面只判断其是否存在，
不回显值，设计 §7.2），因此这份重复即使分叉也不涉及密钥泄露。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

import yaml

from paths import EVAL_DIR
from harness import group_subjects

CONFIG_PATH = EVAL_DIR / "config.yaml"
CONFIG_DEPLOY = EVAL_DIR / "config.deploy.yaml"
CONFIG_HISTORY = EVAL_DIR / "config.history.yaml"
VISIBLE_CONFIGS = (CONFIG_PATH, CONFIG_DEPLOY)

# 解码参数是「可比性」的判定依据（设计 §5.1），单独列出以免各处硬编码字段名。
DECODING_FIELDS = ("temperature", "max_tokens", "top_p", "seed",
                   "presence_penalty", "frequency_penalty", "stop", "extra_body")

# 敏感字段：只判断存在与否，绝不回显值（设计 §7.2）。
SECRET_FIELDS = ("api_key",)


def _load_dotenv() -> None:
    """把 eval/.env 载入 os.environ（不覆盖已存在的变量）。

    与 `run_eval._load_dotenv` 同样是有意的重复实现，理由同上。
    界面需要它，是因为配置体检要判断 `env:EVAL_SUBJECT_API_KEY` 能否解析成功，
    而该变量通常只存在于 .env 中。
    """
    path = EVAL_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip("'\"")
        if k and k not in os.environ:
            os.environ[k] = v


def resolve_env(node: Any) -> Any:
    """递归解析 "env:VAR" → 环境变量；未设置则为空串。

    与 `run_eval._resolve_env`（`run_eval.py:76-83`）同语义，由 selftest 钉住。
    """
    if isinstance(node, str) and node.startswith("env:"):
        return os.environ.get(node[len("env:"):], "")
    if isinstance(node, dict):
        return {k: resolve_env(v) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_env(v) for v in node]
    return node


def _load_raw_file(path: Path, seen: set[Path] | None = None) -> dict[str, Any]:
    """读取配置并解析 `extends`；子文件的 subjects 整体覆盖父文件。"""
    seen = set(seen or ())
    resolved = path.resolve()
    if resolved in seen:
        raise RuntimeError(f"config extends cycle: {resolved}")
    seen.add(resolved)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    parent = raw.pop("extends", None)
    if not parent:
        return raw
    base = _load_raw_file((path.parent / str(parent)).resolve(), seen)
    base.update(raw)
    return base


def load_raw() -> dict[str, Any]:
    """未解析 env 的可见配置：默认四角色 + 独立部署态。"""
    base = _load_raw_file(CONFIG_PATH)
    subjects = dict(base.get("subjects") or {})
    if CONFIG_DEPLOY.exists():
        deploy = _load_raw_file(CONFIG_DEPLOY)
        subjects.update(deploy.get("subjects") or {})
    base["subjects"] = subjects
    return base


def load() -> dict[str, Any]:
    """解析 env 之后的可见配置。"""
    _load_dotenv()
    return resolve_env(load_raw())


def config_file_for_subject(name: str) -> Optional[str]:
    """返回该 subject 所需的子配置；默认 config.yaml 返回 None。"""
    if CONFIG_DEPLOY.exists():
        deploy = _load_raw_file(CONFIG_DEPLOY)
        if name in (deploy.get("subjects") or {}):
            return CONFIG_DEPLOY.name
    return None


def subject_names(cfg: Optional[dict[str, Any]] = None) -> list[str]:
    """可见活动 subject：默认 config.yaml 四项 + config.deploy.yaml 一项。"""
    return list((cfg or load()).get("subjects") or {})


@dataclass
class SubjectView:
    """配置页主体表的一行。字段选自设计 §5.1 的表格。"""

    name: str
    group: str
    tags: list[str]
    model: str
    base_url: str
    base_url_raw: str
    temperature: Optional[float]
    max_tokens: Optional[int]
    system_prompt_file: Optional[str]
    system_prompt_exists: bool
    api_key_set: bool
    kwargs: dict[str, Any]

    @property
    def decoding(self) -> dict[str, Any]:
        return {k: self.kwargs.get(k) for k in DECODING_FIELDS}

    @property
    def env_broken(self) -> bool:
        """base_url 声明了 env: 但解析为空。体检项之一（设计 §5.2）。"""
        return self.base_url_raw.startswith("env:") and not self.base_url

    @property
    def local_endpoint(self) -> bool:
        return any(h in self.base_url for h in ("127.0.0.1", "localhost", "0.0.0.0"))


def subject_view(name: str, cfg: Optional[dict[str, Any]] = None) -> SubjectView:
    raw = load_raw()
    resolved = cfg or load()
    r = (raw.get("subjects") or {}).get(name, {}) or {}
    s = (resolved.get("subjects") or {}).get(name, {}) or {}
    sp = s.get("system_prompt_file")
    return SubjectView(
        name=name,
        group=s.get("group") or "ungrouped",
        tags=list(s.get("tags") or []),
        model=str(s.get("model") or ""),
        base_url=str(s.get("base_url") or ""),
        base_url_raw=str(r.get("base_url") or ""),
        temperature=s.get("temperature"),
        max_tokens=s.get("max_tokens"),
        system_prompt_file=sp,
        system_prompt_exists=bool(sp) and (EVAL_DIR / sp).exists(),
        # 只算布尔，不取回值（设计 §7.2）。
        api_key_set=bool(s.get("api_key")),
        kwargs={k: s.get(k) for k in DECODING_FIELDS},
    )


def subject_views(cfg: Optional[dict[str, Any]] = None) -> list[SubjectView]:
    cfg = cfg or load()
    return [subject_view(n, cfg) for n in subject_names(cfg)]


def subject_groups(cfg: Optional[dict[str, Any]] = None) -> dict[str, dict[str, Any]]:
    """复用 reporting.group_subjects 的分组与一致性判定，界面不重写（设计 §5.1）。"""
    cfg = cfg or load()
    return group_subjects(cfg, list((cfg.get("subjects") or {}).keys()))


def redline(cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return (cfg or load()).get("redline") or {}


def sampling(cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return (cfg or load()).get("sampling") or {}


def concurrency(cfg: Optional[dict[str, Any]] = None) -> int:
    return int((cfg or load()).get("concurrency", 8))

"""提示词模板化管理与热加载。

使用方式：
  from super_agent.prompts import get_prompt

  prompt = get_prompt("query_rewrite", query="MySQL主从延迟怎么排查")
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)

# 内置提示词缓存（避免频繁文件 IO）
_BUILTIN_PROMPTS: dict[str, str] = {}


def get_prompt(name: str, **kwargs) -> str:
    """加载并渲染提示词模板。

    Args:
        name: 模板文件名（不含 .jinja2 后缀），如 "query_rewrite"
        **kwargs: 模板变量

    Returns:
        渲染后的提示词文本

    Raises:
        ValueError: 模板不存在
    """
    template = _try_load(name)
    return template.render(**kwargs)


def register_prompt(name: str, content: str) -> None:
    """注册内置提示词（覆盖文件模板）。"""
    _BUILTIN_PROMPTS[name] = content


def list_prompts() -> list[str]:
    """列出所有可用提示词名称。"""
    names = set(_BUILTIN_PROMPTS.keys())
    for f in _TEMPLATES_DIR.glob("*.jinja2"):
        names.add(f.stem)
    return sorted(names)


def _try_load(name: str):
    """优先加载内置提示词，其次加载文件模板。"""
    if name in _BUILTIN_PROMPTS:
        from jinja2 import Template
        return Template(_BUILTIN_PROMPTS[name])

    try:
        return _env.get_template(f"{name}.jinja2")
    except TemplateNotFound:
        raise ValueError(
            f"Prompt template '{name}' not found. "
            f"Available: {list_prompts()}"
        )

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def parse_tags_yaml(tags_file: Path) -> dict[str, list[str]]:
    """解析 tags.yaml 文件，返回 {文件路径/glob模式: [标签列表]} 映射。"""
    if not tags_file.exists():
        return {}
    try:
        content = yaml.safe_load(tags_file.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            logger.warning("tags.yaml format invalid, expected mapping, got %s", type(content))
            return {}
        return {str(k): list(v) for k, v in content.items() if isinstance(v, list)}
    except Exception:
        logger.warning("Failed to parse tags.yaml", exc_info=True)
        return {}


def match_file_tags(file_path: str, file_tags: dict[str, list[str]]) -> list[str]:
    """匹配文件路径到标签。精确匹配优先于 glob 匹配。"""
    # 精确匹配
    if file_path in file_tags:
        return file_tags[file_path]

    # glob 匹配（按 key 顺序，返回第一个匹配）
    filename = Path(file_path).name
    for pattern, tags in file_tags.items():
        if "*" in pattern or "?" in pattern:
            if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(file_path, pattern):
                return tags

    return []

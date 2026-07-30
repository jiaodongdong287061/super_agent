"""
RuleLoader — YAML 规则文件加载器，支持热加载。

=== 功能 ===
- 启动时从 YAML 文件加载规则
- 每次访问时检查文件修改时间，自动重新加载
- 修改 YAML 文件即生效，无需重启服务

=== 用法 ===
    loader = RuleLoader("data/rules/classifier.yaml")
    keywords = loader.get("qa_keywords", [])

=== 热加载原理 ===
每次 get() 时比较文件 mtime，有变化则重读。
开销极小（一次 stat 调用 ≈ 微秒级），适合低频规则读取。
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# 项目根目录（YAML 路径基于项目根）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class RuleLoader:
    """
    YAML 规则加载器，带热加载能力。

    Attributes:
        path: YAML 文件路径（相对于项目根，或绝对路径）
    """

    def __init__(self, path: str):
        """
        Args:
            path: YAML 文件路径。支持：
                  - 相对路径（相对于项目根目录）
                  - 绝对路径
        """
        self._path = Path(path)
        if not self._path.is_absolute():
            self._path = _PROJECT_ROOT / self._path

        self._data: dict[str, Any] = {}
        self._mtime: float = 0
        self._load()

        logger.info("RuleLoader loaded: %s (%d rules)", self._path.name, len(self._data))

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取规则，如果文件有变更则自动重载。

        Args:
            key: 规则键名
            default: 键不存在时的默认值

        Returns:
            规则值（list / dict / str 等）
        """
        self._reload_if_changed()
        return self._data.get(key, default)

    def get_all(self) -> dict[str, Any]:
        """获取全部规则。"""
        self._reload_if_changed()
        return dict(self._data)

    def reload(self) -> bool:
        """强制重新加载，返回是否成功。"""
        return self._load()

    # ── 内部方法 ──

    def _reload_if_changed(self) -> None:
        """检查文件 mtime，有变化则重载。"""
        try:
            current_mtime = os.path.getmtime(self._path)
            if current_mtime > self._mtime:
                logger.info("Rule file changed, reloading: %s", self._path.name)
                self._load()
        except OSError:
            pass  # 文件暂时不可读，维持旧数据

    def _load(self) -> bool:
        """加载 YAML 文件。"""
        try:
            if not self._path.exists():
                logger.warning("Rule file not found: %s", self._path)
                return False

            with open(self._path, encoding="utf-8") as f:
                self._data = yaml.safe_load(f) or {}

            self._mtime = os.path.getmtime(self._path)
            return True
        except Exception as e:
            logger.error("Failed to load rule file %s: %s", self._path, e)
            return False

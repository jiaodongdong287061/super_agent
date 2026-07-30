"""
Guardrails — Agent 系统的第一道安全门。

=== 设计原则 ===
1. Fail-close：不确定时宁拦不错放
2. 分层检测：规则级（毫秒）→ LLM 级（秒级）
3. 三级结果：allow（放行）/ warn（放行但标记）/ block（拦截）

=== 规则来源 ===
关键词规则从 YAML 文件加载（data/rules/guardrails.yaml），
修改 YAML 后自动生效，无需重启服务。

=== 检测流程 ===
输入阶段（三层）：
  1. 注入检测 — prompt 注入攻击（规则）
  2. 敏感信息检测 — 密码、AK/SK、身份证（规则）
  3. 领域限制 + 权限检查（规则 → LLM 兜底）

输出阶段：
  敏感信息过滤 — IP、密码泄露（规则掩码）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from super_agent.core.rule_loader import RuleLoader

logger = logging.getLogger(__name__)

GuardrailVerdict = Literal["allow", "warn", "block"]

# YAML 规则加载器（模块级单例，热加载）
_rules = RuleLoader("data/rules/guardrails.yaml")


@dataclass
class GuardrailsResult:
    """Guardrails 检测结果。

    Attributes:
        verdict: 三级裁定结果（allow / warn / block）
        reason: 裁定原因的文字描述
        matched_rule: 命中的规则名
        details: 每项检测的详细记录（用于审计和调试）
    """
    verdict: GuardrailVerdict = "allow"
    reason: str = ""
    matched_rule: str = ""
    details: list[dict] = field(default_factory=list)


class Guardrails:
    """
    安全护栏 — 输入/输出安全检测。
    所有关键词规则来自 YAML 文件，支持热加载。
    """

    def __init__(self, llm=None):
        """
        Args:
            llm: LLM 客户端（预留，用于 LLM 级语义检测）
        """
        self._llm = llm

    # ── 属性：从 YAML 动态加载，支持热更新 ──

    @property
    def injection_patterns(self) -> list[str]:
        return _rules.get("injection_patterns", [])

    @property
    def domain_blacklist(self) -> list[str]:
        return _rules.get("domain_blacklist", [])

    @property
    def domain_whitelist(self) -> list[str]:
        return _rules.get("domain_whitelist", [])

    @property
    def output_sensitive_patterns(self) -> list[tuple[str, str]]:
        raw = _rules.get("output_sensitive_patterns", [])
        return [(item["pattern"], item["mask"]) for item in raw]

    # ═══════════════════════════════════════════════
    # 输入检测
    # ═══════════════════════════════════════════════

    def check_input(
        self,
        query: str,
        permissions: list[str] | None = None,
        intent_hint: str | None = None,
        user_info: dict | None = None,
    ) -> GuardrailsResult:
        """
        输入检测：注入 → 敏感信息 → 领域限制 + 权限检查。

        Args:
            query: 用户输入的文本
            permissions: 用户权限列表
            intent_hint: 预判的意图（预留）
            user_info: 用户上下文（用于判断是否为内部已认证用户）

        Returns:
            GuardrailsResult: 检测结果
        """
        details: list[dict] = []

        # 判断是否为内部已认证用户（有 department 或 user_id 视为内部员工）
        # 内部用户放宽领域限制，仅做 warn 审计；匿名用户保持严格拦截
        is_internal = bool((user_info or {}).get("department") or (user_info or {}).get("user_id"))

        # ── 第 1 关：注入检测 ──
        for pattern_str in self.injection_patterns:
            pattern = re.compile(pattern_str)
            m = pattern.search(query)
            if m:
                matched = m.group()[:40]
                result = GuardrailsResult(
                    verdict="block",
                    reason="输入包含不安全指令，已拦截",
                    matched_rule=f"injection:{matched}",
                    details=[{"check": "injection", "verdict": "block", "matched": matched}],
                )
                logger.warning("Guardrails BLOCK injection: %s", matched)
                return result
        details.append({"check": "injection", "verdict": "allow"})

        # ── 第 2 关：敏感信息检测 ──
        sensitive_re = re.compile(r"(?i)(password|secret|token|ak|sk)\s*[=:]\s*\S+")
        m = sensitive_re.search(query)
        if m:
            result = GuardrailsResult(
                verdict="block",
                reason="输入包含敏感信息，已拦截",
                matched_rule=f"sensitive:{m.group()[:40]}",
                details=[{"check": "sensitive_info", "verdict": "block", "matched": m.group()[:60]}],
            )
            logger.warning("Guardrails BLOCK sensitive info: %s", m.group()[:40])
            return result
        details.append({"check": "sensitive_info", "verdict": "allow"})

        # ── 第 3 关：领域限制 + 权限检查 ──
        q = query.lower()
        perms = permissions or []
        is_anonymous = not bool((user_info or {}).get("department") or (user_info or {}).get("user_id"))
        is_super = "*:*:*" in perms

        # ── 匿名用户：全部拦截 ──
        # 注入和敏感信息已在上两关检查通过，但领域层面不允许匿名访问
        if is_anonymous:
            reason = "未登录用户无法使用该服务"
            details.append({"check": "domain", "verdict": "block", "reason": reason})
            logger.info("Guardrails BLOCK anonymous request: %s", query[:60])
            return GuardrailsResult(verdict="block", reason=reason, matched_rule="domain:anonymous", details=details)

        # ── 超管用户：黑白名单全部不拦截，直接放行 ──
        if is_super:
            details.append({"check": "domain", "verdict": "allow", "reason": "超管用户，跳过领域限制"})
            return GuardrailsResult(verdict="allow", details=details)

        # ── 普通用户（有部门/用户ID，非超管） ──
        # 只走黑名单：命中则拦截，未命中全部放行
        for kw in self.domain_blacklist:
            if kw in q:
                result = GuardrailsResult(
                    verdict="block",
                    reason="该问题不在企业服务范围内",
                    matched_rule=f"domain_blacklist:{kw}",
                    details=details + [{"check": "domain_blacklist", "verdict": "block", "matched": kw}],
                )
                logger.info("Guardrails BLOCK domain (blacklist): %s", kw)
                return result
        details.append({"check": "domain_blacklist", "verdict": "allow"})

        # 黑名单未命中 → 直接放行（白名单是用于早期快速通过的优化，不是硬门槛）
        return GuardrailsResult(verdict="allow", details=details)

    # ═══════════════════════════════════════════════
    # 输出检测
    # ═══════════════════════════════════════════════

    def check_output(self, text: str) -> GuardrailsResult:
        """
        输出检测：敏感信息掩码。

        模式来自 YAML 文件的 output_sensitive_patterns。
        """
        sanitized = text
        masked_count = 0
        for pattern_str, mask in self.output_sensitive_patterns:
            pattern = re.compile(pattern_str)
            sanitized, count = pattern.subn(mask, sanitized)
            masked_count += count

        if masked_count > 0:
            logger.info("Guardrails output filter: masked %d sensitive items", masked_count)

        return GuardrailsResult(
            verdict="allow",
            details=[{"check": "output_sanitize", "verdict": "allow",
                       "masked_count": masked_count, "sanitized_text": sanitized}],
        )

    # ═══════════════════════════════════════════════
    # LLM 层（预留）
    # ═══════════════════════════════════════════════

    def _llm_domain_check(self, query: str, user_allowed: bool) -> GuardrailsResult | None:
        """LLM 语义级别领域判定（预留实现）。"""
        return None

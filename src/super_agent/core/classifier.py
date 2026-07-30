"""
Classifier — Agent 的意图分类器。

=== 分层策略 ===
第一层：规则匹配（毫秒级）— 关键词精准命中，修改 YAML 即生效
第二层：Embedding 语义匹配（毫秒级）— 用示例问句的向量相似度判断意图
第三层：LLM 兜底（秒级）— 前两层都拿不准时调用 LLM
第四层：缓存 — 相同 query 5 分钟内直接命中缓存

=== 三维度输出 ===
- intent: qa（闲聊）/ knowledge（知识问答）/ action（工具操作）
- risk: low（只读）/ medium（低影响写）/ high（高影响写）
- complexity: simple（单步）/ multi_step（多步编排）

=== 三层互补关系 ===
RuleClassifier：负责有明确标志词的 — "你好"→qa、"重启"→action
EmbeddingClassifier：负责语义模糊但跟示例像的 — "今天什么节日"→qa、"slurm作业排队"→knowledge
LLM：负责复杂/模棱两可的 — "查工单并发给群聊" 既有查询又有操作
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from super_agent.config import settings
from super_agent.core.rule_loader import RuleLoader

logger = logging.getLogger(__name__)

# YAML 规则加载器（模块级单例，热加载）
_rules = RuleLoader("data/rules/classifier.yaml")


@dataclass
class ClassificationResult:
    """分类器输出结果。"""
    intent: Literal["qa", "knowledge", "action"]
    risk: Literal["low", "medium", "high"] = "low"
    complexity: Literal["simple", "multi_step"] = "simple"
    confidence: float = 1.0
    source: Literal["rule", "embedding", "llm", "cache"] = "rule"


class RuleClassifier:
    """规则版 Classifier：纯关键词匹配，规则从 YAML 加载。

    三层匹配：qa → action → knowledge（默认兜底）。
    输出完整三维度：intent + risk + complexity。
    """

    def classify(self, query: str) -> ClassificationResult:
        q = query.lower().strip()

        # 1. QA 命中
        if any(kw in q for kw in _rules.get("qa_keywords", [])):
            return ClassificationResult(
                intent="qa", risk="low", complexity="simple",
                confidence=0.95, source="rule",
            )

        # 2. Action 多步排查
        if any(kw in q for kw in _rules.get("action_multi_keywords", [])):
            risk = self._assess_risk(q)
            return ClassificationResult(
                intent="action", risk=risk, complexity="multi_step",
                confidence=0.9, source="rule",
            )

        # 3. Action 单步操作
        if any(kw in q for kw in _rules.get("action_simple_keywords", [])):
            risk = self._assess_risk(q)
            return ClassificationResult(
                intent="action", risk=risk, complexity="simple",
                confidence=0.9, source="rule",
            )

        # 4. Knowledge 命中
        if any(kw in q for kw in _rules.get("knowledge_keywords", [])):
            return ClassificationResult(
                intent="knowledge", risk="low", complexity="simple",
                confidence=0.8, source="rule",
            )

        # 5. 未匹配 → 低置信度，交给下一层
        return ClassificationResult(
            intent="qa", risk="low", complexity="simple",
            confidence=0.5, source="rule",
        )

    def _assess_risk(self, q: str) -> Literal["low", "medium", "high"]:
        if any(kw in q for kw in _rules.get("risk_high_keywords", [])):
            return "high"
        if any(kw in q for kw in _rules.get("risk_medium_keywords", [])):
            return "medium"
        return "low"


class EmbeddingClassifier:
    """Embedding 版 Classifier：用向量相似度匹配意图。

    原理：
      每个 intent 在 YAML 中配置若干示例问句（qa_examples / knowledge_examples / action_examples），
      启动时预计算这些例子的 embedding。运行时将用户 query 也转成 embedding，
      与各 intents 的示例比余弦相似度，取均值最高的 intent。

    适用场景：关键词覆盖不到、但语义上跟某类示例明显相似的问题。
    """

    def __init__(self):
        self._embedder = None
        self._example_embeddings: dict[str, list[list[float]]] = {}
        # 命中阈值：余弦相似度超过此值才视为匹配
        self._threshold = 0.65

    def _lazy_init(self):
        """懒初始化：首次 classify 时才创建 embedder 并预计算示例向量。"""
        if self._embedder is not None:
            return
        from super_agent.knowledge.embedders import get_embedder
        self._embedder = get_embedder()

        for intent in ["qa", "knowledge", "action"]:
            examples = _rules.get(f"{intent}_examples", [])
            if examples:
                try:
                    self._example_embeddings[intent] = self._embedder.embed_texts(examples)
                    logger.info("EmbeddingClassifier: %d %s examples loaded", len(examples), intent)
                except Exception as e:
                    logger.warning("EmbeddingClassifier failed to embed %s examples: %s", intent, e)

    def classify(self, query: str) -> ClassificationResult:
        """用 query embedding 与各 intents 的示例比相似度，返回最匹配的意图。"""
        self._lazy_init()
        if not self._example_embeddings:
            return ClassificationResult(
                intent="qa", risk="low", complexity="simple",
                confidence=0.0, source="embedding",
            )

        query_emb = self._embedder.embed_query(query)

        best_intent: Literal["qa", "knowledge", "action"] = "qa"
        best_score = 0.0
        scores_detail = []

        for intent, embeddings in self._example_embeddings.items():
            if not embeddings:
                continue
            scores = [self._cosine_similarity(query_emb, emb) for emb in embeddings]
            avg_score = sum(scores) / len(scores)
            max_score = max(scores)
            # 用最大值而非均值（避免某类样本多时均值被拉低）
            effective_score = max(avg_score, max_score)
            scores_detail.append(f"{intent}={effective_score:.3f}")
            if effective_score > best_score:
                best_score = effective_score
                best_intent = intent  # type: ignore

        logger.info("EmbeddingClassifier scores: %s", ", ".join(scores_detail))

        if best_score >= self._threshold:
            q = query.lower()
            return ClassificationResult(
                intent=best_intent,
                risk=self._assess_risk(q, best_intent),
                complexity=self._assess_complexity(q, best_intent),
                confidence=round(best_score, 3),
                source="embedding",
            )

        # 相似度不足，低置信度返回（交给 LLM 兜底）
        return ClassificationResult(
            intent=best_intent, risk="low", complexity="simple",
            confidence=round(best_score, 3), source="embedding",
        )

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _assess_risk(self, q: str, intent: str) -> Literal["low", "medium", "high"]:
        if intent != "action":
            return "low"
        if any(kw in q for kw in _rules.get("risk_high_keywords", [])):
            return "high"
        if any(kw in q for kw in _rules.get("risk_medium_keywords", [])):
            return "medium"
        return "low"

    def _assess_complexity(self, q: str, intent: str) -> Literal["simple", "multi_step"]:
        if intent != "action":
            return "simple"
        if any(kw in q for kw in _rules.get("action_multi_keywords", [])):
            return "multi_step"
        return "simple"


# ── 缓存 ──
_CLASSIFICATION_CACHE: dict[str, tuple[float, ClassificationResult]] = {}
_CACHE_TTL = 300  # 5 分钟


class HybridClassifier:
    """三层 Classifier：规则（毫秒）→ Embedding（毫秒）→ LLM（秒级）+ 缓存。"""

    def __init__(self, llm_client=None):
        self._rule = RuleClassifier()
        self._embedding = EmbeddingClassifier()
        self._llm = llm_client
        self._model = settings.llm.default_model
        self._api_base = settings.llm.oneapi_base_url.rstrip("/")
        self._api_key = settings.llm.oneapi_api_key

    async def classify(self, query: str) -> ClassificationResult:
        q = query.lower().strip()

        # 缓存命中
        now = time.time()
        if q in _CLASSIFICATION_CACHE:
            cached_at, result = _CLASSIFICATION_CACHE[q]
            if now - cached_at < _CACHE_TTL:
                logger.debug("Classifier cache hit for: %s", q[:40])
                return ClassificationResult(
                    intent=result.intent, risk=result.risk,
                    complexity=result.complexity,
                    confidence=result.confidence, source="cache",
                )

        # ── 第一层：规则匹配（毫秒） ──
        # 关键词精准命中：
        #   qa: "你好"、"今天" → 0.95
        #   action: "重启"、"排查" → 0.9
        #   knowledge: "什么是"、"怎么用" → 0.8
        # 低于 0.8 说明无关键词匹配，继续下一层
        rule_result = self._rule.classify(query)
        logger.info("RuleClassifier: query=%s intent=%s confidence=%s", query[:60], rule_result.intent, rule_result.confidence)

        if rule_result.confidence >= 0.8:
            _CLASSIFICATION_CACHE[q] = (now, rule_result)
            return rule_result

        # ── 第二层：Embedding 语义匹配（毫秒） ──
        # 用示例问句的 embedding 做余弦相似度，能覆盖关键词匹配不到
        # 但语义相似的 query（如 "slurm作业排队" → knowledge）
        embedding_result = self._embedding.classify(query)

        if embedding_result.confidence >= 0.6:
            _CLASSIFICATION_CACHE[q] = (now, embedding_result)
            return embedding_result

        # ── 第三层：LLM 兜底（秒级） ──
        # 规则和 embedding 都拿不准时，让 LLM 做精细判断
        llm_result = await self._llm_classify(query)
        _CLASSIFICATION_CACHE[q] = (now, llm_result)
        return llm_result

    async def _llm_classify(self, query: str) -> ClassificationResult:
        """调用 LLM 进行分类判定。"""
        prompt = f"""请判断以下用户问题的类别，只返回 JSON。

用户问题: {query}

类别定义：
- qa: 问候、闲聊、Agent 自身能力咨询。不检索知识库，不调工具。
  示例："你好"、"今天天气"、"你能干什么"
- knowledge: 一切涉及企业知识的问题。先检索知识库，无结果则 LLM 用自己的知识回答。
  示例："什么是主从复制"、"公司年假政策"、"预算怎么审批"
- action: 明确的操作指令，需要调外部系统工具。
  示例 simple: "重启服务器"、"查工单状态"、"发通知"
  示例 multi_step: "排查数据库慢并修复"、"检查网络并优化"

风险等级：
- low: 只读操作（查询、检索、分析）
- medium: 低影响写操作（发送通知、创建工单）
- high: 高影响写操作（重启、删除、修改配置）

复杂度：
- simple: 单步完成
- multi_step: 需要多步编排

输出格式: {{"intent": "knowledge", "risk": "low", "complexity": "simple"}}"""

        try:
            import httpx
            async with httpx.AsyncClient(
                base_url=self._api_base,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=15,
                trust_env=False,
            ) as client:
                resp = await client.post("/chat/completions", json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": "你是一个分类器，只返回 JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 128,
                })
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                if "{" in content:
                    content = content[content.index("{"):content.rindex("}") + 1]
                result = json.loads(content)
                logger.info(
                    "LLM classifier: query=%s intent=%s risk=%s complexity=%s",
                    query[:60], result.get("intent"), result.get("risk"), result.get("complexity"),
                )
                return ClassificationResult(
                    intent=result.get("intent", "knowledge"),
                    risk=result.get("risk", "low"),
                    complexity=result.get("complexity", "simple"),
                    confidence=0.7,
                    source="llm",
                )
        except Exception as e:
            logger.warning("LLM classifier failed, fallback to embedding: %s", e)
            return self._embedding.classify(query)


# 兼容旧导入
ClassifierResult = ClassificationResult

"""Reranker 重排序器。

默认关闭，通过环境变量 SA_RERANK_PROVIDER=remote 启用。
启用后调用远程 Reranker API（如 FlagEmbedding 服务）对检索结果进行精排。

配置项（.env）：
    SA_RERANK_PROVIDER=disabled | remote    # 默认 disabled
    SA_RERANK_API_URL=http://.../v1/rerank   # 远程 API 地址
    SA_RERANK_API_KEY=                       # 可选 API 密钥
    SA_RERANK_TOP_N=0                        # 0 = 使用 top_k
"""

from __future__ import annotations

from super_agent.knowledge.remote_reranker import RemoteReranker

# 对外暴露 RemoteReranker，保持接口一致
# 旧版 BGEReranker（本地 FlagEmbedding）已移除，统一走远程 API
__all__ = ["RemoteReranker"]

"""Qdrant 集合维度迁移脚本：删除旧集合（2048 维）→ 自动重建（1024 维）。

用法：
  cd super_agent
  uv run python scripts/migrate_qdrant_dim.py

执行后：
  1. 列出所有现有 Qdrant 集合
  2. 删除并重建（使用 .env 中 SA_VECTOR_QDRANT_VECTOR_SIZE=1024）
  3. 清理本地 index_state 文件
  4. 提示执行 /rag/index 重新索引
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# 将项目 src 加入 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from super_agent.config import settings
from super_agent.knowledge.stores.qdrant_store import QdrantStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    qdrant_url = settings.vector_store.qdrant_url
    qdrant_api_key = settings.vector_store.qdrant_api_key
    new_size = settings.vector_store.qdrant_vector_size

    logger.info("Qdrant 地址: %s", qdrant_url)
    logger.info("目标维度: %d", new_size)

    # 连接 Qdrant 并列举所有集合
    from qdrant_client import QdrantClient

    client_kwargs: dict = {"url": qdrant_url, "prefer_grpc": False}
    if qdrant_api_key:
        client_kwargs["api_key"] = qdrant_api_key

    client = QdrantClient(**client_kwargs)
    collections = [c.name for c in client.get_collections().collections]

    if not collections:
        logger.info("没有发现 Qdrant 集合，无需迁移")
        return

    logger.info("发现以下 Qdrant 集合：")
    for col in collections:
        info = client.get_collection(col)
        logger.info("  - %s (维度: %d, 文档数: %d)", col, info.config.params.vectors.size, info.points_count or 0)

    # 确认
    confirm = input("\n将删除以上所有集合并重建（维度 %d），是否继续？(yes/no): " % new_size)
    if confirm.lower() not in ("yes", "y"):
        logger.info("已取消")
        return

    # 删除并重建每个集合
    for col in collections:
        logger.info("删除集合: %s", col)
        client.delete_collection(collection_name=col)
        logger.info("已删除: %s", col)

    logger.info("所有集合已删除，准备重建...")

    for col in collections:
        store = QdrantStore(tenant_id=col.removeprefix("super_agent_docs_") if col != "super_agent_docs" else "")
        logger.info("已重建集合: %s (维度: %d)", store.collection_name, new_size)

    # 清理本地 index_state
    index_state_dir = Path("data/index_state")
    if index_state_dir.exists():
        for f in index_state_dir.glob("index_state*.json"):
            f.unlink()
            logger.info("已删除状态文件: %s", f)

    logger.info("")
    logger.info("=" * 60)
    logger.info("迁移完成！请执行索引重建：")
    logger.info("  公共文档: curl -X POST 'http://localhost:8000/rag/index?doc_dir=data/raw_docs&force=true'")
    logger.info("  部门文档: curl -X POST 'http://localhost:8000/rag/index?doc_dir=data/raw_docs&force=true&department=103'")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

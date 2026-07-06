from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base

from super_agent.config import settings

logger = logging.getLogger(__name__)

Base = declarative_base()


class AuditLogRecord(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False)
    query_text = Column(Text, nullable=False)
    num_chunks = Column(Integer, default=0)
    chunk_ids = Column(Text, default="[]")
    answer_text = Column(Text, default="")
    num_citations = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    status = Column(String(32), default="success")
    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_user_id", "user_id"),
        Index("idx_created", "created_at"),
        Index("idx_status", "status"),
    )


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.mysql.dsn,
            echo=settings.mysql.echo_sql,
            pool_size=2,
            max_overflow=2,
        )
    return _engine


class AuditLogger:
    def __init__(self) -> None:
        self.enabled = settings.rag.enable_audit

    async def log_query(
        self,
        user_id: str,
        query: str,
        num_chunks: int,
        chunk_ids: list[str],
        answer: str,
        num_citations: int,
        latency_ms: float,
        status: str = "success",
    ) -> None:
        if not self.enabled:
            return

        asyncio.create_task(
            self._insert_audit_log(
                user_id=user_id,
                query=query,
                num_chunks=num_chunks,
                chunk_ids=chunk_ids,
                answer=answer,
                num_citations=num_citations,
                latency_ms=latency_ms,
                status=status,
            )
        )

    async def _insert_audit_log(
        self,
        user_id: str,
        query: str,
        num_chunks: int,
        chunk_ids: list[str],
        answer: str,
        num_citations: int,
        latency_ms: float,
        status: str,
    ) -> None:
        try:
            engine = _get_engine()
            async with AsyncSession(engine) as session:
                record = AuditLogRecord(
                    user_id=user_id[:64],
                    query_text=query[:2000],
                    num_chunks=num_chunks,
                    chunk_ids=json.dumps(chunk_ids, ensure_ascii=False),
                    answer_text=answer[:5000],
                    num_citations=num_citations,
                    latency_ms=int(latency_ms),
                    status=status,
                )
                session.add(record)
                await session.commit()
        except Exception as e:
            logger.warning("Audit log write failed (non-blocking): %s", e)

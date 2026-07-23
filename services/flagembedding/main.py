"""
FlagEmbedding 模型推理服务

提供 OpenAI 兼容的 Embedding API + Reranker API，支持 CPU/GPU 模式。
作为一个独立服务部署，供 super_agent 的 APIEmbedder 和 RemoteReranker 调用。
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── 配置（优先级：环境变量 > 默认值） ──────────────────────────

DEVICE: str = os.getenv("FE_DEVICE", "cpu")  # "cpu" or "cuda" or "cuda:0"
EMBED_MODEL: str = os.getenv("FE_EMBED_MODEL", "BAAI/bge-m3")
RERANK_MODEL: str = os.getenv("FE_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
EMBED_DIM: int = int(os.getenv("FE_EMBED_DIM", "2048"))  # 0 = 模型原生维度（BGE-M3 原生 1024）
# BGE-M3 支持 truncate_dim，可设为 1024/2048/4096
# BGE-large-zh-v1.5 固定 1024，设此值无效
HOST: str = os.getenv("FE_HOST", "0.0.0.0")
PORT: int = int(os.getenv("FE_PORT", "8001"))
LOG_LEVEL: str = os.getenv("FE_LOG_LEVEL", "INFO")

# ── 全局模型实例 ──────────────────────────────────────────────

_embed_model = None
_rerank_model = None
_is_m3_model = False
_model_name = EMBED_MODEL  # 用于日志输出


def _load_models():
    global _embed_model, _rerank_model, _is_m3_model
    from FlagEmbedding import BGEM3FlagModel, FlagModel, FlagReranker

    device = DEVICE

    # ── 加载 Embedding 模型 ──
    logger.info("Loading embedding model: %s (device=%s)", EMBED_MODEL, device)
    t0 = time.time()

    if "m3" in EMBED_MODEL.lower():
        kwargs: dict = {"model_name_or_path": EMBED_MODEL, "device": device, "use_fp16": device != "cpu"}
        if EMBED_DIM > 0:
            kwargs["truncate_dim"] = EMBED_DIM
        _embed_model = BGEM3FlagModel(**kwargs)
        _is_m3_model = True
        logger.info(
            "  → BGEM3FlagModel loaded in %.1fs, dim=%s",
            time.time() - t0,
            EMBED_DIM if EMBED_DIM > 0 else "model_default",
        )
    else:
        _embed_model = FlagModel(
            EMBED_MODEL,
            device=device,
            use_fp16=device != "cpu",
        )
        _is_m3_model = False
        logger.info("  → FlagModel loaded in %.1fs, dim=%d", time.time() - t0, _embed_model.model.config.hidden_size)

    # ── 加载 Reranker 模型 ──
    logger.info("Loading reranker model: %s (device=%s)", RERANK_MODEL, device)
    t0 = time.time()
    _rerank_model = FlagReranker(RERANK_MODEL, device=device, use_fp16=device != "cpu")
    logger.info("  → FlagReranker loaded in %.1fs", time.time() - t0)


def _unload_models():
    global _embed_model, _rerank_model
    _embed_model = None
    _rerank_model = None
    torch.cuda.empty_cache()
    logger.info("Models unloaded, GPU cache cleared")


# ── FastAPI 生命周期 ──────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting FlagEmbedding service (device=%s)", DEVICE)
    logger.info("  Embedding model: %s", EMBED_MODEL)
    logger.info("  Reranker model:  %s", RERANK_MODEL)
    logger.info("  Embedding dim:   %s", str(EMBED_DIM) if EMBED_DIM > 0 else "model_default")
    try:
        _load_models()
    except Exception as e:
        logger.error("Failed to load models: %s", e)
        raise
    yield
    _unload_models()


app = FastAPI(title="FlagEmbedding Service", version="1.0.0", lifespan=lifespan)


# ── 请求/响应模型 ──────────────────────────────────────────────


class EmbeddingRequest(BaseModel):
    """OpenAI 兼容的 Embedding 请求格式。"""

    model: str = EMBED_MODEL
    input: str | list[str]
    encoding_format: str = "float"  # 仅支持 "float"


class EmbeddingResponse(BaseModel):
    """OpenAI 兼容的 Embedding 响应格式。"""

    object: str = "list"
    data: list[dict]
    model: str
    usage: dict


class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_n: int = 0  # 0 = 返回所有
    return_documents: bool = False


class RerankItem(BaseModel):
    index: int
    score: float
    text: str | None = None


class RerankResponse(BaseModel):
    object: str = "list"
    results: list[RerankItem]
    usage: dict


class HealthResponse(BaseModel):
    status: str
    device: str
    embed_model: str
    rerank_model: str
    embed_dim: int | str


# ── 中间件：请求日志 ──────────────────────────────────────────


@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.time()
    response = await call_next(request)
    dt = (time.time() - t0) * 1000
    logger.info("%s %s → %d (%.1fms)", request.method, request.url.path, response.status_code, dt)
    return response


# ── 路由 ──────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health():
    if _embed_model is None or _rerank_model is None:
        raise HTTPException(status_code=503, detail="Models not loaded")
    dim = (
        EMBED_DIM
        if EMBED_DIM > 0
        else getattr(_embed_model.model.config, "hidden_size", "unknown")
        if hasattr(_embed_model, "model")
        else "unknown"
    )
    return HealthResponse(
        status="ok",
        device=DEVICE,
        embed_model=EMBED_MODEL,
        rerank_model=RERANK_MODEL,
        embed_dim=dim,
    )


@app.post("/v1/embeddings", response_model=EmbeddingResponse)
async def embed(req: EmbeddingRequest):
    """OpenAI 兼容的 Embedding 接口。

    POST /v1/embeddings
    {
        "model": "BAAI/bge-large-zh-v1.5",
        "input": "Hello world" 或 ["text1", "text2"]
    }

    返回格式与 OpenAI /v1/embeddings 一致，可直接替换 APIEmbedder 的远程地址。
    """
    if _embed_model is None:
        raise HTTPException(status_code=503, detail="Embedding model not loaded")

    texts = [req.input] if isinstance(req.input, str) else req.input
    if not texts:
        raise HTTPException(status_code=422, detail="input must not be empty")

    # BGE 模型建议对 query 和 document 加不同的 instruction prefix
    # 由于该接口同时被 embed_query 和 embed_texts 调用，不加 prefix，由调用方自行处理
    t0 = time.time()

    if _is_m3_model:
        embeddings = _embed_model.encode(texts)["dense_vecs"]
    else:
        embeddings = _embed_model.encode(texts)

    dt = (time.time() - t0) * 1000

    data = [
        {"object": "embedding", "index": i, "embedding": emb.tolist()}
        for i, emb in enumerate(embeddings)
    ]

    total_tokens = sum(len(t) for t in texts)
    return EmbeddingResponse(
        data=data,
        model=req.model,
        usage={"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    )


@app.post("/v1/rerank", response_model=RerankResponse)
async def rerank(req: RerankRequest):
    """Reranker 接口。

    POST /v1/rerank
    {
        "query": "MySQL主从延迟怎么排查",
        "documents": ["文档A...", "文档B...", ...],
        "top_n": 5,
        "return_documents": false
    }

    返回按相关性分数降序排列的结果。
    """
    if _rerank_model is None:
        raise HTTPException(status_code=503, detail="Reranker model not loaded")

    if not req.query.strip():
        raise HTTPException(status_code=422, detail="query must not be empty")
    if not req.documents:
        raise HTTPException(status_code=422, detail="documents must not be empty")

    t0 = time.time()
    pairs = [[req.query, doc] for doc in req.documents]
    scores = _rerank_model.compute_score(pairs)
    dt = (time.time() - t0) * 1000

    # 统一 scores 为 list[float]
    if isinstance(scores, float):
        scores = [scores]

    # 按 score 降序排列
    scored = [
        {"index": i, "score": float(s), "text": req.documents[i] if req.return_documents else None}
        for i, s in enumerate(scores)
    ]
    scored.sort(key=lambda x: x["score"], reverse=True)

    top_n = req.top_n if req.top_n > 0 else len(scored)
    results = scored[:top_n]

    logger.info(
        "Reranked %d docs in %.1fms, top score=%.4f",
        len(req.documents), dt, results[0]["score"] if results else 0,
    )

    return RerankResponse(
        results=[RerankItem(**r) for r in results],
        usage={"total_documents": len(req.documents), "inference_ms": int(dt)},
    )


# ── 启动 ──────────────────────────────────────────────────────


def main():
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, log_level=LOG_LEVEL.lower())


if __name__ == "__main__":
    main()

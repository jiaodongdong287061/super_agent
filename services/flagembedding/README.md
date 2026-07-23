# FlagEmbedding 模型推理服务

独立部署的 FastAPI 服务，提供 OpenAI 兼容的 Embedding API 和 Reranker API。

默认使用 **BAAI/bge-m3**（Embedding，2048 维）和 **BAAI/bge-reranker-v2-m3**（Reranker），
CPU / GPU 均可运行。

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FE_DEVICE` | `cpu` | 运行设备：`cpu` / `cuda` / `cuda:0` |
| `FE_EMBED_MODEL` | `BAAI/bge-m3` | Embedding 模型名 |
| `FE_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Reranker 模型名 |
| `FE_EMBED_DIM` | `2048` | Embedding 维度（BGE-M3 支持 1024/2048/4096） |
| `FE_HOST` | `0.0.0.0` | 监听地址 |
| `FE_PORT` | `8001` | 监听端口 |
| `FE_LOG_LEVEL` | `INFO` | 日志级别 |

### BGE-M3 维度说明

BGE-M3 支持 `truncate_dim` 参数，可直接输出 1024/2048/4096 维向量，
无需加载多个模型。如果你的 Qdrant 集合是 2048 维（`SA_VECTOR_QDRANT_VECTOR_SIZE=2048`），
保持 `FE_EMBED_DIM=2048`，**无需重建索引**。

### CPU 资源

BGE-M3（FP16 原生）在 CPU 上单条推理约 **50-200ms**，对于 RAG 索引场景完全够用。
如需极致 CPU 性能，可改用 GGUF 量化版（`BAAI/bge-m3` 社区有 Q8_0 量化版本，
推理约 12-50ms，内存占用 ~500MB）。

## API

### 健康检查

```bash
GET /health
```

```json
{
  "status": "ok",
  "device": "cpu",
  "embed_model": "BAAI/bge-m3",
  "rerank_model": "BAAI/bge-reranker-v2-m3",
  "embed_dim": 2048
}
```

### Embedding（OpenAI 兼容）

```bash
POST /v1/embeddings
Content-Type: application/json

{
  "model": "BAAI/bge-m3",
  "input": "你好世界"
}
```

返回格式与 OpenAI `/v1/embeddings` 一致，可直接替换 `APIEmbedder` 的远程地址。

### Reranker

```bash
POST /v1/rerank
Content-Type: application/json

{
  "query": "MySQL主从延迟怎么排查",
  "documents": ["文档A...", "文档B...", "文档C..."],
  "top_n": 5
}
```

返回按相关性分数降序排列的结果。

## 启动方式

### 本地启动（CPU）

```bash
pip install -r requirements.txt
python main.py
```

### 本地启动（GPU）

```bash
FE_DEVICE=cuda python main.py
```

### Docker 启动（CPU）

```bash
docker build -t flagembedding-service .
docker run -d \
  --name super-agent-flagembedding \
  -p 31241:8001 \
  --memory=64g \
  --cpus=16 \
  -e FE_DEVICE=cpu \
  -e FE_EMBED_MODEL=BAAI/bge-m3 \
  -e FE_RERANK_MODEL=BAAI/bge-reranker-v2-m3 \
  -e FE_EMBED_DIM=2048 \
  -v flagembedding-cache:/root/.cache/huggingface \
  flagembedding-service
```

### Docker 启动（GPU）

```bash
docker build -t flagembedding-service .
docker run -d \
  --name super-agent-flagembedding \
  --gpus all \
  -p 31241:8001 \
  --memory=64g \
  --cpus=16 \
  -e FE_DEVICE=cuda \
  -e FE_EMBED_MODEL=BAAI/bge-m3 \
  -e FE_RERANK_MODEL=BAAI/bge-reranker-v2-m3 \
  -e FE_EMBED_DIM=2048 \
  -v flagembedding-cache:/root/.cache/huggingface \
  flagembedding-service
```

### Docker Compose（与 super_agent 一起部署）

```yaml
services:
  flagembedding:
    build: ./services/flagembedding
    ports:
      - "31241:8001"
    deploy:
      resources:
        limits:
          memory: 64g
          cpus: "16"
    environment:
      - FE_DEVICE=cpu
      - FE_EMBED_MODEL=BAAI/bge-m3
      - FE_RERANK_MODEL=BAAI/bge-reranker-v2-m3
      - FE_EMBED_DIM=2048
    volumes:
      - flagembedding-cache:/root/.cache/huggingface

volumes:
  flagembedding-cache:
```

## 接入 super_agent

部署后，修改 super_agent 的环境变量指向该服务：

```bash
# Embedding 改成本地服务
SA_EMBEDDING_API_URL=http://flagembedding:8001/v1/embeddings
SA_EMBEDDING_API_KEY=           # 无需 key

# 启用 Reranker（配套 RemoteReranker）
SA_RERANK_PROVIDER=remote
SA_RERANK_API_URL=http://flagembedding:8001/v1/rerank
```

> 注意：BGE 模型对 query 和 document 使用不同的 instruction prefix。
> 当前 API 不自动添加 prefix，由调用方处理。具体参考
> [BGE 官方文档](https://github.com/FlagOpen/FlagEmbedding)。
>
使用国内镜像
pip install huggingface-hub

export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1


hf download BAAI/bge-m3 \
  --local-dir ./models/bge-m3

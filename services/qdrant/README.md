# Qdrant 向量数据库

用于 super_agent 的向量存储，支持 cosine 相似度检索。

## 启动

```bash
cd services/qdrant
docker compose up -d
```

## 验证

```bash
# 健康检查
curl http://localhost:31243/health

# 查看集合列表
curl http://localhost:31243/collections
```

## 配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `QDRANT_API_KEY` | 无 | API 密钥，不设则无鉴权 |

## 接入 super_agent

修改 `.env.dev`：

```bash
SA_VECTOR_PROVIDER=qdrant
SA_VECTOR_QDRANT_URL=http://localhost:31243
SA_VECTOR_QDRANT_API_KEY=your-api-key
SA_VECTOR_QDRANT_COLLECTION=super_agent_docs
SA_VECTOR_QDRANT_VECTOR_SIZE=2048
SA_VECTOR_QDRANT_DISTANCE=COSINE
```

## 端口说明

| 宿主机 | 容器内 | 用途 |
|--------|--------|------|
| 31243 | 6333 | HTTP API，super_agent 连这个 |
| 31244 | 6334 | gRPC（可选）|

## 资源

- 内存：建议 1-2G
- 磁盘：万级文档约几百 MB，按需增长

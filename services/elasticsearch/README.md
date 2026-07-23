# Elasticsearch（含 ik 中文分词）

用于 super_agent 的 BM25 关键词检索，与向量检索形成双路召回 → RRF 融合。

## 启动方式

### 方式一：Docker Compose（推荐）

```bash
cd services/elasticsearch
docker compose up -d
```

### 方式二：Docker Run

```bash
docker run -d \
  --name super-agent-es \
  -p 9200:9200 \
  -p 9300:9300 \
  -e discovery.type=single-node \
  -e xpack.security.enabled=false \
  -e ES_JAVA_OPTS="-Xms4g -Xmx4g" \
  -e cluster.name=super-agent \
  -v es-data:/usr/share/elasticsearch/data \
  --memory=6g \
  docker.elastic.co/elasticsearch/elasticsearch:8.12.0
```

然后进容器装 ik 插件：

```bash
docker exec super-agent-es elasticsearch-plugin install --batch \
  https://github.com/infinilabs/analysis-ik/releases/download/v8.11.0/elasticsearch-analysis-ik-8.11.0.zip
docker restart super-agent-es
```

## 验证

```bash
# 等待 30 秒启动，然后
curl http://localhost:9200
```

返回：

```json
{
  "cluster_name" : "super-agent",
  "status" : "green",
  ...
}
```

验证 ik 分词：

```bash
curl -X POST http://localhost:9200/_analyze \
  -H "Content-Type: application/json" \
  -d '{"analyzer": "ik_smart", "text": "MySQL主从延迟怎么排查"}'
```

应该返回分词结果，而不是报错。

## 接入 super_agent

部署后修改 `.env.dev`：

```bash
# 启用 BM25 混合检索
SA_RAG_ENABLE_BM25_HYBRID=true

# ES 连接地址（如果 ES 在 Docker 中，super_agent 在宿主机用 localhost）
SA_ES_HOSTS=http://localhost:9200
SA_ES_INDEX_NAME=super_agent_docs
```

如果 super_agent 也在 Docker 中，用服务名：

```bash
SA_ES_HOSTS=http://elasticsearch:9200
```

## 资源说明

| 配置 | 值 | 说明 |
|------|-----|------|
| 内存 | 4G-6G | ES JVM 堆 4G，容器上限 6G |
| CPU | 2 核起 | 开发环境够用 |
| 磁盘 | 取决于文档量 | 万级文档约几百 MB |
| 端口 | 9200（HTTP）/ 9300（Transport） | |

## Linux 宿主额外配置

```bash
# ES 需要调整虚拟内存映射数
sudo sysctl -w vm.max_map_count=262144
# 写入 /etc/sysctl.conf 持久化
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
```
（Windows 和 macOS Docker Desktop 不需要此步骤）

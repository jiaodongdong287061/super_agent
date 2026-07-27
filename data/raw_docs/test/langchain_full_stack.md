# LangChain 全家桶（生态总览）

## 一、LangChain 核心

LangChain 是 LLM 应用编排框架，提供：

- Prompt 管理
- Chain / LCEL
- Agent
- Tool calling
- Memory
- RAG pipeline

---

## 二、RAG / 数据层

组件包括：

- Document Loader（PDF / Web / DB）
- Text Splitter
- Retriever   检索器
- VectorStore 接口

常见向量库：

- Chroma
- FAISS
- Pinecone
- Weaviate
- Qdrant

---

## 三、LCEL（LangChain Expression Language）

核心理念：

prompt → model → parser → output

特点：
- 声明式
- 可组合
- 生产推荐

---

## 四、Agents（智能体）

能力：

- 自动选择工具
- 多步推理
- API 调用编排

---

## 五、Tools（工具系统）

支持：

- HTTP API
- DB 查询
- Python function
- 搜索引擎
- Retriever

---

## 六、Memory（记忆）

类型：

- Buffer Memory
- Summary Memory
- Vector Memory

---

## 七、LangServe（部署）

用于：

- Chain/Agent API 化
- FastAPI 集成
- 自动 OpenAPI

---

## 八、LangSmith（观测平台）

功能：

- Trace 调试
- Prompt 分析
- Eval
- A/B test

---

## 九、LangGraph（高级编排）

特点：

- Graph 状态机
- 支持循环/分支
- 适合复杂 agent

---

## 十、整体架构

LangChain 生态 =

Core + RAG + Agent + Infra + Observability

- LangChain（核心）
- LCEL（编程模型）
- LangGraph（状态机）
- LangServe（部署）
- LangSmith（观测）

---

## 一句话总结

LangChain 全家桶 = LLM 应用从开发 → RAG → Agent → 部署 → 监控的一整套基础设施

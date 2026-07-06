# PPT Loader & 自定义标签 设计文档

## 概述

为 RAG 系统新增 PPT 文件加载能力，支持 `.pptx` 和 `.ppt` 格式，提取幻灯片中的文本、表格、演讲者备注和图片 OCR 内容。

## 架构

### PPTLoader

- **基类**：`BaseLoader`
- **文件**：`src/super_agent/knowledge/loaders/ppt.py`
- **扩展名**：`[".pptx", ".ppt"]`

### 分块策略

- 每张幻灯片产出 1 个 Document，Loader 层不做重叠
- 跨 Document 的上下文衔接由 Chunker 层处理（后续增强 SemanticChunker）
- 单页内长文本的重叠由现有 SemanticChunker 滑动窗口机制覆盖

### .pptx 加载流程

```
python-pptx 打开文件 → 遍历幻灯片 → 每张幻灯片提取内容 → 生成 Document
```

### .ppt 加载流程

```
LibreOffice --headless --convert-to pptx → 生成临时 .pptx 文件 → 按 .pptx 流程加载 → 清理临时文件
```

转换参数：
- 超时：60 秒
- 失败时抛出明确错误（提示安装 LibreOffice）

## 内容提取

每张幻灯片按以下顺序提取内容，合并为 Document 的 `page_content`：

| 内容类型 | 提取方式 | 文本格式 |
|---------|---------|---------|
| 文本（标题/正文/文本框） | `slide.shapes` 中 `has_text_frame` 的 shape | 直接文本 |
| 表格 | `shape.has_table`，逐行逐列读取 | Markdown 表格 |
| 演讲者备注 | `slide.notes_slide.notes_text_frame.text` | `[备注]` 前缀分段 |
| 图片 OCR | 提取图片 → 保存临时文件 → PaddleOCR | `[OCR]` 前缀分段 |

图片 OCR 复用 PDFLoader 中已有的 PaddleOCR 检测和调用模式，在 PPTLoader 内部独立实现。

## Metadata

每张幻灯片 Document 的 metadata：

```python
{
    "source": str,           # 文件路径
    "slide_number": int,     # 从 1 开始
    "total_slides": int,     # 总幻灯片数
    "has_notes": bool,       # 是否有演讲者备注
    "has_tables": bool,      # 是否有表格
    "has_images": bool,      # 是否有图片
    "ocr_used": bool,        # 是否使用了 OCR
}
```

`topic_tags` 由 Chunker 层通过 `build_metadata()` 统一生成，不在 Loader 层处理。

## 依赖

`pyproject.toml` 新增：
- `python-pptx>=0.6.21` — 核心依赖

`.ppt` 转换需要系统安装 LibreOffice（Docker 镜像中预装）。

## 注册

`src/super_agent/knowledge/loaders/__init__.py` 中：

```python
from super_agent.knowledge.loaders.ppt import PPTLoader
_register(PPTLoader)
```

## 不在本次范围

- SemanticChunker 跨 Document 边界处理（后续单独任务）
- PDFLoader 重叠改造（后续单独任务）
- OCR 逻辑提取为公共模块

---

## 自定义标签（topic_tags）增强设计

### 现状问题

当前 `topic_tags` 的生成链路：

```
Indexer.build()
  → loader.load(file_path)           # 无 tags 传入
  → chunker.chunk(documents)         # 无 tags 传入
    → _make_chunk()
      → build_metadata(file_path=source)   # manual_tags=None
        → resolve_topic_tags(path, None)    # 只能从路径目录自动继承
```

问题：
1. `manual_tags` 参数存在但整条链路未传入，用户无法自定义标签
2. `resolve_topic_tags` 中 `manual_tags` 和路径继承是互斥的（有 manual_tags 就不走路径继承），而非互补
3. ExcelLoader 在 Document metadata 中硬编码 `"topic_tags": []`，与 Chunker 层的 `build_metadata` 重复

### 设计目标

1. 支持外部传入自定义标签
2. 自动从路径继承标签
3. 两者互补合并：自定义标签 + 路径继承标签 = 最终 topic_tags
4. 对所有文件类型统一生效

### 改动方案

#### 1. metadata.py — 合并逻辑

```python
def resolve_topic_tags(
    file_path: str,
    manual_tags: list[str] | None = None,
) -> list[str]:
    # 路径自动继承
    parts = Path(file_path).parts
    inherited = []
    for part in parts[1:-1]:
        if part and part not in inherited:
            inherited.append(part)

    # 合并：自定义标签 + 路径继承标签，去重保序
    if manual_tags:
        seen = set(manual_tags)
        for tag in inherited:
            if tag not in seen:
                manual_tags.append(tag)
                seen.add(tag)
        return manual_tags

    return inherited
```

#### 2. Indexer — 接受 manual_tags 映射

```python
class Indexer:
    def build(
        self,
        doc_dir: str,
        file_tags: dict[str, list[str]] | None = None,  # 新增：文件路径 → 自定义标签映射
        **kwargs,
    ) -> None:
        ...
        for fp in doc_path.rglob("*"):
            ...
            loader = get_loader(fp.suffix.lower())
            documents = loader.load(str(fp))

            # 将自定义标签写入每个 Document 的 metadata
            tags = file_tags.get(str(fp), []) if file_tags else []
            for doc in documents:
                doc.metadata["manual_tags"] = tags

            chunks = self.chunker.chunk(documents, **kwargs)
            ...
```

#### 3. SemanticChunker — 传递 manual_tags

```python
def _make_chunk(self, content, heading_chain, source, doc_meta):
    full_text = f"{heading_chain}\n{content}" if heading_chain else content
    manual_tags = doc_meta.get("manual_tags")        # 从 Document metadata 读取
    meta = build_metadata(file_path=source, manual_tags=manual_tags)  # 传入
    meta.update({k: v for k, v in doc_meta.items() if k not in ("source", "manual_tags")})
    ...
```

#### 4. ExcelLoader — 移除硬编码

删除 ExcelLoader 中 `"topic_tags": []`，topic_tags 统一由 Chunker 层生成。

#### 5. tags.yaml 配置文件（可选）

在文档目录下支持 `tags.yaml` 声明每个文件的自定义标签：

```yaml
# 放在 doc_dir 下的 tags.yaml
"网络/防火墙规则.docx": ["网络", "安全", "防火墙"]
"运维/巡检手册.pdf": ["运维", "巡检"]
"*.pptx": ["演示文档"]       # 支持 glob 模式
```

Indexer 在 `build()` 时自动检测同目录下的 `tags.yaml`，解析后合并到 `file_tags` 中。

### 数据流

```
tags.yaml (可选)  +  调用方 file_tags 参数
         ↓
    Indexer.build(file_tags=...)
         ↓
    Document.metadata["manual_tags"] = [...]
         ↓
    SemanticChunker._make_chunk()
         ↓
    build_metadata(manual_tags=doc_meta["manual_tags"])
         ↓
    resolve_topic_tags(path, manual_tags)
         ↓
    合并: manual_tags + 路径继承 → topic_tags
```

### API 层不暴露 file_tags 参数

`/rag/index` 接口不提供 `file_tags` 参数，原因：

1. **tags.yaml 已覆盖声明式场景** — 标签是文档的静态元数据，应跟文档放在一起，改标签改文件即可，不需要每次调 API 时传一遍。
2. **REST API 传 file_tags 不合理** — 需要传 `{文件路径: [标签]}` 映射，批量索引时文件可能几十上百个，塞在请求体里既丑又易出错。
3. **file_tags 是 SDK 层逃生舱口** — 它是给程序化调用 `Indexer.build()` 的开发者用的（比如从 CMDB/工单系统自动拉标签），不适合暴露为 HTTP 接口参数。

如果将来有动态打标签的需求，更好的做法是加独立的标签管理接口（如 `PUT /rag/tags`），而不是把映射塞进 index 接口。

### 影响范围

| 文件 | 改动 |
|------|------|
| `metadata.py` | `resolve_topic_tags` 改为合并逻辑 |
| `indexer.py` | `build()` 新增 `file_tags` 参数，写入 Document metadata |
| `chunkers/semantic.py` | `_make_chunk` 从 doc_meta 读取 `manual_tags` 传入 `build_metadata` |
| `loaders/excel.py` | 删除 `"topic_tags": []` 硬编码 |
| 新增 `tags.py` | 解析 `tags.yaml` 配置文件的工具函数 |

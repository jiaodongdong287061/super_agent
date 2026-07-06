# PPT Loader & 自定义标签增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 RAG 系统新增 PPT 文件加载能力（.pptx/.ppt），并实现 topic_tags 的合并逻辑（自定义标签 + 路径继承互补），统一全链路标签传递。

**Architecture:** PPTLoader 继承 BaseLoader，.pptx 用 python-pptx 直接解析，.ppt 用 LibreOffice 转换后走 pptx 流程。每张幻灯片产出 1 个 Document，OCR 复用 PDFLoader 中的 PaddleOCR 模式。topic_tags 改造沿 Indexer → SemanticChunker → build_metadata 链路传递 manual_tags，resolve_topic_tags 改为合并逻辑。

**Tech Stack:** python-pptx, PaddleOCR (可选), LibreOffice (系统依赖, .ppt 格式), pyyaml (tags.yaml 解析)

---

## File Structure

| 文件 | 职责 | 操作 |
|------|------|------|
| `src/super_agent/knowledge/loaders/ppt.py` | PPTLoader 实现 | 新建 |
| `src/super_agent/knowledge/loaders/__init__.py` | 注册 PPTLoader | 修改 |
| `src/super_agent/knowledge/metadata.py` | resolve_topic_tags 合并逻辑 | 修改 |
| `src/super_agent/knowledge/indexer.py` | build() 新增 file_tags 参数 | 修改 |
| `src/super_agent/knowledge/chunkers/semantic.py` | _make_chunk 传递 manual_tags | 修改 |
| `src/super_agent/knowledge/loaders/excel.py` | 移除 `"topic_tags": []` 硬编码 | 修改 |
| `src/super_agent/knowledge/tags.py` | 解析 tags.yaml 工具函数 | 新建 |
| `pyproject.toml` | 新增 python-pptx 依赖 | 修改 |
| `tests/unit/test_loaders.py` | PPTLoader 单元测试 | 修改 |
| `tests/unit/test_metadata.py` | resolve_topic_tags 合并逻辑测试 | 修改 |
| `tests/unit/test_indexer.py` | Indexer file_tags 测试 | 修改 |
| `tests/unit/test_tags.py` | tags.yaml 解析测试 | 新建 |

---

### Task 1: resolve_topic_tags 合并逻辑

**Files:**
- Modify: `src/super_agent/knowledge/metadata.py:10-19`
- Test: `tests/unit/test_metadata.py`

- [ ] **Step 1: 写失败测试 — 合并逻辑 + 互补**

在 `tests/unit/test_metadata.py` 末尾追加：

```python
def test_resolve_topic_tags_merge_manual_and_inherited():
    """manual_tags 和路径继承应互补合并，去重保序"""
    tags = resolve_topic_tags(
        file_path="raw_docs/SRE/mysql/runbook.md",
        manual_tags=["mysql", "backup"],
    )
    assert tags == ["mysql", "backup", "SRE"]


def test_resolve_topic_tags_no_manual():
    """无 manual_tags 时纯路径继承"""
    tags = resolve_topic_tags(file_path="raw_docs/SRE/mysql/runbook.md")
    assert tags == ["SRE", "mysql"]


def test_resolve_topic_tags_dedup():
    """manual_tags 中已包含路径标签时不重复"""
    tags = resolve_topic_tags(
        file_path="raw_docs/SRE/mysql/runbook.md",
        manual_tags=["SRE", "mysql"],
    )
    assert tags == ["SRE", "mysql"]


def test_resolve_topic_tags_empty_path():
    """路径无中间目录时只返回 manual_tags"""
    tags = resolve_topic_tags(
        file_path="runbook.md",
        manual_tags=["运维"],
    )
    assert tags == ["运维"]


def test_resolve_topic_tags_none_manual():
    """manual_tags=None 时纯路径继承"""
    tags = resolve_topic_tags(
        file_path="raw_docs/SRE/mysql/runbook.md",
        manual_tags=None,
    )
    assert tags == ["SRE", "mysql"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_metadata.py::test_resolve_topic_tags_merge_manual_and_inherited tests/unit/test_metadata.py::test_resolve_topic_tags_dedup tests/unit/test_metadata.py::test_resolve_topic_tags_empty_path tests/unit/test_metadata.py::test_resolve_topic_tags_no_manual tests/unit/test_metadata.py::test_resolve_topic_tags_none_manual -v`
Expected: FAIL — 当前 `resolve_topic_tags` 有 manual_tags 时直接返回，不合并路径继承

- [ ] **Step 3: 修改 resolve_topic_tags 为合并逻辑**

将 `src/super_agent/knowledge/metadata.py` 中的 `resolve_topic_tags` 替换为：

```python
def resolve_topic_tags(
    file_path: str,
    manual_tags: list[str] | None = None,
) -> list[str]:
    parts = Path(file_path).parts
    inherited = []
    for part in parts[1:-1]:
        if part and part not in inherited:
            inherited.append(part)

    if manual_tags:
        result = list(manual_tags)
        seen = set(result)
        for tag in inherited:
            if tag not in seen:
                result.append(tag)
                seen.add(tag)
        return result

    return inherited
```

- [ ] **Step 4: 更新已有测试 — test_manual_tags_take_priority 和 test_build_metadata_overrides**

修改 `tests/unit/test_metadata.py` 中两个旧测试以匹配新行为：

```python
def test_manual_tags_take_priority():
    tags = resolve_topic_tags(
        file_path="raw_docs/SRE/mysql/runbook.md",
        manual_tags=["mysql", "backup"],
    )
    # 合并：manual_tags + 路径继承（去重）
    assert tags == ["mysql", "backup", "SRE"]
```

```python
def test_build_metadata_overrides():
    m = build_metadata(
        file_path="raw_docs/runbook.md",
        doc_type="api_doc",
        department="DBA",
        manual_tags=["mysql"],
    )
    assert m["doc_type"] == "api_doc"
    assert m["department"] == "DBA"
    # manual_tags=["mysql"] + 路径继承=["raw_docs"] → ["mysql", "raw_docs"]
    assert m["topic_tags"] == ["mysql", "raw_docs"]
```

- [ ] **Step 5: 运行全部 metadata 测试确认通过**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_metadata.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/super_agent/knowledge/metadata.py tests/unit/test_metadata.py
git commit -m "feat: resolve_topic_tags merges manual_tags with path inheritance"
```

---

### Task 2: SemanticChunker 传递 manual_tags

**Files:**
- Modify: `src/super_agent/knowledge/chunkers/semantic.py:141-158`
- Modify: `tests/unit/test_chunkers.py`

- [ ] **Step 1: 写失败测试 — manual_tags 传递到 Chunk metadata**

在 `tests/unit/test_chunkers.py` 追加（如已有 TestSemanticChunker 类则在其内追加方法，否则新建类）：

```python
def test_chunk_carries_manual_tags():
    from langchain_core.documents import Document
    from super_agent.knowledge.chunkers.semantic import SemanticChunker

    chunker = SemanticChunker()
    doc = Document(
        page_content="这是一段测试文本内容，用于验证 manual_tags 的传递。",
        metadata={"source": "raw_docs/test/doc.md", "manual_tags": ["自定义标签"]},
    )
    chunks = chunker.chunk([doc])
    assert len(chunks) > 0
    assert "自定义标签" in chunks[0].metadata["topic_tags"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_chunkers.py::test_chunk_carries_manual_tags -v`
Expected: FAIL — 当前 `_make_chunk` 不读取 `doc_meta["manual_tags"]`

- [ ] **Step 3: 修改 _make_chunk 传递 manual_tags**

修改 `src/super_agent/knowledge/chunkers/semantic.py` 中的 `_make_chunk` 方法：

```python
def _make_chunk(
    self, content: str, heading_chain: str, source: str, doc_meta: dict
) -> Chunk:
    full_text = f"{heading_chain}\n{content}" if heading_chain else content
    manual_tags = doc_meta.get("manual_tags")
    meta = build_metadata(file_path=source, manual_tags=manual_tags)
    meta.update({k: v for k, v in doc_meta.items() if k not in ("source", "manual_tags")})
    meta["heading_path"] = heading_chain

    page_nums = doc_meta.get("page_numbers", [])

    return Chunk(
        id=str(uuid.uuid4()),
        content=content,
        heading_chain=heading_chain,
        full_text=full_text,
        metadata=meta,
        page_numbers=page_nums,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_chunkers.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/super_agent/knowledge/chunkers/semantic.py tests/unit/test_chunkers.py
git commit -m "feat: SemanticChunker passes manual_tags through to build_metadata"
```

---

### Task 3: ExcelLoader 移除 topic_tags 硬编码

**Files:**
- Modify: `src/super_agent/knowledge/loaders/excel.py:195-201`
- Test: `tests/unit/test_loaders.py`

- [ ] **Step 1: 写失败测试 — ExcelLoader metadata 不含 topic_tags**

在 `tests/unit/test_loaders.py` 追加：

```python
class TestExcelLoaderNoTopicTags:
    def test_metadata_no_topic_tags_key(self, tmp_path):
        """ExcelLoader 不应在 metadata 中硬编码 topic_tags"""
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["名称", "值"])
        ws.append(["A", "1"])
        ws.append(["B", "2"])
        f = tmp_path / "test.xlsx"
        wb.save(str(f))

        from super_agent.knowledge.loaders.excel import ExcelLoader

        loader = ExcelLoader()
        docs = loader.load(str(f))
        assert len(docs) > 0
        assert "topic_tags" not in docs[0].metadata
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_loaders.py::TestExcelLoaderNoTopicTags -v`
Expected: FAIL — 当前 ExcelLoader 硬编码了 `"topic_tags": []`

- [ ] **Step 3: 移除 ExcelLoader 中的 `"topic_tags": []`**

修改 `src/super_agent/knowledge/loaders/excel.py` 中 `_chunk_sheet` 方法的 meta 字典：

```python
meta = {
    "source": file_name,
    "sheet_name": sheet_name,
    "row_range": f"{row_start}-{row_end}",
    "headers": headers,
}
```

（删除 `"topic_tags": [],` 这一行）

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_loaders.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/super_agent/knowledge/loaders/excel.py tests/unit/test_loaders.py
git commit -m "fix: remove hardcoded topic_tags from ExcelLoader metadata"
```

---

### Task 4: Indexer 接受 file_tags 参数

**Files:**
- Modify: `src/super_agent/knowledge/indexer.py:28-52`
- Test: `tests/unit/test_indexer.py`

- [ ] **Step 1: 写失败测试 — Indexer 传递 file_tags 到 Document metadata**

在 `tests/unit/test_indexer.py` 追加：

```python
def test_indexer_build_passes_file_tags(tmp_path):
    """Indexer.build 应将 file_tags 写入 Document.metadata["manual_tags"]"""
    from langchain_core.documents import Document
    from super_agent.knowledge.chunkers.semantic import SemanticChunker

    store = MagicMock()
    embedder = MagicMock()
    embedder.embed_texts.return_value = [[0.1] * 1024]

    doc_dir = tmp_path / "raw_docs"
    doc_dir.mkdir()
    sample = doc_dir / "test.md"
    sample.write_text("# Test\n一些内容用于测试", encoding="utf-8")

    chunker = SemanticChunker()
    indexer = Indexer(
        store=store,
        embedder=embedder,
        chunker=chunker,
        state_dir=str(tmp_path / "state"),
    )

    file_path = str(sample)
    indexer.build(doc_dir=str(doc_dir), file_tags={file_path: ["自定义标签"]})

    # 验证 store.add 被调用，且 chunks 的 metadata 包含自定义标签
    store.add.assert_called()
    chunks = store.add.call_args[0][0]
    assert any("自定义标签" in c.metadata.get("topic_tags", []) for c in chunks)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_indexer.py::test_indexer_build_passes_file_tags -v`
Expected: FAIL — Indexer.build 不接受 file_tags 参数

- [ ] **Step 3: 修改 Indexer.build 接受 file_tags 并写入 Document metadata**

修改 `src/super_agent/knowledge/indexer.py` 中的 `build` 方法：

```python
def build(self, doc_dir: str, file_tags: dict[str, list[str]] | None = None, **kwargs) -> None:
    doc_path = Path(doc_dir)
    state = self._load_state()

    for fp in doc_path.rglob("*"):
        if not fp.is_file() or fp.suffix.lower() not in supported_extensions():
            continue

        file_hash = self._file_hash(fp)
        rel_path = str(fp)

        if state.get(rel_path) == file_hash:
            continue

        loader = get_loader(fp.suffix.lower())
        documents = loader.load(str(fp))

        # 将自定义标签写入每个 Document 的 metadata
        tags = file_tags.get(str(fp), []) if file_tags else []
        for doc in documents:
            doc.metadata["manual_tags"] = tags

        chunks = self.chunker.chunk(documents, **kwargs)

        if chunks:
            texts = [c.full_text for c in chunks]
            embeddings = self.embedder.embed_texts(texts)
            self.store.add(chunks, embeddings)

        state[rel_path] = file_hash
        self._save_state(state)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_indexer.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/super_agent/knowledge/indexer.py tests/unit/test_indexer.py
git commit -m "feat: Indexer.build accepts file_tags and passes manual_tags to Documents"
```

---

### Task 5: tags.yaml 解析工具

**Files:**
- Create: `src/super_agent/knowledge/tags.py`
- Create: `tests/unit/test_tags.py`

- [ ] **Step 1: 写失败测试 — tags.yaml 解析**

创建 `tests/unit/test_tags.py`：

```python
import pytest
from pathlib import Path
from super_agent.knowledge.tags import parse_tags_yaml, match_file_tags


class TestParseTagsYaml:
    def test_parse_basic(self, tmp_path):
        tags_file = tmp_path / "tags.yaml"
        tags_file.write_text(
            '"网络/防火墙规则.docx": ["网络", "安全", "防火墙"]\n'
            '"运维/巡检手册.pdf": ["运维", "巡检"]\n',
            encoding="utf-8",
        )
        result = parse_tags_yaml(tags_file)
        assert result["网络/防火墙规则.docx"] == ["网络", "安全", "防火墙"]
        assert result["运维/巡检手册.pdf"] == ["运维", "巡检"]

    def test_parse_glob_pattern(self, tmp_path):
        tags_file = tmp_path / "tags.yaml"
        tags_file.write_text(
            '"*.pptx": ["演示文档"]\n',
            encoding="utf-8",
        )
        result = parse_tags_yaml(tags_file)
        assert result["*.pptx"] == ["演示文档"]

    def test_parse_nonexistent_returns_empty(self, tmp_path):
        result = parse_tags_yaml(tmp_path / "nonexistent.yaml")
        assert result == {}


class TestMatchFileTags:
    def test_exact_match(self):
        file_tags = {"raw_docs/test.docx": ["运维"], "*.pptx": ["演示文档"]}
        tags = match_file_tags("raw_docs/test.docx", file_tags)
        assert tags == ["运维"]

    def test_glob_match(self):
        file_tags = {"raw_docs/test.docx": ["运维"], "*.pptx": ["演示文档"]}
        tags = match_file_tags("presentations/intro.pptx", file_tags)
        assert tags == ["演示文档"]

    def test_no_match(self):
        file_tags = {"raw_docs/test.docx": ["运维"]}
        tags = match_file_tags("other/file.pdf", file_tags)
        assert tags == []

    def test_exact_match_priority_over_glob(self):
        file_tags = {"intro.pptx": ["重要"], "*.pptx": ["演示文档"]}
        tags = match_file_tags("intro.pptx", file_tags)
        assert tags == ["重要"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_tags.py -v`
Expected: FAIL — `super_agent.knowledge.tags` 模块不存在

- [ ] **Step 3: 创建 tags.py**

创建 `src/super_agent/knowledge/tags.py`：

```python
from __future__ import annotations

import fnmatch
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def parse_tags_yaml(tags_file: Path) -> dict[str, list[str]]:
    """解析 tags.yaml 文件，返回 {文件路径/glob模式: [标签列表]} 映射。"""
    if not tags_file.exists():
        return {}
    try:
        content = yaml.safe_load(tags_file.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            logger.warning("tags.yaml format invalid, expected mapping, got %s", type(content))
            return {}
        return {str(k): list(v) for k, v in content.items() if isinstance(v, list)}
    except Exception:
        logger.warning("Failed to parse tags.yaml", exc_info=True)
        return {}


def match_file_tags(file_path: str, file_tags: dict[str, list[str]]) -> list[str]:
    """匹配文件路径到标签。精确匹配优先于 glob 匹配。"""
    # 精确匹配
    if file_path in file_tags:
        return file_tags[file_path]

    # glob 匹配（按 key 顺序，返回第一个匹配）
    filename = Path(file_path).name
    for pattern, tags in file_tags.items():
        if "*" in pattern or "?" in pattern:
            if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(file_path, pattern):
                return tags

    return []
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_tags.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/super_agent/knowledge/tags.py tests/unit/test_tags.py
git commit -m "feat: add tags.yaml parser and glob-based file tag matcher"
```

---

### Task 6: Indexer 集成 tags.yaml 自动检测

**Files:**
- Modify: `src/super_agent/knowledge/indexer.py`
- Test: `tests/unit/test_indexer.py`

- [ ] **Step 1: 写失败测试 — Indexer 自动加载 tags.yaml**

在 `tests/unit/test_indexer.py` 追加：

```python
def test_indexer_build_auto_tags_yaml(tmp_path):
    """Indexer.build 自动检测 doc_dir 下的 tags.yaml 并合并到 file_tags"""
    from super_agent.knowledge.chunkers.semantic import SemanticChunker

    store = MagicMock()
    embedder = MagicMock()
    embedder.embed_texts.return_value = [[0.1] * 1024]

    doc_dir = tmp_path / "raw_docs"
    doc_dir.mkdir()
    sample = doc_dir / "test.md"
    sample.write_text("# Test\n一些内容用于测试", encoding="utf-8")

    tags_yaml = doc_dir / "tags.yaml"
    tags_yaml.write_text(f'"{str(sample)}": ["自动标签"]\n', encoding="utf-8")

    chunker = SemanticChunker()
    indexer = Indexer(
        store=store,
        embedder=embedder,
        chunker=chunker,
        state_dir=str(tmp_path / "state"),
    )
    indexer.build(doc_dir=str(doc_dir))

    store.add.assert_called()
    chunks = store.add.call_args[0][0]
    assert any("自动标签" in c.metadata.get("topic_tags", []) for c in chunks)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_indexer.py::test_indexer_build_auto_tags_yaml -v`
Expected: FAIL — Indexer 不检测 tags.yaml

- [ ] **Step 3: 修改 Indexer.build 集成 tags.yaml**

修改 `src/super_agent/knowledge/indexer.py`，在文件顶部新增 import，在 build 方法中检测 tags.yaml：

```python
from super_agent.knowledge.tags import parse_tags_yaml, match_file_tags
```

修改 `build` 方法：

```python
def build(self, doc_dir: str, file_tags: dict[str, list[str]] | None = None, **kwargs) -> None:
    doc_path = Path(doc_dir)
    state = self._load_state()

    # 自动检测 tags.yaml
    tags_yaml_path = doc_path / "tags.yaml"
    yaml_tags = parse_tags_yaml(tags_yaml_path)

    for fp in doc_path.rglob("*"):
        if not fp.is_file() or fp.suffix.lower() not in supported_extensions():
            continue
        if fp.name == "tags.yaml":
            continue

        file_hash = self._file_hash(fp)
        rel_path = str(fp)

        if state.get(rel_path) == file_hash:
            continue

        loader = get_loader(fp.suffix.lower())
        documents = loader.load(str(fp))

        # 合并: 调用方 file_tags + tags.yaml 匹配
        manual_tags = file_tags.get(str(fp), []) if file_tags else []
        yaml_matched = match_file_tags(str(fp), yaml_tags)
        merged = manual_tags + [t for t in yaml_matched if t not in manual_tags]

        for doc in documents:
            doc.metadata["manual_tags"] = merged

        chunks = self.chunker.chunk(documents, **kwargs)

        if chunks:
            texts = [c.full_text for c in chunks]
            embeddings = self.embedder.embed_texts(texts)
            self.store.add(chunks, embeddings)

        state[rel_path] = file_hash
        self._save_state(state)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_indexer.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/super_agent/knowledge/indexer.py tests/unit/test_indexer.py
git commit -m "feat: Indexer auto-detects tags.yaml and merges with file_tags"
```

---

### Task 7: pyproject.toml 新增 python-pptx 依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 添加 python-pptx 到 dependencies**

在 `pyproject.toml` 的 `dependencies` 列表中，在 `"xlrd>=2.0",` 之后添加：

```
    "python-pptx>=0.6.21",
```

- [ ] **Step 2: 安装依赖**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && uv sync`
Expected: 依赖安装成功

- [ ] **Step 3: 验证 import 可用**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -c "import pptx; print('python-pptx', pptx.__version__)"`
Expected: 输出版本号

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add python-pptx dependency for PPTLoader"
```

---

### Task 8: PPTLoader — .pptx 加载核心

**Files:**
- Create: `src/super_agent/knowledge/loaders/ppt.py`
- Test: `tests/unit/test_loaders.py`

- [ ] **Step 1: 写失败测试 — PPTLoader 注册和基本加载**

在 `tests/unit/test_loaders.py` 追加：

```python
class TestPPTLoader:
    def test_pptx_extension_registered(self):
        loader = get_loader(".pptx")
        assert loader is not None
        assert ".pptx" in loader.supported_extensions()

    def test_ppt_extension_registered(self):
        loader = get_loader(".ppt")
        assert loader is not None
        assert ".ppt" in loader.supported_extensions()

    def test_load_pptx_sample(self, tmp_path):
        """用 python-pptx 构造一个最小 pptx 文件并加载"""
        from pptx import Presentation
        from pptx.util import Inches, Pt

        prs = Presentation()
        slide_layout = prs.slide_layouts[1]  # Title and Content
        slide = prs.slides.add_slide(slide_layout)
        slide.shapes.title.text = "测试标题"
        body = slide.placeholders[1]
        body.text = "测试正文内容"

        f = tmp_path / "test.pptx"
        prs.save(str(f))

        from super_agent.knowledge.loaders.ppt import PPTLoader

        loader = PPTLoader()
        docs = loader.load(str(f))
        assert len(docs) == 1
        assert "测试标题" in docs[0].page_content
        assert "测试正文内容" in docs[0].page_content

    def test_pptx_metadata(self, tmp_path):
        from pptx import Presentation

        prs = Presentation()
        slide_layout = prs.slide_layouts[1]
        slide = prs.slides.add_slide(slide_layout)
        slide.shapes.title.text = "标题"

        f = tmp_path / "test.pptx"
        prs.save(str(f))

        from super_agent.knowledge.loaders.ppt import PPTLoader

        loader = PPTLoader()
        docs = loader.load(str(f))
        assert len(docs) == 1
        meta = docs[0].metadata
        assert meta["source"] == str(f)
        assert meta["slide_number"] == 1
        assert meta["total_slides"] == 1
        assert "has_notes" in meta
        assert "has_tables" in meta
        assert "has_images" in meta
        assert "ocr_used" in meta
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_loaders.py::TestPPTLoader -v`
Expected: FAIL — PPTLoader 模块不存在

- [ ] **Step 3: 创建 PPTLoader — .pptx 核心实现**

创建 `src/super_agent/knowledge/loaders/ppt.py`：

```python
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from langchain_core.documents import Document

from super_agent.config import settings
from super_agent.knowledge.loaders.base import BaseLoader

logger = logging.getLogger(__name__)

_PADDLEOCR_AVAILABLE: bool | None = None


def _check_paddleocr() -> bool:
    global _PADDLEOCR_AVAILABLE
    if _PADDLEOCR_AVAILABLE is None:
        try:
            import paddleocr  # noqa: F401

            _PADDLEOCR_AVAILABLE = True
        except ImportError:
            _PADDLEOCR_AVAILABLE = False
            logger.warning(
                "paddleocr is not installed. Image OCR in PPT will be skipped. "
                "Install with: uv sync --extra ml"
            )
    return _PADDLEOCR_AVAILABLE


def _get_ocr_engine():
    from functools import lru_cache

    @lru_cache(maxsize=1)
    def _cached():
        if not _check_paddleocr():
            return None
        from paddleocr import PaddleOCR

        return PaddleOCR(use_gpu=settings.ocr.use_gpu, lang=settings.ocr.lang, show_log=False)

    return _cached()


class PPTLoader(BaseLoader):
    def load(self, source: str) -> list[Document]:
        ext = Path(source).suffix.lower()
        if ext == ".pptx":
            return self._load_pptx(source)
        if ext == ".ppt":
            return self._load_ppt(source)
        raise ValueError(f"Unsupported extension: {ext}")

    def supported_extensions(self) -> list[str]:
        return [".pptx", ".ppt"]

    def _load_ppt(self, source: str) -> list[Document]:
        """Convert .ppt to .pptx via LibreOffice, then load as pptx."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                subprocess.run(
                    [
                        "libreoffice",
                        "--headless",
                        "--convert-to",
                        "pptx",
                        "--outdir",
                        tmp_dir,
                        source,
                    ],
                    timeout=60,
                    check=True,
                    capture_output=True,
                )
            except FileNotFoundError:
                raise RuntimeError(
                    "LibreOffice is required for .ppt conversion but not found. "
                    "Install LibreOffice or convert .ppt to .pptx manually."
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    f"LibreOffice conversion timed out (60s) for {source}"
                )

            pptx_path = Path(tmp_dir) / (Path(source).stem + ".pptx")
            if not pptx_path.exists():
                raise RuntimeError(f"LibreOffice conversion failed: {pptx_path} not found")

            return self._load_pptx(str(pptx_path))

    def _load_pptx(self, source: str) -> list[Document]:
        from pptx import Presentation

        prs = Presentation(source)
        total = len(prs.slides)
        docs: list[Document] = []

        for idx, slide in enumerate(prs.slides, start=1):
            content_parts: list[str] = []
            has_notes = False
            has_tables = False
            has_images = False
            ocr_used = False

            for shape in slide.shapes:
                # 文本
                if shape.has_text_frame:
                    text = shape.text_frame.text.strip()
                    if text:
                        content_parts.append(text)

                # 表格
                if shape.has_table:
                    has_tables = True
                    table_text = self._extract_table(shape.table)
                    if table_text:
                        content_parts.append(table_text)

                # 图片 OCR
                if shape.shape_type == 13:  # MSO_SHAPE_TYPE.PICTURE
                    has_images = True
                    if settings.ocr.enabled:
                        ocr_text = self._ocr_shape(shape)
                        if ocr_text:
                            ocr_used = True
                            content_parts.append(f"[OCR] {ocr_text}")

            # 演讲者备注
            notes_text = self._extract_notes(slide)
            if notes_text:
                has_notes = True
                content_parts.append(f"[备注] {notes_text}")

            page_content = "\n\n".join(content_parts)
            if not page_content.strip():
                continue

            docs.append(
                Document(
                    page_content=page_content,
                    metadata={
                        "source": source,
                        "slide_number": idx,
                        "total_slides": total,
                        "has_notes": has_notes,
                        "has_tables": has_tables,
                        "has_images": has_images,
                        "ocr_used": ocr_used,
                    },
                )
            )

        return docs

    @staticmethod
    def _extract_table(table) -> str:
        """将表格转换为 Markdown 格式。"""
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            rows.append("| " + " | ".join(cells) + " |")
        if not rows:
            return ""
        # 添加表头分隔行
        header_sep = "| " + " | ".join("---" for _ in table.rows[0].cells) + " |"
        rows.insert(1, header_sep)
        return "\n".join(rows)

    @staticmethod
    def _extract_notes(slide) -> str:
        """提取演讲者备注。"""
        if slide.has_notes_slide:
            notes_frame = slide.notes_slide.notes_text_frame
            text = notes_frame.text.strip()
            return text
        return ""

    def _ocr_shape(self, shape) -> str:
        """对 PPT 中的图片执行 OCR。"""
        engine = _get_ocr_engine()
        if engine is None:
            return ""
        try:
            image = shape.image
            img_bytes = image.blob
            result = engine.ocr(img_bytes, cls=True)
            if not result or not result[0]:
                return ""
            lines = [line[1][0] for line in result[0]]
            return "\n".join(lines)
        except Exception:
            logger.warning("OCR failed for a PPT image, skipping", exc_info=True)
            return ""
```

- [ ] **Step 4: 注册 PPTLoader**

修改 `src/super_agent/knowledge/loaders/__init__.py`，在 `from super_agent.knowledge.loaders.excel import ExcelLoader` 后添加：

```python
from super_agent.knowledge.loaders.ppt import PPTLoader
```

在 `_register(ExcelLoader)` 后添加：

```python
_register(PPTLoader)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_loaders.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/super_agent/knowledge/loaders/ppt.py src/super_agent/knowledge/loaders/__init__.py tests/unit/test_loaders.py
git commit -m "feat: add PPTLoader with .pptx/.ppt support, table/notes/OCR extraction"
```

---

### Task 9: PPTLoader — 多幻灯片与边界测试

**Files:**
- Test: `tests/unit/test_loaders.py`

- [ ] **Step 1: 写测试 — 多幻灯片、空幻灯片、表格、备注**

在 `tests/unit/test_loaders.py` 的 TestPPTLoader 类中追加：

```python
    def test_multiple_slides(self, tmp_path):
        """多张幻灯片各产出 1 个 Document"""
        from pptx import Presentation

        prs = Presentation()
        for i in range(3):
            slide_layout = prs.slide_layouts[1]
            slide = prs.slides.add_slide(slide_layout)
            slide.shapes.title.text = f"第{i + 1}页"
            body = slide.placeholders[1]
            body.text = f"内容{i + 1}"

        f = tmp_path / "multi.pptx"
        prs.save(str(f))

        from super_agent.knowledge.loaders.ppt import PPTLoader

        loader = PPTLoader()
        docs = loader.load(str(f))
        assert len(docs) == 3
        assert docs[0].metadata["slide_number"] == 1
        assert docs[1].metadata["slide_number"] == 2
        assert docs[2].metadata["slide_number"] == 3
        assert docs[0].metadata["total_slides"] == 3

    def test_table_extraction(self, tmp_path):
        """表格提取为 Markdown 格式"""
        from pptx import Presentation
        from pptx.util import Inches

        prs = Presentation()
        slide_layout = prs.slide_layouts[5]  # Blank
        slide = prs.slides.add_slide(slide_layout)

        rows, cols = 2, 2
        table_shape = slide.shapes.add_table(rows, cols, Inches(1), Inches(1), Inches(4), Inches(2))
        table = table_shape.table
        table.cell(0, 0).text = "名称"
        table.cell(0, 1).text = "值"
        table.cell(1, 0).text = "CPU"
        table.cell(1, 1).text = "90%"

        f = tmp_path / "table.pptx"
        prs.save(str(f))

        from super_agent.knowledge.loaders.ppt import PPTLoader

        loader = PPTLoader()
        docs = loader.load(str(f))
        assert len(docs) == 1
        assert "|" in docs[0].page_content
        assert "CPU" in docs[0].page_content
        assert docs[0].metadata["has_tables"] is True

    def test_notes_extraction(self, tmp_path):
        """演讲者备注以 [备注] 前缀提取"""
        from pptx import Presentation

        prs = Presentation()
        slide_layout = prs.slide_layouts[1]
        slide = prs.slides.add_slide(slide_layout)
        slide.shapes.title.text = "标题"
        notes_slide = slide.notes_slide
        notes_slide.notes_text_frame.text = "这是备注内容"

        f = tmp_path / "notes.pptx"
        prs.save(str(f))

        from super_agent.knowledge.loaders.ppt import PPTLoader

        loader = PPTLoader()
        docs = loader.load(str(f))
        assert len(docs) == 1
        assert "[备注]" in docs[0].page_content
        assert "这是备注内容" in docs[0].page_content
        assert docs[0].metadata["has_notes"] is True

    def test_unsupported_extension_raises(self):
        from super_agent.knowledge.loaders.ppt import PPTLoader

        loader = PPTLoader()
        with pytest.raises(ValueError, match="Unsupported extension"):
            loader.load("test.txt")
```

- [ ] **Step 2: 运行测试确认通过**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/test_loaders.py::TestPPTLoader -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_loaders.py
git commit -m "test: add multi-slide, table, notes, and edge case tests for PPTLoader"
```

---

### Task 10: 全量测试验证

**Files:** 无新文件

- [ ] **Step 1: 运行全部单元测试**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/ -v`
Expected: ALL PASS

- [ ] **Step 2: 运行 ruff 检查代码风格**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m ruff check src/super_agent/knowledge/loaders/ppt.py src/super_agent/knowledge/tags.py src/super_agent/knowledge/metadata.py src/super_agent/knowledge/indexer.py src/super_agent/knowledge/chunkers/semantic.py src/super_agent/knowledge/loaders/excel.py`
Expected: 无报错（或仅有可忽略的提示）

- [ ] **Step 3: 如有 ruff 报错则修复后重新检查**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m ruff check --fix src/super_agent/knowledge/loaders/ppt.py src/super_agent/knowledge/tags.py src/super_agent/knowledge/metadata.py src/super_agent/knowledge/indexer.py src/super_agent/knowledge/chunkers/semantic.py src/super_agent/knowledge/loaders/excel.py`

- [ ] **Step 4: 最终确认全量测试通过**

Run: `cd /d/workspace/jdd/创新项目组/IT运维数字员工/super_agent && python -m pytest tests/unit/ -v`
Expected: ALL PASS

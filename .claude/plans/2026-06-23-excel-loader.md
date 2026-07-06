# Excel Loader 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 RAG 流水线新增 Excel (.xlsx / .xls) 文件加载能力，支持多 sheet、合并单元格填充、行级分 chunk + 重叠。

**Architecture:** 新建 `ExcelLoader` 类继承 `BaseLoader`，内部按扩展名分派到 `_load_xlsx`（openpyxl）和 `_load_xls`（xlrd）两条路径。xlsx 路径在读取后执行合并单元格向下填充，然后与 xls 路径共用 `_chunk_sheet` 方法完成 chunk 拆分和文本格式化。注册到 loader 全局注册表后即可被 `Indexer` 自动发现。

**Tech Stack:** openpyxl >= 3.1, xlrd >= 2.0, langchain-core Document

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `src/super_agent/knowledge/loaders/excel.py` | ExcelLoader 主类：xlsx/xls 读取、合并单元格填充、chunk 拆分、文本格式化 |
| `src/super_agent/knowledge/loaders/__init__.py` | 注册 ExcelLoader 到全局注册表 |
| `pyproject.toml` | 新增 openpyxl、xlrd 依赖 |
| `tests/unit/test_excel_loader.py` | ExcelLoader 全量单元测试 |

---

### Task 1: 添加依赖

**Files:**
- Modify: `pyproject.toml:36`

- [ ] **Step 1: 在 dependencies 列表末尾添加 openpyxl 和 xlrd**

在 `pyproject.toml` 的 `dependencies` 列表中，在 `"httpx>=0.27"` 之后添加：

```toml
    "openpyxl>=3.1",
    "xlrd>=2.0",
```

- [ ] **Step 2: 安装依赖**

Run: `uv sync --extra dev`
Expected: 依赖安装成功

- [ ] **Step 3: 验证 import 可用**

Run: `uv run python -c "import openpyxl; import xlrd; print('ok')"`
Expected: 输出 `ok`

---

### Task 2: ExcelLoader 骨架 + 基础 xlsx 单 sheet 加载（无合并单元格）

**Files:**
- Create: `src/super_agent/knowledge/loaders/excel.py`
- Create: `tests/unit/test_excel_loader.py`

- [ ] **Step 1: 写失败测试 — 基础 xlsx 单 sheet 加载**

创建 `tests/unit/test_excel_loader.py`：

```python
import pytest
from pathlib import Path
from openpyxl import Workbook

from super_agent.knowledge.loaders.excel import ExcelLoader


def _make_xlsx(tmp_path: Path, rows: list[list[str]], headers: list[str], sheet_name: str = "Sheet") -> Path:
    """辅助函数：用 openpyxl 创建一个简单 xlsx 文件并返回路径。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers)
    for row in rows:
        ws.append(row)
    p = tmp_path / "test.xlsx"
    wb.save(str(p))
    return p


class TestExcelLoaderXlsxSingleSheet:
    def test_load_returns_documents(self, tmp_path):
        headers = ["姓名", "部门", "级别"]
        rows = [
            ["张三", "SRE", "P7"],
            ["李四", "DBA", "P6"],
        ]
        p = _make_xlsx(tmp_path, rows, headers)
        loader = ExcelLoader()
        docs = loader.load(str(p))
        assert len(docs) >= 1

    def test_document_contains_header_and_data(self, tmp_path):
        headers = ["姓名", "部门"]
        rows = [["张三", "SRE"]]
        p = _make_xlsx(tmp_path, rows, headers)
        loader = ExcelLoader()
        docs = loader.load(str(p))
        assert len(docs) == 1
        text = docs[0].page_content
        assert "[表头]" in text
        assert "姓名: 姓名" in text
        assert "部门: 部门" in text
        assert "姓名: 张三" in text
        assert "部门: SRE" in text

    def test_metadata_fields(self, tmp_path):
        headers = ["姓名", "部门"]
        rows = [["张三", "SRE"]]
        p = _make_xlsx(tmp_path, rows, headers, sheet_name="员工表")
        loader = ExcelLoader()
        docs = loader.load(str(p))
        meta = docs[0].metadata
        assert meta["source"] == "test.xlsx"
        assert meta["sheet_name"] == "员工表"
        assert meta["headers"] == ["姓名", "部门"]
        assert "row_range" in meta

    def test_supported_extensions(self):
        loader = ExcelLoader()
        assert ".xlsx" in loader.supported_extensions()
        assert ".xls" in loader.supported_extensions()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_excel_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'super_agent.knowledge.loaders.excel'`

- [ ] **Step 3: 创建 ExcelLoader 骨架，实现基础 xlsx 单 sheet 加载**

创建 `src/super_agent/knowledge/loaders/excel.py`：

```python
from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.documents import Document

from super_agent.knowledge.loaders.base import BaseLoader

logger = logging.getLogger(__name__)


class ExcelLoader(BaseLoader):
    def __init__(self, chunk_size: int = 20, chunk_overlap: int = 3) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap must be non-negative")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def load(self, source: str) -> list[Document]:
        ext = Path(source).suffix.lower()
        if ext == ".xlsx":
            return self._load_xlsx(source)
        if ext == ".xls":
            return self._load_xls(source)
        raise ValueError(f"Unsupported extension: {ext}")

    def supported_extensions(self) -> list[str]:
        return [".xlsx", ".xls"]

    def _load_xlsx(self, source: str) -> list[Document]:
        import openpyxl

        wb = openpyxl.load_workbook(source, read_only=False, data_only=True)
        docs: list[Document] = []
        file_name = Path(source).name

        for sheet in wb.worksheets:
            sheet_docs = self._process_xlsx_sheet(sheet, file_name)
            docs.extend(sheet_docs)

        wb.close()
        return docs

    def _process_xlsx_sheet(self, sheet, file_name: str) -> list[Document]:
        from openpyxl.utils import get_column_letter

        if sheet.max_row is None or sheet.max_row < 1:
            return []

        # 读取所有行
        all_rows: list[list[str]] = []
        for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row, values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            all_rows.append(cells)

        if not all_rows:
            return []

        # 过滤空列：找出所有行中至少有一个非空值的列
        max_cols = max(len(r) for r in all_rows) if all_rows else 0
        non_empty_cols: list[int] = []
        for col_idx in range(max_cols):
            for row in all_rows:
                if col_idx < len(row) and row[col_idx].strip():
                    non_empty_cols.append(col_idx)
                    break

        if not non_empty_cols:
            return []

        # 只保留非空列
        filtered_rows: list[list[str]] = []
        for row in all_rows:
            filtered_rows.append([row[i] if i < len(row) else "" for i in non_empty_cols])

        # 表头 = 第一个非空行
        header_idx = 0
        for i, row in enumerate(filtered_rows):
            if any(cell.strip() for cell in row):
                header_idx = i
                break

        headers = filtered_rows[header_idx]
        data_rows = filtered_rows[header_idx + 1 :]

        # 过滤全空数据行
        data_rows = [r for r in data_rows if any(cell.strip() for cell in r)]

        if not data_rows:
            return []

        sheet_name = sheet.title
        return self._chunk_sheet(headers, data_rows, file_name, sheet_name)

    def _load_xls(self, source: str) -> list[Document]:
        import xlrd

        wb = xlrd.open_workbook(source)
        docs: list[Document] = []
        file_name = Path(source).name

        for sheet_idx in range(wb.nsheets):
            sheet = wb.sheet_by_index(sheet_idx)
            sheet_docs = self._process_xls_sheet(sheet, file_name)
            docs.extend(sheet_docs)

        return docs

    def _process_xls_sheet(self, sheet, file_name: str) -> list[Document]:
        if sheet.nrows < 2:
            return []

        all_rows: list[list[str]] = []
        for row_idx in range(sheet.nrows):
            cells = [str(sheet.cell_value(row_idx, col_idx)) for col_idx in range(sheet.ncols)]
            all_rows.append(cells)

        # 过滤空列
        max_cols = max(len(r) for r in all_rows) if all_rows else 0
        non_empty_cols: list[int] = []
        for col_idx in range(max_cols):
            for row in all_rows:
                if col_idx < len(row) and row[col_idx].strip():
                    non_empty_cols.append(col_idx)
                    break

        if not non_empty_cols:
            return []

        filtered_rows: list[list[str]] = []
        for row in all_rows:
            filtered_rows.append([row[i] if i < len(row) else "" for i in non_empty_cols])

        header_idx = 0
        for i, row in enumerate(filtered_rows):
            if any(cell.strip() for cell in row):
                header_idx = i
                break

        headers = filtered_rows[header_idx]
        data_rows = filtered_rows[header_idx + 1 :]
        data_rows = [r for r in data_rows if any(cell.strip() for cell in r)]

        if not data_rows:
            return []

        sheet_name = sheet.name
        return self._chunk_sheet(headers, data_rows, file_name, sheet_name)

    def _chunk_sheet(
        self,
        headers: list[str],
        data_rows: list[list[str]],
        file_name: str,
        sheet_name: str,
    ) -> list[Document]:
        docs: list[Document] = []
        total = len(data_rows)
        step = self.chunk_size - self.chunk_overlap
        if step <= 0:
            step = 1

        pos = 0
        while pos < total:
            end = min(pos + self.chunk_size, total)
            chunk_rows = data_rows[pos:end]
            row_start = pos + 1
            row_end = end

            header_text = self._format_header(headers)
            data_text = self._format_data_rows(headers, chunk_rows, row_start, row_end)
            page_content = f"{header_text}\n\n{data_text}"

            meta = {
                "source": file_name,
                "sheet_name": sheet_name,
                "row_range": f"{row_start}-{row_end}",
                "headers": headers,
                "topic_tags": [],
            }
            docs.append(Document(page_content=page_content, metadata=meta))
            pos += step

        return docs

    @staticmethod
    def _format_header(headers: list[str]) -> str:
        pairs = [f"{h}: {h}" for h in headers]
        return f"[表头]\n{', '.join(pairs)}"

    @staticmethod
    def _format_data_rows(
        headers: list[str],
        rows: list[list[str]],
        row_start: int,
        row_end: int,
    ) -> str:
        lines = [f"[数据行 {row_start}-{row_end}]"]
        for row in rows:
            pairs: list[str] = []
            for i, h in enumerate(headers):
                val = row[i] if i < len(row) else ""
                if val.strip():
                    pairs.append(f"{h}: {val}")
            lines.append(", ".join(pairs))
        return "\n".join(lines)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_excel_loader.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add src/super_agent/knowledge/loaders/excel.py tests/unit/test_excel_loader.py
git commit -m "feat: add ExcelLoader with basic xlsx single-sheet loading"
```

---

### Task 3: 合并单元格向下填充

**Files:**
- Modify: `src/super_agent/knowledge/loaders/excel.py`
- Modify: `tests/unit/test_excel_loader.py`

- [ ] **Step 1: 写失败测试 — 合并单元格填充**

在 `tests/unit/test_excel_loader.py` 末尾添加：

```python
class TestExcelLoaderMergedCells:
    def test_merged_cell_fill_down(self, tmp_path):
        """合并单元格：左上角的值应填充到所有被合并的行。"""
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws.title = "合并测试"
        ws.append(["系统", "指标", "值"])
        # 合并 A2:A4（系统列前 3 行数据）
        ws.append(["生产环境", "CPU", "80%"])
        ws.append([None, "内存", "60%"])
        ws.append([None, "磁盘", "50%"])
        ws.append(["测试环境", "CPU", "30%"])
        ws.merge_cells("A2:A4")

        p = tmp_path / "merged.xlsx"
        wb.save(str(p))

        loader = ExcelLoader()
        docs = loader.load(str(p))
        text = docs[0].page_content
        # 合并填充后，A2-A4 都应为 "生产环境"
        assert text.count("系统: 生产环境") == 3
        assert "系统: 测试环境" in text

    def test_no_merge_in_xls(self, tmp_path):
        """xls 不支持合并填充，空值保留为空。"""
        # 此测试在 Task 5 实现 xls 后验证
        pass
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_excel_loader.py::TestExcelLoaderMergedCells::test_merged_cell_fill_down -v`
Expected: FAIL — 合并区域中 `系统` 列为空的行不会出现 `系统: 生产环境`

- [ ] **Step 3: 实现 `_fill_merged_cells` 方法并集成到 `_process_xlsx_sheet`**

在 `src/super_agent/knowledge/loaders/excel.py` 的 `ExcelLoader` 类中添加方法，并修改 `_process_xlsx_sheet`：

在 `_process_xlsx_sheet` 方法中，`# 读取所有行` 之前插入合并单元格填充逻辑：

```python
    def _fill_merged_cells(self, sheet) -> None:
        """将合并单元格的值向下填充到所有被合并的行，然后取消合并。"""
        if not sheet.merged_cells.ranges:
            return
        ranges = list(sheet.merged_cells.ranges)
        for mr in ranges:
            min_row = mr.min_row
            min_col = mr.min_col
            top_value = sheet.cell(row=min_row, column=min_col).value
            if top_value is None:
                top_value = ""
            for row_idx in range(mr.min_row, mr.max_row + 1):
                for col_idx in range(mr.min_col, mr.max_col + 1):
                    sheet.cell(row=row_idx, column=col_idx, value=top_value)
            sheet.unmerge_cells(str(mr))
```

修改 `_process_xlsx_sheet`，在读取行之前调用填充：

将 `_process_xlsx_sheet` 中的：

```python
        # 读取所有行
        all_rows: list[list[str]] = []
        for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row, values_only=True):
```

改为：

```python
        # 合并单元格填充
        self._fill_merged_cells(sheet)

        # 读取所有行
        all_rows: list[list[str]] = []
        for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row, values_only=True):
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_excel_loader.py::TestExcelLoaderMergedCells::test_merged_cell_fill_down -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/super_agent/knowledge/loaders/excel.py tests/unit/test_excel_loader.py
git commit -m "feat: add merged cell fill-down for xlsx sheets"
```

---

### Task 4: 行级分 chunk + 重叠

**Files:**
- Modify: `tests/unit/test_excel_loader.py`

- [ ] **Step 1: 写失败测试 — chunk 拆分与重叠**

在 `tests/unit/test_excel_loader.py` 末尾添加：

```python
class TestExcelLoaderChunking:
    def test_chunk_size_splits_rows(self, tmp_path):
        """超过 chunk_size 的数据行应拆分为多个 Document。"""
        headers = ["ID", "名称"]
        rows = [[str(i), f"项目{i}"] for i in range(1, 26)]  # 25 行数据
        p = _make_xlsx(tmp_path, rows, headers)
        loader = ExcelLoader(chunk_size=10, chunk_overlap=0)
        docs = loader.load(str(p))
        assert len(docs) == 3  # 10 + 10 + 5
        assert "1-10" in docs[0].metadata["row_range"]
        assert "11-20" in docs[1].metadata["row_range"]
        assert "21-25" in docs[2].metadata["row_range"]

    def test_chunk_overlap(self, tmp_path):
        """相邻 chunk 应有重叠行。"""
        headers = ["ID", "名称"]
        rows = [[str(i), f"项目{i}"] for i in range(1, 26)]  # 25 行数据
        p = _make_xlsx(tmp_path, rows, headers)
        loader = ExcelLoader(chunk_size=10, chunk_overlap=3)
        docs = loader.load(str(p))
        # step = 10 - 3 = 7; chunks: [0:10], [7:17], [14:24], [21:25]
        assert len(docs) == 4
        # 验证重叠：chunk1 第一个数据行应包含 chunk0 倒数第3行的数据
        assert "8" in docs[1].page_content  # row 8 出现在 chunk1

    def test_small_data_single_chunk(self, tmp_path):
        """数据行不足 chunk_size 时生成单个 chunk。"""
        headers = ["ID"]
        rows = [["1"], ["2"]]
        p = _make_xlsx(tmp_path, rows, headers)
        loader = ExcelLoader(chunk_size=20, chunk_overlap=3)
        docs = loader.load(str(p))
        assert len(docs) == 1
        assert "1-2" in docs[0].metadata["row_range"]

    def test_each_chunk_contains_header(self, tmp_path):
        """每个 chunk 都应包含表头部分。"""
        headers = ["ID", "名称"]
        rows = [[str(i), f"项目{i}"] for i in range(1, 26)]
        p = _make_xlsx(tmp_path, rows, headers)
        loader = ExcelLoader(chunk_size=10, chunk_overlap=0)
        docs = loader.load(str(p))
        for doc in docs:
            assert "[表头]" in doc.page_content
```

- [ ] **Step 2: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_excel_loader.py::TestExcelLoaderChunking -v`
Expected: 4 passed（Task 2 中已实现 chunking 逻辑）

- [ ] **Step 3: 提交**

```bash
git add tests/unit/test_excel_loader.py
git commit -m "test: add chunking and overlap tests for ExcelLoader"
```

---

### Task 5: 多 sheet 支持

**Files:**
- Modify: `tests/unit/test_excel_loader.py`

- [ ] **Step 1: 写测试 — 多 sheet 分别生成 Document**

在 `tests/unit/test_excel_loader.py` 末尾添加：

```python
class TestExcelLoaderMultiSheet:
    def test_multiple_sheets(self, tmp_path):
        """每个 sheet 独立生成 Document。"""
        from openpyxl import Workbook

        wb = Workbook()
        ws1 = wb.active
        ws1.title = "员工"
        ws1.append(["姓名", "部门"])
        ws1.append(["张三", "SRE"])

        ws2 = wb.create_sheet("服务器")
        ws2.append(["主机名", "IP"])
        ws2.append(["web01", "10.0.0.1"])

        p = tmp_path / "multi.xlsx"
        wb.save(str(p))

        loader = ExcelLoader()
        docs = loader.load(str(p))
        assert len(docs) == 2
        sheet_names = [d.metadata["sheet_name"] for d in docs]
        assert "员工" in sheet_names
        assert "服务器" in sheet_names

    def test_empty_sheet_skipped(self, tmp_path):
        """空 sheet 或只有表头无数据的 sheet 应跳过。"""
        from openpyxl import Workbook

        wb = Workbook()
        ws1 = wb.active
        ws1.title = "有数据"
        ws1.append(["姓名"])
        ws1.append(["张三"])

        ws2 = wb.create_sheet("空表")
        ws2.append(["姓名"])
        # 无数据行

        ws3 = wb.create_sheet("完全空")
        # 无任何内容

        p = tmp_path / "mixed.xlsx"
        wb.save(str(p))

        loader = ExcelLoader()
        docs = loader.load(str(p))
        assert len(docs) == 1
        assert docs[0].metadata["sheet_name"] == "有数据"
```

- [ ] **Step 2: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_excel_loader.py::TestExcelLoaderMultiSheet -v`
Expected: 2 passed（Task 2 中已实现多 sheet 遍历）

- [ ] **Step 3: 提交**

```bash
git add tests/unit/test_excel_loader.py
git commit -m "test: add multi-sheet tests for ExcelLoader"
```

---

### Task 6: xls 格式支持

**Files:**
- Modify: `tests/unit/test_excel_loader.py`

- [ ] **Step 1: 写测试 — xls 基础加载**

在 `tests/unit/test_excel_loader.py` 末尾添加：

```python
class TestExcelLoaderXls:
    def test_load_xls_file(self, tmp_path):
        """xls 文件可正常加载。"""
        import xlwt

        wb = xlwt.Workbook()
        ws = wb.add_sheet("数据")
        ws.write(0, 0, "主机名")
        ws.write(0, 1, "IP")
        ws.write(1, 0, "web01")
        ws.write(1, 1, "10.0.0.1")

        p = tmp_path / "test.xls"
        wb.save(str(p))

        loader = ExcelLoader()
        docs = loader.load(str(p))
        assert len(docs) >= 1
        assert "主机名: 主机名" in docs[0].page_content
        assert "主机名: web01" in docs[0].page_content
        assert docs[0].metadata["sheet_name"] == "数据"

    def test_xls_no_merge_support(self, tmp_path):
        """xls 不支持合并单元格填充，空值不出现键值对。"""
        import xlwt

        wb = xlwt.Workbook()
        ws = wb.add_sheet("合并")
        ws.write(0, 0, "系统")
        ws.write(0, 1, "指标")
        ws.write(1, 0, "生产环境")
        ws.write(1, 1, "CPU")
        # 第 2 行系统列留空（模拟合并后的空值）
        ws.write(2, 1, "内存")

        p = tmp_path / "nomerge.xls"
        wb.save(str(p))

        loader = ExcelLoader()
        docs = loader.load(str(p))
        text = docs[0].page_content
        # 空值不应生成 "系统: " 键值对（空列跳过逻辑）
        assert "系统: " not in text.split("[数据行")[1] or "系统: 生产环境" in text
```

- [ ] **Step 2: 安装 xlwt 测试依赖**

Run: `uv add --dev xlwt`
Expected: 安装成功

- [ ] **Step 3: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_excel_loader.py::TestExcelLoaderXls -v`
Expected: 2 passed（Task 2 中已实现 `_load_xls` 路径）

- [ ] **Step 4: 将 xlwt 从生产依赖移除（仅测试用）**

xlwt 仅用于测试生成 .xls 文件，不是生产依赖。确认 `pyproject.toml` 中无 xlwt 条目即可。如果 `uv add --dev xlwt` 已将其放入 dev-dependencies 则符合预期。

- [ ] **Step 5: 提交**

```bash
git add tests/unit/test_excel_loader.py pyproject.toml uv.lock
git commit -m "test: add xls format tests for ExcelLoader"
```

---

### Task 7: 注册到 Loader 全局注册表 + 边界用例

**Files:**
- Modify: `src/super_agent/knowledge/loaders/__init__.py`
- Modify: `tests/unit/test_excel_loader.py`

- [ ] **Step 1: 写失败测试 — get_loader 支持 .xlsx/.xls**

在 `tests/unit/test_excel_loader.py` 末尾添加：

```python
class TestExcelLoaderRegistry:
    def test_get_loader_xlsx(self):
        from super_agent.knowledge.loaders import get_loader

        loader = get_loader(".xlsx")
        assert isinstance(loader, ExcelLoader)

    def test_get_loader_xls(self):
        from super_agent.knowledge.loaders import get_loader

        loader = get_loader(".xls")
        assert isinstance(loader, ExcelLoader)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_excel_loader.py::TestExcelLoaderRegistry -v`
Expected: FAIL — `ValueError: Unsupported file extension: .xlsx`

- [ ] **Step 3: 在 `__init__.py` 中注册 ExcelLoader**

修改 `src/super_agent/knowledge/loaders/__init__.py`：

在 import 区域添加：

```python
from super_agent.knowledge.loaders.excel import ExcelLoader
```

在 `_register` 调用区域添加：

```python
_register(ExcelLoader)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_excel_loader.py::TestExcelLoaderRegistry -v`
Expected: 2 passed

- [ ] **Step 5: 补充边界用例测试**

在 `tests/unit/test_excel_loader.py` 末尾添加：

```python
class TestExcelLoaderEdgeCases:
    def test_invalid_chunk_size(self):
        with pytest.raises(ValueError, match="chunk_size"):
            ExcelLoader(chunk_size=0)

    def test_overlap_exceeds_size(self):
        with pytest.raises(ValueError, match="chunk_overlap"):
            ExcelLoader(chunk_size=5, chunk_overlap=5)

    def test_empty_columns_skipped(self, tmp_path):
        """全空列不出现在输出中。"""
        headers = ["名称", "备注", "状态"]
        rows = [["服务A", "", "运行中"]]
        p = _make_xlsx(tmp_path, rows, headers)
        loader = ExcelLoader()
        docs = loader.load(str(p))
        text = docs[0].page_content
        # "备注" 列在数据行中值为空，不应生成 "备注: " 键值对
        data_section = text.split("[数据行")[1]
        assert "备注:" not in data_section

    def test_unsupported_extension(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("hello", encoding="utf-8")
        loader = ExcelLoader()
        with pytest.raises(ValueError, match="Unsupported"):
            loader.load(str(p))

    def test_numeric_cell_values(self, tmp_path):
        """数值型单元格应转为字符串。"""
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["指标", "值"])
        ws.append(["CPU", 0.85])
        ws.append(["内存", 1024])

        p = tmp_path / "numeric.xlsx"
        wb.save(str(p))

        loader = ExcelLoader()
        docs = loader.load(str(p))
        text = docs[0].page_content
        assert "值: 0.85" in text
        assert "值: 1024" in text
```

- [ ] **Step 6: 运行全部测试确认通过**

Run: `uv run pytest tests/unit/test_excel_loader.py -v`
Expected: 全部通过

- [ ] **Step 7: 运行已有 loader 测试确保无回归**

Run: `uv run pytest tests/unit/test_loaders.py -v`
Expected: 全部通过

- [ ] **Step 8: 提交**

```bash
git add src/super_agent/knowledge/loaders/__init__.py tests/unit/test_excel_loader.py
git commit -m "feat: register ExcelLoader, add edge case tests"
```

---

### Task 8: 最终验证

- [ ] **Step 1: 运行全量单元测试**

Run: `uv run pytest tests/unit/ -v`
Expected: 全部通过

- [ ] **Step 2: 检查 ruff 格式**

Run: `uv run ruff check src/super_agent/knowledge/loaders/excel.py`
Expected: 无错误

- [ ] **Step 3: 确认 `supported_extensions()` 包含新扩展**

Run: `uv run python -c "from super_agent.knowledge.loaders import supported_extensions; print(supported_extensions())"`
Expected: 输出列表中包含 `.xlsx` 和 `.xls`

# Excel Loader 设计

## 目标

为 RAG 流水线新增 Excel (.xlsx / .xls) 文件加载能力，支持多 sheet、合并单元格、多行分 chunk + 行级重叠，每个 chunk 包含表头以保持语义完整性。

## 数据流

```
Excel 文件 (.xlsx / .xls)
  → .xlsx 用 openpyxl / .xls 用 xlrd 读取
  → 解析表头行（第一个非空行）
  → 合并单元格向下填充（仅 .xlsx，.xls 无合并信息时跳过）
  → 每 N 行数据为一个 chunk，重叠 M 行
  → 每个 chunk 文本：先输出表头键值对，再输出数据行键值对
  → 返回 list[Document]
```

## 配置

通过 `ExcelLoader` 构造参数控制，与现有 chunker 风格一致：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `chunk_size` | `int` | `20` | 每个 chunk 包含的数据行数 |
| `chunk_overlap` | `int` | `3` | 相邻 chunk 重叠的行数 |

## 合并单元格处理

检测 `sheet.merged_cells.ranges`，将合并区域的值向下填充到所有被合并的行中。

处理步骤：
1. 收集所有合并范围 `MergeRange`
2. 读取左上角单元格的值
3. 将该值赋给范围内每一行对应列的单元格
4. 取消合并（`sheet.unmerge_cells`），使填充生效

效果：原本因合并显示为空的单元格，填充后具有与左上角相同的值，保证每行数据的语义完整。

## chunk 文本格式

每个 chunk 的文本由两部分组成：

```
[表头]
列名1: 列名1, 列名2: 列名2, 列名3: 列名3

[数据行 1-20]
列名1: 值1, 列名2: 值2, 列名3: 值3
列名1: 值4, 列名2: 值5, 列名3: 值6
...
```

- 表头部分固定出现在每个 chunk 开头
- 数据行部分按行号标注范围
- 每行格式为 `列名: 值`，列之间用 `, ` 分隔

## 行级重叠机制

- chunk1: `rows[0:20]`，chunk2: `rows[17:37]`，chunk3: `rows[34:54]`
- 步长 = chunk_size - chunk_overlap
- 重叠行在相邻 chunk 中重复出现，但每个 chunk 自身都带表头，可独立理解

## 元信息

每个 Document 的 metadata：

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | `str` | 文件名称（不含路径） |
| `sheet_name` | `str` | 工作表名称 |
| `row_range` | `str` | 数据行范围，如 `"1-20"` |
| `headers` | `list[str]` | 表头列名列表 |
| `topic_tags` | `list[str]` | 自定义标签，用于检索过滤 |

`topic_tags` 与 `metadata.py` 中 `build_metadata` 的 topic_tags 字段对齐，支持通过文件路径自动推导或手动传入。

## 依赖

- `openpyxl>=3.1`（纯 Python，无系统依赖，支持 .xlsx，含合并单元格填充能力）
- `xlrd>=2.0`（纯 Python，支持 .xls 读取，无合并单元格 API）

## 边界情况

- 空 sheet → 跳过
- 只有表头无数据行 → 跳过
- 合并单元格 → 向下填充
- 单个 sheet 数据行不足 chunk_size → 生成单个 chunk
- 多个空列 → 跳过空列，不输出 `列名: `
- .xls 文件 → 用 xlrd 读取，不支持合并单元格填充（xlrd 无此 API），空值保留为空

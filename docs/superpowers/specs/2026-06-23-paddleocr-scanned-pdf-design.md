# PaddleOCR 扫描 PDF 支持

## 目标

让 PDFLoader 自动检测扫描页（无文本层），使用 PaddleOCR 提取文本，使扫描版 PDF 也能走完整 RAG 流程。本地开发用 CPU 模式，生产环境用 GPU 模式，通过配置切换。

## 方案

PyMuPDF 渲染页面为图片 → PaddleOCR 识别文字。仅修改 PDFLoader.load()，下游 chunker/embedder/store 无感知。

## 配置层

新增 `OCRSettings`，挂载到 `Settings`：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `True` | 是否启用 OCR |
| `provider` | `str` | `"paddle"` | 预留扩展 |
| `use_gpu` | `bool` | `False` | PaddleOCR 的 use_gpu 参数 |
| `lang` | `str` | `"ch"` | 语言（ch 中英混合、en、japan 等） |
| `page_dpi` | `int` | `200` | PyMuPDF 渲染 DPI |
| `text_threshold` | `float` | `0.5` | 字符数低于 page_pixel_count * 此值视为扫描页 |

环境变量：`OCR_ENABLED`、`OCR_USE_GPU`、`OCR_LANG`、`OCR_PAGE_DPI`、`OCR_TEXT_THRESHOLD`。

## PDFLoader 改造

逐页处理逻辑：

1. `page.get_text()` 提取文本层
2. 文本量 > 阈值 → 正常文本页
3. 文本量 ≤ 阈值 → 扫描页：
   - `page.get_pixmap(dpi=ocr.page_dpi)` 渲染为 PNG
   - 送入 PaddleOCR 识别
   - 按 y 坐标排序、按行拼接 OCR 结果
4. 构建 Document，metadata 增加 `ocr_used: bool`

关键设计：
- PaddleOCR 实例延迟初始化（首次遇到扫描页时创建）
- 实例全局缓存（单例），避免重复加载模型
- 非扫描 PDF 零开销

## 依赖管理

```toml
[project.optional-dependencies]
ml = [
    "sentence-transformers>=3.0",
    "FlagEmbedding>=1.2",
    "paddleocr>=2.7",
    "paddlepaddle>=2.6",
]
gpu = [
    "paddlepaddle-gpu>=2.6",
]
```

安装：CPU `uv sync --extra ml`，GPU `uv sync --extra ml --extra gpu`。

## 错误处理

- PaddleOCR 未安装：import 时捕获 ImportError，扫描页 warning 跳过，不中断流程
- 渲染失败：单页异常 warning 跳过
- 混合 PDF：逐页独立判断，两种模式可混用
- OCR 无结果：跳过该页

## 不在范围内

- 表格结构还原（后续可用 PP-Structure 升级）
- 版面分析增强 chunk 元信息

# PaddleOCR 扫描 PDF 实现计划

## 步骤

### 1. 新增 OCRSettings 配置 — `config.py`
- 新增 `OCRSettings(BaseSettings)` 类，env_prefix=`"SA_OCR_"`
- 字段：`enabled`(bool, True)、`use_gpu`(bool, False)、`lang`(str, "ch")、`page_dpi`(int, 200)、`text_threshold`(float, 0.5)
- 在 `Settings` 中添加 `ocr: OCRConfig = OCRConfig()`
- 在 `_rebuild_sub_configs` 中添加 `self.ocr = OCRConfig()`
- 在 `validate_settings` 中添加 GPU 可用性 warning

### 2. 更新依赖 — `pyproject.toml`
- `ml` 可选组新增 `paddleocr>=2.7`、`paddlepaddle>=2.6`
- 新增 `gpu` 可选组：`paddlepaddle-gpu>=2.6`

### 3. 改造 PDFLoader — `loaders/pdf.py`
- 导入 `settings` 获取 OCR 配置
- 新增 `_is_scanned_page(text, page)` 方法：文本字符数 < 阈值视为扫描页
- 新增 `_get_ocr_engine()` 类方法：延迟初始化 + 单例缓存 PaddleOCR 实例，import 失败时记录 warning 并返回 None
- 新增 `_ocr_page(page)` 方法：fitz 渲染 pixmap → PaddleOCR → 按 y 坐标排序拼接文本
- 修改 `load()` 主循环：
  - 先 `page.get_text()`
  - 若 `_is_scanned_page` 且 OCR enabled 且 engine 可用 → 走 `_ocr_page`
  - Document metadata 增加 `ocr_used: bool`

### 4. 更新单元测试 — `tests/unit/test_loaders.py`
- 新增 `TestPDFLoader` 类：
  - `test_is_scanned_page_blank`：空文本页面判定为扫描页
  - `test_is_scanned_page_normal`：带文本页面判定为正常页
  - `test_ocr_engine_not_installed`：mock import 失败时返回 None
  - `test_load_with_ocr_disabled`：OCR 关闭时扫描页被跳过

### 5. 更新配置测试 — `tests/unit/test_config.py`
- 新增 `test_ocr_defaults`：验证 OCRSettings 默认值
- 新增 `test_ocr_from_env`：验证环境变量覆盖

### 6. 运行测试验证
- `uv run pytest tests/unit/test_config.py tests/unit/test_loaders.py -v`

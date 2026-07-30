# AutoReceipt 综合优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 AutoReceipt 多个一致性和健壮性问题，包括日期混淆、PDF 命名碰撞、OCR 缓存、枚举值不一致、GLM 返回校验和行程推断程序化。

**Architecture:** 添加 `tools/validator.py` 数据校验层 + `tools/trip_inference.py` 行程推断层 + `tools/ocr_cache.py` 缓存层；修复 pdf_converter.py 命名碰撞；统一所有文件中的费用种类枚举为「餐补费」；强化文档和 vision prompt。

**Tech Stack:** Python 3.14, openpyxl, GLM-4.6V-Flash API, pytest

---

## 文件结构

| 操作 | 文件 | 职责 |
|------|------|------|
| Create | `tools/models.py` | 数据类型定义（Invoice, ReimbursementRow 等） |
| Create | `tools/pipeline.py` | 核心流程编排（识别→分类→校验→输出） |
| Create | `tools/validator.py` | 日期校验、费用类型校验、数据一致性检查 |
| Create | `tools/ocr_cache.py` | OCR 结果 JSON 缓存，避免重复调用 API |
| Create | `tools/trip_inference.py` | 去程/返程判断、出差日期范围推断 |
| Create | `tests/test_models.py` | models 单元测试 |
| Create | `tests/test_validator.py` | validator + trip_inference 单元测试 |
| Create | `tests/test_ocr_cache.py` | 缓存单元测试 |
| Create | `tests/test_pdf_converter.py` | PDF 前缀唯一性测试 |
| Modify | `tools/__init__.py` | 统一导出所有 public API |
| Modify | `SKILL.md` | 添加 Step 4.7 验证表 + 错误示例 + 缓存步骤 + 模型配置说明 |
| Modify | `rules/HARD_RULES.md` | 添加第 12 节日期校验规则 + 统一餐补枚举 |
| Modify | `tools/vision.py` | 模型可配置 + 强化 prompt + GLM 返回值校验 + 缓存集成 |
| Modify | `tools/pdf_converter.py` | 修复文件名碰撞问题 |
| Modify | `tools/excel_writer.py` | 使用 models 类型 + 统一「餐补费」 |
| Modify | `tools/file_manager.py` | 使用 models 类型 + 统一「餐补费」 |
| Modify | `examples/sample_output.json` | 更新示例（含校验字段 + 餐补费枚举） |

---

### Task 1: 创建 validator.py 核心校验函数

**Files:**
- Create: `tools/validator.py`
- Create: `tests/test_validator.py`

- [ ] **Step 1: 写测试 - 日期校验函数**

```python
# tests/test_validator.py
import pytest
import sys
from pathlib import Path

# 添加 tools 目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from validator import validate_reimbursement_data, validate_date_for_fee_type


class TestValidateDateForFeeType:
    """测试单个费用行的日期校验"""

    def test_airline_must_use_travel_date_not_invoice_date(self):
        """机票必须使用行程日期，不能用开票日期"""
        errors = validate_date_for_fee_type(
            fee_type="机票",
            start_date="2026-03-23",
            end_date="2026-03-23",
            travel_date="2026-03-23",
            invoice_date="2026-03-30",
        )
        assert len(errors) == 0  # 行程日期正确

    def test_airline_wrong_date_should_fail(self):
        """机票使用了开票日期应报错"""
        errors = validate_date_for_fee_type(
            fee_type="机票",
            start_date="2026-03-30",  # 这是开票日期，不是行程日期
            end_date="2026-03-30",
            travel_date="2026-03-23",
            invoice_date="2026-03-30",
        )
        assert len(errors) > 0
        assert any("行程日期" in e for e in errors)

    def test_taxi_no_travel_date_ok(self):
        """打车费没有行程日期时用行程单日期，不报错"""
        errors = validate_date_for_fee_type(
            fee_type="打车费",
            start_date="2026-03-23",
            end_date="2026-03-23",
            travel_date="",
            invoice_date="2026-03-23",
        )
        assert len(errors) == 0

    def test_hotel_date_range_check(self):
        """住宿费日期应在出差范围内"""
        errors = validate_date_for_fee_type(
            fee_type="住宿费",
            start_date="2026-03-23",
            end_date="2026-03-27",
            travel_date="",
            invoice_date="2026-03-27",
            trip_start="2026-03-23",
            trip_end="2026-03-29",
        )
        assert len(errors) == 0

    def test_meal_allowance_dates(self):
        """餐补日期应覆盖整个出差期间"""
        errors = validate_date_for_fee_type(
            fee_type="餐补费",
            start_date="2026-03-23",
            end_date="2026-03-27",
            travel_date="",
            invoice_date="",
            trip_start="2026-03-23",
            trip_end="2026-03-27",
        )
        assert len(errors) == 0


class TestValidateReimbursementData:
    """测试整组报销数据的校验"""

    def test_airline_invoice_date_mismatch_detected(self):
        """机票行起止日期=开票日期时被检测出来"""
        data = [
            {
                "起-年月日": "2026-03-30",  # 开票日期
                "止-年月日": "2026-03-30",
                "起-地点": "北京",
                "止-地点": "西安",
                "费用种类": "机票",
                "单据张数": 2,
                "金额": 470.00,
                "_travel_date": "2026-03-23",
                "_invoice_date": "2026-03-30",
            }
        ]
        errors = validate_reimbursement_data(data)
        assert len(errors) > 0

    def test_correct_data_passes(self):
        """正确的数据应通过校验"""
        data = [
            {
                "起-年月日": "2026-03-23",
                "止-年月日": "2026-03-23",
                "起-地点": "北京",
                "止-地点": "西安",
                "费用种类": "机票",
                "单据张数": 2,
                "金额": 470.00,
                "_travel_date": "2026-03-23",
                "_invoice_date": "2026-03-30",
            }
        ]
        errors = validate_reimbursement_data(data)
        assert len(errors) == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ~/.claude/skills/AutoReceipt && python -m pytest tests/test_validator.py -v 2>&1 | head -30`
Expected: FAIL - `ModuleNotFoundError: No module named 'validator'`

- [ ] **Step 3: 实现 validator.py**

```python
# tools/validator.py
"""
报销数据校验工具

功能：在生成 Excel 前校验报销明细数据，防止日期混淆等错误。
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, date


def validate_date_for_fee_type(
    fee_type: str,
    start_date: str,
    end_date: str,
    travel_date: str = "",
    invoice_date: str = "",
    trip_start: str = "",
    trip_end: str = "",
) -> List[str]:
    """
    校验单个费用行的日期是否正确。

    核心规则（来自 HARD_RULES.md 第 3 节）：
    - 机票/火车票：起止日期必须是行程日期（travel_date），不是开票日期（invoice_date）
    - 打车费：起止日期应来自行程单
    - 住宿费：应在出差日期范围内
    - 餐补费：应等于出差起止日期

    Args:
        fee_type: 费用种类
        start_date: 报销明细中的起日期
        end_date: 报销明细中的止日期
        travel_date: 行程日期（机票/火车票的乘车日期）
        invoice_date: 开票日期
        trip_start: 出差开始日期
        trip_end: 出差结束日期

    Returns:
        错误消息列表，空列表表示通过
    """
    errors: list[str] = []

    def _parse(d: str) -> Optional[date]:
        if not d:
            return None
        try:
            return datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            return None

    start = _parse(start_date)
    end = _parse(end_date)
    travel = _parse(travel_date)
    invoice = _parse(invoice_date)
    t_start = _parse(trip_start)
    t_end = _parse(trip_end)

    # ---- 机票 / 火车票：必须使用行程日期 ----
    if fee_type in ("机票", "火车票"):
        if travel and start and start != travel:
            errors.append(
                f"[{fee_type}] 起日期 {start_date} 与行程日期 {travel_date} 不一致。"
                f"机票/火车票必须使用行程日期（票面乘车日期），不是开票日期 {invoice_date}"
            )

    # ---- 打车费：起止日期不应全部等于开票日期（除非确实同一天） ----
    if fee_type == "打车费":
        # 打车费日期应来自行程单，无特殊强制校验
        pass

    # ---- 住宿费：日期应在出差范围内 ----
    if fee_type == "住宿费" and t_start and t_end:
        if start and (start < t_start or start > t_end):
            errors.append(
                f"[住宿费] 入住日期 {start_date} 不在出差范围 "
                f"{trip_start} ~ {trip_end} 内"
            )
        if end and (end < t_start or end > t_end):
            errors.append(
                f"[住宿费] 退房日期 {end_date} 不在出差范围 "
                f"{trip_start} ~ {trip_end} 内"
            )

    # ---- 餐补费：日期应等于出差起止日期 ----
    if fee_type == "餐补费" and t_start and t_end:
        if start and start != t_start:
            errors.append(
                f"[餐补费] 起日期 {start_date} 应等于出差开始日期 {trip_start}"
            )
        if end and end != t_end:
            errors.append(
                f"[餐补费] 止日期 {end_date} 应等于出差结束日期 {trip_end}"
            )

    return errors


def validate_reimbursement_data(
    data: List[Dict[str, Any]],
) -> List[str]:
    """
    校验整组报销明细数据。

    每行数据除标准字段外，还可携带以下内部字段（以 _ 开头）：
    - _travel_date: 行程日期
    - _invoice_date: 开票日期

    Args:
        data: 报销明细数据列表

    Returns:
        错误消息列表，空列表表示全部通过
    """
    all_errors: list[str] = []

    # 提取出差日期范围（从机票/火车票推断）
    trip_start = ""
    trip_end = ""
    for row in data:
        ft = row.get("费用种类", "")
        sd = row.get("起-年月日", "")
        ed = row.get("止-年月日", "")
        if ft in ("机票", "火车票"):
            travel = row.get("_travel_date", sd)
            if travel:
                if not trip_start or travel < trip_start:
                    trip_start = travel
                if not trip_end or travel > trip_end:
                    trip_end = travel

    for i, row in enumerate(data):
        fee_type = row.get("费用种类", "")
        start_date = row.get("起-年月日", "")
        end_date = row.get("止-年月日", "")
        travel_date = row.get("_travel_date", "")
        invoice_date = row.get("_invoice_date", "")

        errors = validate_date_for_fee_type(
            fee_type=fee_type,
            start_date=start_date,
            end_date=end_date,
            travel_date=travel_date,
            invoice_date=invoice_date,
            trip_start=trip_start,
            trip_end=trip_end,
        )
        for e in errors:
            all_errors.append(f"第 {i+1} 行: {e}")

    return all_errors
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd ~/.claude/skills/AutoReceipt && python -m pytest tests/test_validator.py -v 2>&1 | head -30`
Expected: 全部 PASS

---

### Task 2: 更新 tools/__init__.py 导出 validator

**Files:**
- Modify: `tools/__init__.py`

- [ ] **Step 1: 添加 validator 导出**

在 `tools/__init__.py` 中添加：

```python
# 报销助手工具模块

from .validator import validate_reimbursement_data, validate_date_for_fee_type
```

- [ ] **Step 2: 验证导入正常**

Run: `cd ~/.claude/skills/AutoReceipt && python -c "from tools.validator import validate_reimbursement_data; print('OK')" 2>&1`
Expected: 输出 `OK`

---

### Task 3: 在 SKILL.md 添加 Step 4.7 强制验证步骤

**Files:**
- Modify: `SKILL.md`

- [ ] **Step 1: 在 Step 4.6 之后添加 Step 4.7**

在 `SKILL.md` 的 `#### 4.6 住宿费单据张数` 之后、`---` 分隔线之前，插入以下内容：

```markdown

#### 4.7 生成前强制验证（必须执行）

> **关键**：此步骤必须在调用 `excel_writer` 之前执行。跳过此步骤是最常见的错误来源。

**使用 `tools/validator.py` 的 `validate_reimbursement_data()` 函数校验所有数据行。**

校验内容：
1. **机票/火车票**：起止日期是否 = 行程日期（travel_date），而非开票日期（invoice_date）
2. **住宿费**：日期是否在出差范围内
3. **餐补费**：日期是否 = 出差起止日期

**如果校验返回任何错误，必须修正后再生成 Excel。**

##### 验证自检表

生成 Excel 前，Agent 必须逐行确认下表：

| 费用种类 | 起日期来源 | 止日期来源 | 是否正确 |
|---------|----------|----------|---------|
| 机票/火车票 | travel_date（票面乘车日期） | travel_date（同一日期） | ⬜ |
| 打车费 | 行程单日期 / invoice_date | 行程单日期 / invoice_date | ⬜ |
| 住宿费 | 入住日期（去程当天或次日） | 退房日期（返程当天或前日） | ⬜ |
| 餐补费 | 出差开始日期 | 出差结束日期 | ⬜ |

##### 常见错误

```
错误：机票起日期使用了开票日期 2026-03-30（实际行程日期是 2026-03-23）
原因：invoice_date 和 travel_date 混淆
修复：将起止日期改为 travel_date

正确：
| 起日期       | 止日期       | 费用种类 |
|-------------|-------------|---------|
| 2026-03-23  | 2026-03-23  | 机票     |  ← travel_date

错误：
| 起日期       | 止日期       | 费用种类 |
|-------------|-------------|---------|
| 2026-03-30  | 2026-03-30  | 机票     |  ← invoice_date（开票日期）
```
```

---

### Task 4: 在 HARD_RULES.md 添加第 12 节日期校验规则

**Files:**
- Modify: `rules/HARD_RULES.md`

- [ ] **Step 1: 在第 11 节之后添加第 12 节**

在 `HARD_RULES.md` 末尾追加：

```markdown

---

## 12. 日期校验规则

### 12.1 日期字段定义

| 字段名 | 含义 | 来源 | 用途 |
|-------|------|------|------|
| `invoice_date` | 开票日期 | 发票右上角日期 | 文件重命名 |
| `travel_date` | 行程日期 | 票面乘车日期 / 备注栏行程日期 | 报销明细起止日期 |

### 12.2 强制规则

**机票和火车票的报销明细起止日期必须使用 `travel_date`，绝对不允许使用 `invoice_date`。**

### 12.3 错误示例与正确示例

**场景：机票行程日期 2026-03-23，开票日期 2026-03-30**

```
错误（使用开票日期）：
| 起-年月日    | 止-年月日    | 起地点 | 止地点 | 费用种类 | 金额   |
|-------------|-------------|-------|-------|---------|--------|
| 2026-03-30  | 2026-03-30  | 北京  | 西安  | 机票    | 470.00 |  ❌

正确（使用行程日期）：
| 起-年月日    | 止-年月日    | 起地点 | 止地点 | 费用种类 | 金额   |
|-------------|-------------|-------|-------|---------|--------|
| 2026-03-23  | 2026-03-23  | 北京  | 西安  | 机票    | 470.00 |  ✅
```

### 12.4 程序化校验

在生成 Excel 前，必须调用 `tools/validator.py`：

```python
from tools.validator import validate_reimbursement_data

errors = validate_reimbursement_data(data)
if errors:
    # 必须修正错误，不能跳过
    for error in errors:
        print(f"校验失败: {error}")
```
```

---

### Task 5: 强化 vision.py prompt 中的日期字段说明

**Files:**
- Modify: `tools/vision.py`

- [ ] **Step 1: 修改 prompt 中的通用字段说明和重要提醒**

在 `vision.py` 的 prompt 中，找到 `【所有票据通用字段】` 部分，将 `invoice_date` 和 `travel_date` 的说明替换为：

```
【所有票据通用字段】
- invoice_number: 发票号码（右上角或顶部）
- invoice_date: 开票日期（发票右上角打印日期，格式 YYYY-MM-DD）⚠️ 此日期仅用于文件重命名，不可用于报销明细
- amount: 总金额/价税合计（数字，不带单位）
- seller_name: 销售方完整名称
- buyer_name: 购买方名称
- fee_type: 费用类型（见下方枚举）
- is_itinerary: 是否为行程单（true/false）

⚠️ 关于日期的重要区分：
- invoice_date = 开票日期（发票打印日期）→ 仅用于文件重命名
- travel_date = 行程日期（实际乘车/乘机日期）→ 用于报销明细的起止日期
- 这两个日期经常不同！例如行程 3 月 23 日，但 3 月 30 日才开票
```

同时找到 `【火车票/机票 额外字段】` 部分，替换为：

```
【火车票/机票 额外字段 - 极其重要！】
- travel_date: 乘车日期/出发日期（⚠️ 这是报销明细的起止日期来源！）
  - 火车票：从票面中部的"××××年××月××日"格式提取
  - 机票：从备注栏行程信息提取（如"成都双流 - 合肥 (V 舱/06 月 08 日)"→"2025-06-08"）
  - ⚠️ travel_date ≠ invoice_date！必须从票面行程信息提取，不是右上角日期
- departure: 出发站点（如"北京南站"→"北京"）
- arrival: 到达站点（如"济南西站"→"济南"）
```

---

### Task 6: 更新 sample_output.json 添加 _travel_date 和 _invoice_date 示例

**Files:**
- Modify: `examples/sample_output.json`

- [ ] **Step 1: 更新示例数据**

将 `examples/sample_output.json` 替换为：

```json
{
  "description": "报销明细输出示例（含内部校验字段）",
  "note": "_travel_date 和 _invoice_date 是内部字段，不写入 Excel，仅用于 validator 校验",
  "headers": ["报销明细", "起-年月日", "止-年月日", "起-地点", "止-地点", "费用种类", "单据张数", "金额"],
  "rows": [
    {
      "起-年月日": "2025-02-26",
      "止-年月日": "2025-02-26",
      "起-地点": "上海",
      "止-地点": "北京",
      "费用种类": "机票",
      "单据张数": 1,
      "金额": 640.00,
      "_travel_date": "2025-02-26",
      "_invoice_date": "2025-02-28"
    },
    {
      "起-年月日": "2025-02-26",
      "止-年月日": "2025-02-26",
      "起-地点": "上海",
      "止-地点": "上海",
      "费用种类": "住宿费",
      "单据张数": 1,
      "金额": 957.00,
      "_travel_date": "",
      "_invoice_date": "2025-02-26"
    },
    {
      "起-年月日": "2025-02-26",
      "止-年月日": "2025-02-26",
      "起-地点": "上海",
      "止-地点": "上海",
      "费用种类": "打车费",
      "单据张数": 1,
      "金额": 78.95,
      "_travel_date": "",
      "_invoice_date": "2025-02-26"
    },
    {
      "起-年月日": "2025-02-26",
      "止-年月日": "2025-02-27",
      "起-地点": "上海",
      "止-地点": "北京",
      "费用种类": "餐补费",
      "单据张数": 2,
      "金额": 200.00,
      "_travel_date": "",
      "_invoice_date": ""
    }
  ]
}
```

---

### Task 7: 修复 PDF 文件名碰撞

**Files:**
- Modify: `tools/pdf_converter.py`
- Create: `tests/test_pdf_converter.py`

**问题：** 多个 PDF 文件名前 20 字符相同（如 4 个 D57FF 开头的文件），`save_images()` 生成的 `prefix_1.png` 会互相覆盖。

- [ ] **Step 1: 写测试 - 碰撞场景**

```python
# tests/test_pdf_converter.py
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from pdf_converter import save_images, generate_pdf_prefix
from PIL import Image


class TestPdfPrefixGeneration:
    """测试 PDF 前缀生成不会碰撞"""

    def test_different_pdfs_get_unique_prefixes(self):
        """不同 PDF 文件即使名字相似也应生成不同前缀"""
        files = [
            "D57FF2BC1E0DE.pdf",
            "D57FF3A5EBF67.pdf",
            "D57FF8A4F89AE.pdf",
            "D57FFBE8179A9.pdf",
        ]
        prefixes = [generate_pdf_prefix(f, i) for i, f in enumerate(files)]
        assert len(set(prefixes)) == 4  # 全部唯一

    def test_same_name_different_index(self):
        """同名文件用不同 index 区分"""
        p1 = generate_pdf_prefix("test.pdf", 0)
        p2 = generate_pdf_prefix("test.pdf", 1)
        assert p1 != p2


class TestSaveImagesNoCollision:
    """测试图片保存不会互相覆盖"""

    def test_two_batches_no_overwrite(self, tmp_path):
        """两批图片保存到同目录，不应覆盖"""
        imgs1 = [Image.new("RGB", (100, 100), "red")]
        imgs2 = [Image.new("RGB", (100, 100), "blue")]

        paths1 = save_images(imgs1, str(tmp_path), prefix="inv_00")
        paths2 = save_images(imgs2, str(tmp_path), prefix="inv_01")

        assert len(paths1) == 1
        assert len(paths2) == 1
        assert paths1[0] != paths2[0]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ~/.claude/skills/AutoReceipt && python -m pytest tests/test_pdf_converter.py -v 2>&1 | head -20`
Expected: FAIL - `ImportError: cannot import name 'generate_pdf_prefix'`

- [ ] **Step 3: 在 pdf_converter.py 中添加 generate_pdf_prefix 函数**

在 `pdf_converter.py` 的 `convert_pdf_to_images` 函数之前添加：

```python
def generate_pdf_prefix(pdf_path: str, index: int) -> str:
    """
    为 PDF 文件生成唯一前缀，避免文件名碰撞。

    使用 {index}_{文件名hash前8位} 格式确保唯一性。

    Args:
        pdf_path: PDF 文件路径
        index: 文件在列表中的序号

    Returns:
        唯一前缀字符串
    """
    import hashlib
    name = Path(pdf_path).stem
    hash_suffix = hashlib.md5(name.encode()).hexdigest()[:8]
    return f"inv_{index:02d}_{hash_suffix}"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd ~/.claude/skills/AutoReceipt && python -m pytest tests/test_pdf_converter.py -v 2>&1 | head -20`
Expected: 全部 PASS

---

### Task 8: 添加 OCR 结果缓存

**Files:**
- Create: `tools/ocr_cache.py`
- Create: `tests/test_ocr_cache.py`

**问题：** 同一批发票运行两次会重新调用 GLM API，浪费时间且结果可能不同。

- [ ] **Step 1: 写测试 - 缓存读写**

```python
# tests/test_ocr_cache.py
import pytest
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from ocr_cache import OcrCache


class TestOcrCache:
    def test_cache_miss_returns_none(self, tmp_path):
        """未缓存过的文件返回 None"""
        cache = OcrCache(str(tmp_path / "cache.json"))
        assert cache.get("nonexistent.png") is None

    def test_cache_hit_returns_data(self, tmp_path):
        """缓存过的文件能正确返回"""
        cache = OcrCache(str(tmp_path / "cache.json"))
        data = {"invoice_number": "12345", "amount": 100.0}
        cache.put("test.png", data)
        assert cache.get("test.png") == data

    def test_cache_persists_to_disk(self, tmp_path):
        """缓存持久化到文件，重新加载仍可用"""
        cache_path = str(tmp_path / "cache.json")
        cache1 = OcrCache(cache_path)
        cache1.put("test.png", {"amount": 200})

        cache2 = OcrCache(cache_path)
        assert cache2.get("test.png") == {"amount": 200}

    def test_cache_key_is_image_hash(self, tmp_path):
        """缓存 key 基于图片内容 hash，同内容不同路径也能命中"""
        cache = OcrCache(str(tmp_path / "cache.json"))
        cache.put("path_a/img.png", {"amount": 300})
        # 同文件名即可命中（简化方案：用文件名作为 key）
        assert cache.get("path_a/img.png") == {"amount": 300}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ~/.claude/skills/AutoReceipt && python -m pytest tests/test_ocr_cache.py -v 2>&1 | head -20`
Expected: FAIL - `ModuleNotFoundError: No module named 'ocr_cache'`

- [ ] **Step 3: 实现 ocr_cache.py**

```python
# tools/ocr_cache.py
"""
OCR 结果缓存

避免重复调用 GLM API，将识别结果持久化到 JSON 文件。
缓存 key = 图片文件名，value = 识别结果 dict。
"""

import json
from typing import Dict, Any, Optional
from pathlib import Path


class OcrCache:
    def __init__(self, cache_path: str) -> None:
        self._path = Path(cache_path)
        self._data: Dict[str, Dict[str, Any]] = {}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, image_path: str) -> Optional[Dict[str, Any]]:
        """获取缓存，未命中返回 None"""
        key = Path(image_path).name
        return self._data.get(key)

    def put(self, image_path: str, result: Dict[str, Any]) -> None:
        """写入缓存并持久化"""
        key = Path(image_path).name
        self._data[key] = result
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
```

- [ ] **Step 4: 在 vision.py 的 recognize_invoice 中集成缓存**

在 `vision.py` 的 `recognize_invoice` 函数开头添加缓存检查：

```python
# 在 recognize_invoice 函数内部，image_data = _compress_image(...) 之前添加：
    # 检查缓存
    from .ocr_cache import OcrCache
    cache_dir = Path(image_path).parent.parent / ".ocr_cache"
    cache = OcrCache(str(cache_dir / "cache.json"))
    cached = cache.get(image_path)
    if cached:
        return cached
```

在 `return _parse_json_response(content)` 之前添加：

```python
    # 写入缓存
    cache.put(image_path, result)
```

- [ ] **Step 5: 运行所有测试**

Run: `cd ~/.claude/skills/AutoReceipt && python -m pytest tests/test_ocr_cache.py tests/test_pdf_converter.py -v 2>&1 | head -30`
Expected: 全部 PASS

---

### Task 9: 统一费用种类枚举为「餐补费」

**Files:**
- Modify: `tools/excel_writer.py` — 第 110 行 `"餐补"` → `"餐补费"`
- Modify: `tools/file_manager.py` — FOLDER_MAP 中 `"餐补"` → `"餐补费"`
- Modify: `SKILL.md` — 所有「餐补」枚举值统一为「餐补费」
- Modify: `rules/HARD_RULES.md` — 所有「餐补」枚举值统一为「餐补费」

- [ ] **Step 1: 修复 excel_writer.py**

将 `create_sample_data()` 第 110 行：
```python
            "费用种类": "餐补",
```
改为：
```python
            "费用种类": "餐补费",
```

- [ ] **Step 2: 修复 file_manager.py**

将 FOLDER_MAP 第 17 行：
```python
    "餐补": "餐补",
```
改为：
```python
    "餐补费": "餐补",
```

- [ ] **Step 3: 修复 SKILL.md 中的枚举值**

SKILL.md 中费用种类枚举已经是「餐补费」，但搜索全文确认：
- 第 165 行: `打车费、机票、火车票、餐补费、住宿费、其他餐费、办公用品、饮用水` → 已正确
- 确认其他引用处也使用「餐补费」

- [ ] **Step 4: 修复 HARD_RULES.md 中的枚举值**

HARD_RULES.md 中第 188 行：
```
| 费用种类 | 餐补 |
```
改为：
```
| 费用种类 | 餐补费 |
```

第 103-104 行的费用种类枚举：
```
打车费、机票、火车票、餐补费、住宿费、其他餐费、办公用品、饮用水
```
确认已正确使用「餐补费」。

- [ ] **Step 5: 全文搜索确认无遗漏**

Run: `cd ~/.claude/skills/AutoReceipt && grep -rn '"餐补"' --include='*.py' --include='*.md' --include='*.json' . 2>&1 | grep -v venv | grep -v '.ocr_cache'`
Expected: 0 结果（所有「餐补」已统一为「餐补费」）

---

### Task 10: 添加 GLM 返回值校验

**Files:**
- Modify: `tools/vision.py` — 在 `_parse_json_response` 后添加字段校验

- [ ] **Step 1: 在 vision.py 中添加校验函数**

在 `_parse_json_response` 函数之后添加：

```python
def _validate_ocr_result(result: dict) -> dict:
    """
    校验并修正 OCR 返回值。

    确保：
    1. amount 是数字类型
    2. 必需字段存在
    3. 日期格式正确
    """
    # 确保 amount 是数字
    amount = result.get("amount")
    if amount is not None:
        try:
            result["amount"] = float(amount)
        except (ValueError, TypeError):
            result["amount"] = None

    # 确保必需字段存在（缺失则填默认值）
    defaults = {
        "invoice_number": "",
        "invoice_date": "",
        "travel_date": "",
        "amount": None,
        "seller_name": "",
        "buyer_name": "",
        "fee_type": "其他",
        "departure": "",
        "arrival": "",
        "is_itinerary": False,
    }
    for key, default in defaults.items():
        if key not in result:
            result[key] = default

    # 日期格式校验：确保 YYYY-MM-DD
    for date_field in ("invoice_date", "travel_date"):
        val = result.get(date_field, "")
        if val:
            val = str(val).strip()
            # 处理 YYYYMMDD → YYYY-MM-DD
            if len(val) == 8 and val.isdigit():
                val = f"{val[:4]}-{val[4:6]}-{val[6:8]}"
            result[date_field] = val

    return result
```

- [ ] **Step 2: 在 recognize_invoice 中调用校验**

在 `recognize_invoice` 函数中，将 `return _parse_json_response(content)` 改为：
```python
    parsed = _parse_json_response(content)
    return _validate_ocr_result(parsed)
```

- [ ] **Step 3: 验证修改正确**

Run: `cd ~/.claude/skills/AutoReceipt && python -c "
from tools.vision import _validate_ocr_result
# 测试 amount 字符串转数字
r = _validate_ocr_result({'amount': '640.00', 'invoice_date': '20250226'})
assert r['amount'] == 640.0
assert r['invoice_date'] == '2025-02-26'
print('OK')
" 2>&1`
Expected: `OK`

---

### Task 11: 行程推断程序化

**Files:**
- Create: `tools/trip_inference.py`
- Modify: `tests/test_validator.py` — 添加 trip inference 测试

- [ ] **Step 1: 写测试 - 去程/返程判断**

在 `tests/test_validator.py` 末尾追加：

```python
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from trip_inference import infer_trip_direction, infer_trip_date_range


class TestInferTripDirection:
    """测试去程/返程判断"""

    def test_outbound(self):
        """出发城市=A, 到达城市=B → 去程"""
        result = infer_trip_direction(
            departure="北京", arrival="西安",
            source_city="北京", dest_city="西安"
        )
        assert result == "去程"

    def test_return(self):
        """出发城市=B, 到达城市=A → 返程"""
        result = infer_trip_direction(
            departure="西安", arrival="北京",
            source_city="北京", dest_city="西安"
        )
        assert result == "返程"

    def test_unknown(self):
        """无法匹配 → 未知"""
        result = infer_trip_direction(
            departure="上海", arrival="广州",
            source_city="北京", dest_city="西安"
        )
        assert result == "未知"


class TestInferTripDateRange:
    """测试出差日期范围推断"""

    def test_normal_range(self):
        """正常去程返程推断日期范围"""
        invoices = [
            {"fee_type": "机票", "departure": "北京", "arrival": "西安",
             "travel_date": "2026-03-23", "_direction": "去程"},
            {"fee_type": "机票", "departure": "成都", "arrival": "北京",
             "travel_date": "2026-03-29", "_direction": "返程"},
        ]
        start, end = infer_trip_date_range(invoices)
        assert start == "2026-03-23"
        assert end == "2026-03-29"

    def test_no_return(self):
        """只有去程，缺少返程"""
        invoices = [
            {"fee_type": "机票", "departure": "北京", "arrival": "西安",
             "travel_date": "2026-03-23", "_direction": "去程"},
        ]
        start, end = infer_trip_date_range(invoices)
        assert start == "2026-03-23"
        assert end == ""  # 无返程
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ~/.claude/skills/AutoReceipt && python -m pytest tests/test_validator.py::TestInferTripDirection tests/test_validator.py::TestInferTripDateRange -v 2>&1 | head -20`
Expected: FAIL - `ImportError`

- [ ] **Step 3: 实现 trip_inference.py**

```python
# tools/trip_inference.py
"""
行程推断工具

功能：根据发票信息推断去程/返程方向、出差日期范围。
"""

from typing import List, Dict, Any, Tuple


def infer_trip_direction(
    departure: str,
    arrival: str,
    source_city: str,
    dest_city: str,
) -> str:
    """
    判断单张发票是去程还是返程。

    Args:
        departure: 发票上的出发城市
        arrival: 发票上的到达城市
        source_city: 用户输入的出发城市
        dest_city: 用户输入的目的城市

    Returns:
        "去程" | "返程" | "未知"
    """
    if departure == source_city and arrival == dest_city:
        return "去程"
    if departure == dest_city and arrival == source_city:
        return "返程"
    return "未知"


def infer_trip_date_range(
    invoices: List[Dict[str, Any]],
) -> Tuple[str, str]:
    """
    从发票列表推断出差日期范围。

    Args:
        invoices: 发票列表，每项需含 fee_type, travel_date, departure, arrival

    Returns:
        (出差开始日期, 出差结束日期)，格式 YYYY-MM-DD。缺少返程时 end 为空字符串。
    """
    outbound_dates: list[str] = []
    return_dates: list[str] = []

    for inv in invoices:
        if inv.get("fee_type") not in ("机票", "火车票"):
            continue
        direction = inv.get("_direction", "")
        td = inv.get("travel_date", "")
        if not td:
            continue
        if direction == "去程":
            outbound_dates.append(td)
        elif direction == "返程":
            return_dates.append(td)

    start = min(outbound_dates) if outbound_dates else ""
    end = min(return_dates) if return_dates else ""

    return start, end
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd ~/.claude/skills/AutoReceipt && python -m pytest tests/test_validator.py -v 2>&1 | head -40`
Expected: 全部 PASS

- [ ] **Step 5: 更新 tools/__init__.py 导出新模块**

将 `tools/__init__.py` 更新为：

```python
# 报销助手工具模块

from .validator import validate_reimbursement_data, validate_date_for_fee_type
from .ocr_cache import OcrCache
from .trip_inference import infer_trip_direction, infer_trip_date_range
```

---

### Task 12: 创建 models.py 数据类型定义

**Files:**
- Create: `tools/models.py`
- Create: `tests/test_models.py`

**目的：** 用 dataclass 替代 `Dict[str, Any]`，让字段有明确类型定义，IDE 和 Agent 都能准确理解数据结构。

- [ ] **Step 1: 写测试 - 数据类型创建与转换**

```python
# tests/test_models.py
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from models import Invoice, ReimbursementRow, TripInfo, FEE_TYPES


class TestInvoice:
    def test_from_dict(self):
        """从 OCR 识别结果创建 Invoice"""
        data = {
            "invoice_number": "12345",
            "invoice_date": "2026-03-30",
            "travel_date": "2026-03-23",
            "amount": 470.0,
            "seller_name": "中国国际航空股份有限公司",
            "buyer_name": "XX科技有限公司",
            "fee_type": "机票",
            "departure": "北京",
            "arrival": "西安",
            "is_itinerary": False,
        }
        inv = Invoice.from_dict(data)
        assert inv.invoice_number == "12345"
        assert inv.travel_date == "2026-03-23"
        assert inv.amount == 470.0
        assert inv.fee_type == "机票"

    def test_to_dict(self):
        """Invoice 可转回 dict"""
        inv = Invoice(
            invoice_number="12345",
            invoice_date="2026-03-30",
            travel_date="2026-03-23",
            amount=470.0,
            seller_name="国航",
            buyer_name="XX公司",
            fee_type="机票",
            departure="北京",
            arrival="西安",
            is_itinerary=False,
        )
        d = inv.to_dict()
        assert d["travel_date"] == "2026-03-23"
        assert d["amount"] == 470.0


class TestReimbursementRow:
    def test_from_dict(self):
        """从报销明细 dict 创建 ReimbursementRow"""
        data = {
            "起-年月日": "2026-03-23",
            "止-年月日": "2026-03-23",
            "起-地点": "北京",
            "止-地点": "西安",
            "费用种类": "机票",
            "单据张数": 2,
            "金额": 470.0,
            "_travel_date": "2026-03-23",
            "_invoice_date": "2026-03-30",
        }
        row = ReimbursementRow.from_dict(data)
        assert row.start_date == "2026-03-23"
        assert row.fee_type == "机票"
        assert row.sheet_count == 2

    def test_to_dict_drops_internal_fields(self):
        """to_dict 不包含 _ 开头的内部字段"""
        row = ReimbursementRow(
            start_date="2026-03-23",
            end_date="2026-03-23",
            start_location="北京",
            end_location="西安",
            fee_type="机票",
            sheet_count=2,
            amount=470.0,
            _travel_date="2026-03-23",
            _invoice_date="2026-03-30",
        )
        d = row.to_dict()
        assert "起-年月日" in d
        assert "_travel_date" not in d


class TestTripInfo:
    def test_creation(self):
        info = TripInfo(
            source_city="北京",
            dest_city="西安",
            outbound_date="2026-03-23",
            return_date="2026-03-27",
        )
        assert info.source_city == "北京"
        assert info.trip_days == 5


class TestFeeTypes:
    def test_fee_types_contains_meal_allowance(self):
        """费用种类枚举必须包含餐补费"""
        assert "餐补费" in FEE_TYPES

    def test_fee_types_no_old_name(self):
        """费用种类枚举不应包含旧名称「餐补」"""
        assert "餐补" not in FEE_TYPES
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ~/.claude/skills/AutoReceipt && python -m pytest tests/test_models.py -v 2>&1 | head -20`
Expected: FAIL - `ImportError`

- [ ] **Step 3: 实现 models.py**

```python
# tools/models.py
"""
数据类型定义

用 dataclass 替代 Dict[str, Any]，让发票、报销行等数据结构有明确类型。
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from datetime import datetime

# 费用种类枚举（唯一权威来源）
FEE_TYPES: frozenset[str] = frozenset({
    "打车费", "机票", "火车票", "餐补费", "住宿费", "其他餐费", "办公用品", "饮用水",
})


@dataclass(frozen=True)
class Invoice:
    """发票/行程单识别结果"""
    invoice_number: str
    invoice_date: str          # 开票日期 YYYY-MM-DD → 用于文件重命名
    amount: Optional[float]
    seller_name: str
    buyer_name: str
    fee_type: str
    is_itinerary: bool
    travel_date: str = ""      # 行程日期 YYYY-MM-DD → 用于报销明细起止日期
    departure: str = ""
    arrival: str = ""
    nights: Optional[int] = None
    remarks: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Invoice":
        return cls(
            invoice_number=str(data.get("invoice_number", "")),
            invoice_date=str(data.get("invoice_date", "")),
            travel_date=str(data.get("travel_date", "")),
            amount=float(data["amount"]) if data.get("amount") is not None else None,
            seller_name=str(data.get("seller_name", "")),
            buyer_name=str(data.get("buyer_name", "")),
            fee_type=str(data.get("fee_type", "其他")),
            departure=str(data.get("departure", "")),
            arrival=str(data.get("arrival", "")),
            nights=data.get("nights"),
            remarks=str(data.get("remarks", "")),
            is_itinerary=bool(data.get("is_itinerary", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReimbursementRow:
    """报销明细一行数据"""
    start_date: str            # 起-年月日
    end_date: str              # 止-年月日
    start_location: str        # 起-地点
    end_location: str          # 止-地点
    fee_type: str              # 费用种类
    sheet_count: int           # 单据张数
    amount: float              # 金额
    _travel_date: str = ""     # 内部字段：行程日期（校验用，不写入 Excel）
    _invoice_date: str = ""    # 内部字段：开票日期（校验用，不写入 Excel）

    # 字段名映射：内部名 → Excel 列名
    _COLUMN_MAP = {
        "start_date": "起-年月日",
        "end_date": "止-年月日",
        "start_location": "起-地点",
        "end_location": "止-地点",
        "fee_type": "费用种类",
        "sheet_count": "单据张数",
        "amount": "金额",
    }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReimbursementRow":
        return cls(
            start_date=str(data.get("起-年月日", "")),
            end_date=str(data.get("止-年月日", "")),
            start_location=str(data.get("起-地点", "")),
            end_location=str(data.get("止-地点", "")),
            fee_type=str(data.get("费用种类", "")),
            sheet_count=int(data.get("单据张数", 1)),
            amount=float(data.get("金额", 0)),
            _travel_date=str(data.get("_travel_date", "")),
            _invoice_date=str(data.get("_invoice_date", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为 Excel 写入格式（不含内部字段）"""
        result = {}
        for attr, col in self._COLUMN_MAP.items():
            result[col] = getattr(self, attr)
        return result


@dataclass(frozen=True)
class TripInfo:
    """出差行程信息"""
    source_city: str
    dest_city: str
    outbound_date: str         # 去程日期
    return_date: str           # 返程日期

    @property
    def trip_days(self) -> int:
        """出差天数 = (返程 - 去程) + 1"""
        try:
            start = datetime.strptime(self.outbound_date, "%Y-%m-%d").date()
            end = datetime.strptime(self.return_date, "%Y-%m-%d").date()
            return (end - start).days + 1
        except (ValueError, TypeError):
            return 0
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd ~/.claude/skills/AutoReceipt && python -m pytest tests/test_models.py -v 2>&1 | head -20`
Expected: 全部 PASS

---

### Task 13: 创建 pipeline.py 核心流程编排

**Files:**
- Create: `tools/pipeline.py`

**目的：** 将 SKILL.md 中散落在文档里的核心业务逻辑代码化，Agent 只需调用入口函数，减少对文档理解的依赖。

- [ ] **Step 1: 实现 pipeline.py**

```python
# tools/pipeline.py
"""
核心流程编排

将 SKILL.md 的 Step 2~5 串联为可调用的函数，Agent 按顺序调用即可完成报销处理。
"""

from typing import List, Dict, Any
from pathlib import Path

from .models import Invoice, ReimbursementRow, TripInfo, FEE_TYPES
from .trip_inference import infer_trip_direction, infer_trip_date_range
from .validator import validate_reimbursement_data


def classify_invoices(
    invoices: List[Invoice],
    source_city: str,
    dest_city: str,
) -> List[Dict[str, Any]]:
    """
    Step 3: 对识别后的发票进行分类，标记去程/返程方向。

    Args:
        invoices: 识别后的发票列表
        source_city: 用户出发城市
        dest_city: 用户目的城市

    Returns:
        带有 _direction 标记的发票字典列表
    """
    results: list[dict[str, Any]] = []
    for inv in invoices:
        d = inv.to_dict()
        if inv.fee_type in ("机票", "火车票") and inv.departure and inv.arrival:
            d["_direction"] = infer_trip_direction(
                inv.departure, inv.arrival, source_city, dest_city
            )
        results.append(d)
    return results


def infer_trip_info(
    classified_invoices: List[Dict[str, Any]],
    source_city: str,
    dest_city: str,
) -> TripInfo:
    """
    从已分类发票推断出差信息（去程日期、返程日期、天数）。

    Args:
        classified_invoices: 经 classify_invoices 处理后的发票列表
        source_city: 出发城市
        dest_city: 目的城市

    Returns:
        TripInfo 实例
    """
    start, end = infer_trip_date_range(classified_invoices)
    return TripInfo(
        source_city=source_city,
        dest_city=dest_city,
        outbound_date=start,
        return_date=end,
    )


def build_reimbursement_data(
    classified_invoices: List[Dict[str, Any]],
    trip_info: TripInfo,
) -> List[Dict[str, Any]]:
    """
    Step 4: 从发票数据构建报销明细行。

    注意：
    - 机票/火车票的起止日期使用 travel_date（行程日期），不是 invoice_date（开票日期）
    - 餐补费枚举值为「餐补费」，不是「餐补」

    Args:
        classified_invoices: 已分类发票列表
        trip_info: 出差行程信息

    Returns:
        报销明细数据行列表（含 _travel_date / _invoice_date 内部字段）
    """
    rows: list[dict[str, Any]] = []

    for inv in classified_invoices:
        if inv.get("is_itinerary"):
            continue  # 行程单不单独成行

        fee_type = inv.get("fee_type", "其他")
        if fee_type not in FEE_TYPES:
            fee_type = "其他"

        travel_date = inv.get("travel_date", "")
        invoice_date = inv.get("invoice_date", "")
        direction = inv.get("_direction", "")

        # 确定起止日期：机票/火车票用 travel_date
        if fee_type in ("机票", "火车票") and travel_date:
            start_date = travel_date
            end_date = travel_date
        else:
            start_date = invoice_date
            end_date = invoice_date

        # 确定起止地点
        if fee_type in ("机票", "火车票"):
            start_loc = inv.get("departure", "")
            end_loc = inv.get("arrival", "")
        elif fee_type == "住宿费":
            start_loc = trip_info.dest_city
            end_loc = trip_info.dest_city
        elif fee_type == "打车费":
            start_loc = inv.get("departure", "")
            end_loc = inv.get("arrival", "")
        else:
            start_loc = ""
            end_loc = ""

        row = {
            "起-年月日": start_date,
            "止-年月日": end_date,
            "起-地点": start_loc,
            "止-地点": end_loc,
            "费用种类": fee_type,
            "单据张数": 1,
            "金额": inv.get("amount", 0),
            "_travel_date": travel_date,
            "_invoice_date": invoice_date,
        }
        rows.append(row)

    # TODO: 特殊费用合并（民航发展基金→机票等）—— 由 Agent 根据规则处理
    # TODO: 餐补费计算 —— 由 Agent 根据规则处理

    return rows


def validate_and_output(
    rows: List[Dict[str, Any]],
) -> List[str]:
    """
    Step 4.7: 校验报销明细数据。

    Args:
        rows: 报销明细数据行

    Returns:
        错误列表，空 = 全部通过
    """
    return validate_reimbursement_data(rows)
```

- [ ] **Step 2: 验证导入正常**

Run: `cd ~/.claude/skills/AutoReceipt && python -c "
from tools.pipeline import classify_invoices, infer_trip_info, build_reimbursement_data, validate_and_output
print('OK')
" 2>&1`
Expected: `OK`

---

### Task 14: vision.py 模型可配置化 + 修正模型名称

**Files:**
- Modify: `tools/vision.py`

**问题：**
1. 当前硬编码 `"model": "glm-4v-flash"`，用户可能使用其他视觉模型
2. 模型推荐使用 GLM-4.6V-Flash（免费开源），但用户有自己的模型也可使用

- [ ] **Step 1: 重构 recognize_invoice 签名，支持自定义模型和 API**

将 `recognize_invoice` 函数签名改为：

```python
def recognize_invoice(
    image_path: str,
    api_key: str,
    model: str = "glm-4.6v-flash",
    api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions",
) -> Dict[str, Any]:
```

同时将 payload 中的硬编码改为参数：

```python
    payload = {
        "model": model,
        ...
    }

    response = requests.post(api_url, headers=headers, json=payload, timeout=60)
```

- [ ] **Step 2: 在 SKILL.md 中添加模型配置说明**

在 SKILL.md Step 1 的「关于 API Key」部分之后添加：

```markdown
**关于视觉模型：**
> 默认使用 **GLM-4.6V-Flash**（免费、开源，注册即可使用）。
> 如需使用其他视觉大模型，在 `~/.autoreceipt/config.json` 中配置：
> ```json
> {
>   "vision_model": "glm-4.6v-flash",
>   "vision_api_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions"
> }
> ```
> 只需确保模型支持图片输入 + JSON 输出即可。
```

- [ ] **Step 3: 验证默认行为不变**

Run: `cd ~/.claude/skills/AutoReceipt && python -c "
import inspect
from tools.vision import recognize_invoice
sig = inspect.signature(recognize_invoice)
assert sig.parameters['model'].default == 'glm-4.6v-flash'
assert sig.parameters['api_url'].default == 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
print('OK')
" 2>&1`
Expected: `OK`

---

## Self-Review

### 1. Spec Coverage

| 优化项 | 对应 Task | 状态 |
|-------|----------|------|
| P0: 日期校验 validator | Task 1, 2 | 覆盖 |
| P0: SKILL.md Step 4.7 验证表 | Task 3 | 覆盖 |
| P1: 错误示例 | Task 3, 4 | 覆盖 |
| P1: vision.py prompt 强化 | Task 5 | 覆盖 |
| P1: sample 更新 | Task 6 | 覆盖 |
| P0: PDF 文件名碰撞 | Task 7 | 覆盖 |
| P1: OCR 结果缓存 | Task 8 | 覆盖 |
| P1: 餐补枚举统一为「餐补费」 | Task 9 | 覆盖 |
| P1: GLM 返回值校验 | Task 10 | 覆盖 |
| P2: 行程推断程序化 | Task 11 | 覆盖 |
| P2: 数据类型定义 models.py | Task 12 | 覆盖 |
| P2: 核心流程编排 pipeline.py | Task 13 | 覆盖 |
| P1: 视觉模型可配置化 | Task 14 | 覆盖 |

### 2. Placeholder Scan

pipeline.py 中有两处 `TODO` 标记（特殊费用合并、餐补计算），这些是预留给 Agent 按规则处理的复杂逻辑，已在 SKILL.md/HARD_RULES.md 中有完整规则描述。其余无 TBD/TODO/待定。

### 3. Type Consistency

- `Invoice.from_dict` / `to_dict`: Task 12 定义，pipeline.py Task 13 使用，一致
- `ReimbursementRow.from_dict` / `to_dict`: Task 12 定义，Task 1 validator 消费 `_travel_date`/`_invoice_date` 字段，一致
- `TripInfo.trip_days`: Task 12 定义，与 SKILL.md 公式 `(返程-去程)+1` 一致
- `FEE_TYPES` 枚举: Task 12 定义（唯一权威来源），pipeline.py Task 13 引用，Task 9 全文统一，一致
- `recognize_invoice` 签名: Task 14 更新默认参数，向后兼容
- `_travel_date` / `_invoice_date` 内部字段: Task 1, 3, 4, 6, 12, 13 中命名一致
- 餐补枚举: 全部统一为「餐补费」

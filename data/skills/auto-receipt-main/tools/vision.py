"""
发票识别工具

使用 GLM-4.6V-Flash 视觉大模型识别发票图片
"""

import base64
import json
import requests
import io
from pathlib import Path
from typing import Dict, Any, Optional
from PIL import Image


def _compress_image(image_path: str, max_width: int = 1500) -> str:
    """
    压缩图片并返回 base64 编码

    Args:
        image_path: 图片文件路径
        max_width: 最大宽度（像素），默认 1500

    Returns:
        base64 编码的图片数据
    """
    img = Image.open(image_path)

    # 调整为更小的尺寸（如果超过最大宽度）
    if img.size[0] > max_width:
        scale = max_width / img.size[0]
        new_size = (max_width, int(img.size[1] * scale))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    # 转换为 RGB（如果必要）
    if img.mode != 'RGB':
        img = img.convert('RGB')

    # 保存到内存并编码
    buffer = io.BytesIO()
    img.save(buffer, format='PNG', optimize=True)
    return base64.b64encode(buffer.getvalue()).decode()


def recognize_invoice(image_path: str, api_key: str) -> Dict[str, Any]:
    """
    识别发票图片，返回结构化信息

    Args:
        image_path: 图片文件路径
        api_key: GLM API Key

    Returns:
        包含以下字段的字典：
        - invoice_number: 发票号码
        - invoice_date: 开票日期 (YYYY-MM-DD)
        - amount: 金额
        - seller_name: 销售方名称
        - buyer_name: 购买方名称
        - fee_type: 费用类型
        - file_type: 文件类型 (发票/行程单/登机牌/水单)
        - departure: 出发地
        - arrival: 到达地
        - remarks: 备注
        - is_itinerary: 是否为行程单 (true/false)
    """
    # 压缩图片并转为 base64（避免大图片超出 API 限制）
    image_data = _compress_image(image_path)

    # 调用 GLM-4.6V API
    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    prompt = """你是一名专业的票据识别专家。请仔细分析这张票据图片，首先判断票据类型，然后提取对应的字段信息。

=================================================================
第一步：判断票据类型（根据视觉特征）
=================================================================

【火车票】特征：
- 标题含"电子发票（铁路电子客票）"或"铁路电子客票"
- 浅蓝色背景
- 有"北京南站→济南西站"格式的站点信息
- 有"二等座"、"车厢号"等座位信息

【机票】特征：
- 增值税专用发票格式（红色框线、税务局章）
- 项目名称含"国内机票款"或"燃油附加费"
- 备注栏包含行程信息（如"成都双流 - 合肥"）

【住宿费】特征：
- 项目名称含"住宿服务"或"住宿费"
- 销售方含"酒店"、"宾馆"字样

【打车费】特征：
- 出租车发票：窄长型纸质机打发票，有"TAXI"字样
- 滴滴发票：项目名称含"运输服务*客运服务费"
- 销售方含"滴滴出行"、"出租车"字样

【餐饮费】特征：
- 项目名称含"餐饮服务"或"餐费"
- 销售方含"餐饮"、"餐厅"、"饭店"字样

【行程单（非发票）】特征：
- 标题明确写有"行程单"、"行程详情"
- 表格形式，包含"序号、车型、上车时间、起点、终点、里程、金额"等列
- 没有"电子发票"字样和税务局监制章

【登机牌】特征：
- 长条形卡片式排版，有磁条或二维码区域
- 标题含"登机牌"、"BOARDING PASS"
- 包含航班号（如 CA1234）、座位号（如 12A）、登机口
- 包含出发地/目的地三字码（如 PEK/SHA）或城市名
- 有乘客姓名
- 不是增值税发票格式，没有税务局章

【水单】特征：
- 酒店出具的消费明细单据
- 标题含"水单"、"宾客账单"、"消费明细"、"收据"
- 列出房费、餐饮等分项明细
- 通常有酒店名称和入住/离店日期
- 不是增值税发票格式，没有税务局章和发票号码

【高速通行费】特征：
- 粉色/橙色通用机打发票
- 有"收费员"、"车道号"、"车次"等字段
- 销售方含"高速公路"字样

【民航发展基金】特征：
- 单独开具的发票
- 金额固定 50 元
- 备注栏包含：起止地点、乘客姓名、航班日期
- 项目名称含"民航发展基金"

【飞机保险费】特征：
- 单独开具的发票
- 销售方为保险公司
- 金额通常 20-30 元
- 项目名称含"保险"字样

【火车票退改签费】特征：
- 销售方为"中国铁路"
- 项目名称含"退票费"、"改签费"字样
- 金额为手续费（通常较小）

【机票代订服务费】特征：
- 销售方为旅行社（如"华程"、"携程"等）
- 项目名称含"代订"、"服务费"、"附加产品"等
- 金额通常较小（50-100元）
- 与机票发票来自同一家旅行社

=================================================================
第二步：根据票据类型提取对应字段
=================================================================

【所有票据通用字段】
- invoice_number: 发票号码（右上角或顶部）
- invoice_date: 开票日期（右上角，格式 YYYY-MM-DD）
- amount: 总金额/价税合计（数字，不带单位）
- seller_name: 销售方完整名称
- buyer_name: 购买方名称
- fee_type: 费用类型（见下方枚举）
- file_type: 文件类型（发票/行程单/登机牌/水单）
- is_itinerary: 是否为行程单（true/false，仅行程单为 true）

【火车票/机票 额外字段】
- travel_date: 乘车日期/出发日期（必填）
  - 火车票：从票面中部的"××××年××月××日"格式提取
  - 机票：从备注栏行程信息提取（如"成都双流 - 合肥 (V 舱/06 月 08 日)"→"2025-06-08"）
- departure: 出发站点（如"北京南站"→"北京"）
- arrival: 到达站点（如"济南西站"→"济南"）

【打车费/行程单 额外字段】
- departure: 上车地点（城市级别，如"四川省成都市双流区..."→"成都"）
- arrival: 下车地点（城市级别）
- travel_date: 行程日期（从上车时间提取）

【住宿费 额外字段】
- nights: 住宿天数（从"数量"字段或发票备注提取）

【登机牌 额外字段】
- travel_date: 登机日期（必填）
- departure: 出发城市（从三字码或城市名提取，如 PEK→北京）
- arrival: 到达城市

【水单 额外字段】
- nights: 住宿天数（如有）
- invoice_number: 水单通常无发票号码，留空

travel_date 填写规则：
- 火车票：必填（票面发车日期）
- 机票：必填（备注栏行程日期）
- 登机牌：必填（登机日期）
- 打车行程单：必填（上车时间）
- 住宿费/其他发票：留空

=================================================================
第三步：返回 JSON 格式（严格遵守）
=================================================================

```json
{
    "invoice_number": "发票号码",
    "invoice_date": "开票日期 YYYY-MM-DD",
    "travel_date": "乘车/行程日期 YYYY-MM-DD，住宿费/其他发票留空",
    "amount": 数字类型,
    "seller_name": "销售方全称",
    "buyer_name": "购买方名称",
    "fee_type": "机票/火车票/打车费/住宿费/餐饮费/高速通行费/民航发展基金/飞机保险费/火车票退改签费/机票代订服务费/其他",
    "file_type": "发票/行程单/登机牌/水单",
    "departure": "出发地（城市名）或空字符串",
    "arrival": "到达地（城市名）或空字符串",
    "nights": 住宿天数或 null,
    "remarks": "备注栏内容或空字符串",
    "is_itinerary": true 或 false
}
```

=================================================================
重要提醒（必须遵守）
=================================================================

1. **日期优先级**：火车票/机票的 travel_date 必须从票面"乘车日期"提取，不是开票日期！
   - 示例："2025 年 11 月 10 日 14:42 开" → travel_date = "2025-11-10"

2. **发票 vs 行程单 vs 登机牌 vs 水单区分（file_type 字段）**：
   - 有"电子发票"字样 + 税务局章 = 发票（file_type="发票"，is_itinerary=false）
   - 标题"行程单" + 表格形式 = 行程单（file_type="行程单"，is_itinerary=true）
   - 长条形卡片 + "登机牌"/"BOARDING PASS" = 登机牌（file_type="登机牌"，is_itinerary=false）
   - 酒店消费明细单 + "水单"/"宾客账单" = 水单（file_type="水单"，is_itinerary=false）

3. **金额格式**：返回数字类型，不要带"元"、"¥"等单位

4. **找不到字段时**：字符串返回空字符串 ""，数字返回 null

5. **城市提取规则**：从地址中提取城市名，如"四川省成都市双流区"→"成都"

请返回 JSON 格式，不要其他多余内容。"""

    payload = {
        "model": "glm-4.6v-flash",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_data}"}
                    }
                ]
            }
        ]
    }

    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()

    result = response.json()
    content = result['choices'][0]['message']['content']

    # 解析 JSON
    return _parse_json_response(content)


def _parse_json_response(content: str) -> dict:
    """解析 JSON 响应"""
    import re

    def try_parse_json(json_str: str):
        try:
            return json.loads(json_str.strip())
        except:
            return None

    # 查找 JSON 块
    if '```json' in content:
        json_str = content.split('```json')[1].split('```')[0]
    elif '```' in content:
        json_str = content.split('```')[1].split('```')[0]
    else:
        json_str = content

    # 移除注释
    json_str = re.sub(r'//[^\n]*', '', json_str)
    json_str = re.sub(r'/\*.*?\*/', '', json_str, flags=re.DOTALL)

    result = try_parse_json(json_str)
    if result:
        return result

    # 备用：提取 { } 之间的内容
    start = content.find('{')
    end = content.rfind('}')
    if start != -1 and end != -1:
        json_str = content[start:end+1]
        json_str = re.sub(r'//[^\n]*', '', json_str)
        json_str = re.sub(r'/\*.*?\*/', '', json_str, flags=re.DOTALL)
        result = try_parse_json(json_str)
        if result:
            return result

    return {}


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法: python vision.py <图片路径> <API_KEY>")
        sys.exit(1)

    image_path = sys.argv[1]
    api_key = sys.argv[2]

    result = recognize_invoice(image_path, api_key)
    print(json.dumps(result, ensure_ascii=False, indent=2))

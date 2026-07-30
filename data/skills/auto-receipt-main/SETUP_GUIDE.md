# AutoReceipt - 安装与使用指南

## 一、获取访问权限

1. 向仓库管理员提供你的 **GitHub 用户名** 或 **邮箱**
2. 等待收到邀请邮件
3. 点击邮件中的 **"Accept invitation"** 接受邀请

---

## 二、克隆仓库

接受邀请后，选择以下方式之一克隆：

```bash
# 方式 1：HTTPS（推荐）
git clone https://github.com/仓库地址/AutoReceipt.git

# 方式 2：SSH（需先配置 SSH Key）
git clone git@github.com:仓库地址/AutoReceipt.git
```

---

## 三、安装依赖

```bash
cd AutoReceipt
pip install -r requirements.txt
```

**依赖列表：**
- `pdf2image` - PDF 转图片
- `openpyxl` - Excel 操作
- `requests` - API 调用
- `Pillow` - 图片处理

**注意**：macOS 用户需安装 poppler：
```bash
brew install poppler
```

---

## 四、配置

### 方式 1：手动创建配置文件

创建 `~/.autoreceipt/config.json`：

```json
{
  "glm_api_key": "你的 GLM API Key",
  "company_name": "你的公司完整名称",
  "user_name": "你的姓名",
  "default_source_city": "",
  "default_dest_city": ""
}
```

### 方式 2：首次运行时交互式配置

直接运行，按提示输入信息即可自动生成配置。

### 获取视觉模型 API Key

**推荐：GLM-4.6V-Flash（免费）**

1. 访问 https://open.bigmodel.cn/
2. 注册/登录
3. 进入控制台 → API Keys → 创建新密钥
4. 复制密钥（**GLM-4.6V-Flash 免费**）

> 如有自己的视觉识别大模型，可在 `~/.autoreceipt/config.json` 中配置相应的 API Key。

---

## 五、使用方式

### 在 Claude Code 中使用

1. 将 `AutoReceipt` 文件夹放入你的项目目录
2. 在 Claude Code 中提及 `SKILL.md` 即可激活报销助手功能

### 工作流程

```
┌────────────────┐
│ 1. 提供发票文件夹 │
└───────┬────────┘
        ↓
┌────────────────┐
│ 2. 确认行程信息 │
│  - 出发城市    │
│  - 到达城市    │
└───────┬────────┘
        ↓
┌────────────────┐
│ 3. 自动识别发票 │
│  - 机票/火车票  │
│  - 打车费      │
│  - 住宿费      │
└───────┬────────┘
        ↓
┌────────────────┐
│ 4. 生成报销明细 │
│  - Excel 表格  │
│  - 重命名文件  │
└────────────────┘
```

---

## 六、输出结果

处理完成后，在发票文件夹中生成：

```
发票文件夹/
└── output/
    └── {出发城市}-{到达城市}/
        ├── 报销明细_输出.xlsx    ← 报销明细表格
        ├── XX科技有限公司-xxx-发票.pdf
        ├── XX科技有限公司-xxx-行程单.pdf
        └── ...
```

---

## 七、费用类型支持

| 类型 | 说明 |
|-----|------|
| 机票 | 含民航发展基金、保险费合并 |
| 火车票 | 含退改签费合并 |
| 打车费 | 含高速通行费合并，支持滴滴/出租车 |
| 住宿费 | 自动识别住宿天数 |
| 餐补费 | 按城市标准自动计算 |

---

## 八、常见问题

### Q: PDF 转换失败？
确保已安装 poppler：
```bash
# macOS
brew install poppler

# Ubuntu
sudo apt-get install poppler-utils
```

### Q: API Key 无效？
- 检查是否复制完整
- 确认账户余额充足（GLM-4.6V-Flash 免费，但需注册）

### Q: 发票识别不准确？
- 确保 PDF 清晰
- 检查是否为标准发票格式

---

## 九、版本说明

| 版本 | 支持范围 |
|-----|---------|
| V1.1 | 差旅报销（机票/火车票/打车/住宿/餐补） |
| V2.0 | 餐饮/招待费用（规划中） |

**V1.1 限制**：每次处理一个出差行程，多个出差需分开处理。

---

## 十、联系与反馈

如有问题或建议，请联系仓库管理员。

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".autoreceipt"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "glm_api_key": "",
    "company_name": "",
    "user_name": "",
    "default_source_city": "",
    "default_dest_city": ""
}

FEE_TYPES = [
    "打车费",
    "机票",
    "火车票",
    "餐补费",
    "住宿费",
    "其他餐费",
    "办公用品",
    "饮用水"
]

def ensure_config_dir():
    """确保配置目录存在"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def load_config() -> dict:
    """加载配置文件"""
    ensure_config_dir()
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            # 合并默认配置
            return {**DEFAULT_CONFIG, **config}
    return DEFAULT_CONFIG.copy()

def save_config(config: dict):
    """保存配置文件"""
    ensure_config_dir()
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def is_first_run() -> bool:
    """检查是否首次运行"""
    config = load_config()
    return not config.get("glm_api_key") or not config.get("company_name") or not config.get("user_name")

def get_output_dir(input_dir: str) -> Path:
    """获取输出目录"""
    input_path = Path(input_dir)
    output_dir = input_path.parent / "output"
    return output_dir

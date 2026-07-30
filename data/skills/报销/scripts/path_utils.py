#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
路径处理工具
支持Windows和Linux路径格式的自动识别和转换
"""

import os
import sys
from pathlib import Path


def normalize_path(path: str) -> str:
    """
    标准化路径格式，支持Windows和Linux路径
    
    参数:
        path: 用户输入的路径字符串
        
    返回:
        标准化后的路径字符串
    """
    if not path:
        raise ValueError("路径不能为空")
    
    # 处理Windows路径中的反斜杠
    if '\\' in path:
        path = path.replace('\\', '/')
    
    # 移除末尾多余的斜杠
    while path.endswith('/'):
        path = path[:-1]
    
    # 使用os.path标准化
    normalized = os.path.normpath(path)
    
    # 确保路径存在
    if not os.path.exists(normalized):
        # 尝试在当前目录下查找
        cwd_path = os.path.join(os.getcwd(), path)
        if os.path.exists(cwd_path):
            normalized = cwd_path
        else:
            raise FileNotFoundError(f"路径不存在: {path}")
    
    return normalized


def is_windows_path(path: str) -> bool:
    """
    判断是否为Windows路径
    
    参数:
        path: 路径字符串
        
    返回:
        True如果是Windows路径，False否则
    """
    # 检查驱动器字母（如 C:）
    if len(path) >= 2 and path[1] == ':':
        return True
    # 检查UNC路径（如 \\server\share）
    if path.startswith('\\\\') or path.startswith('//'):
        return True
    return False


def get_platform() -> str:
    """
    获取当前操作系统平台
    
    返回:
        'windows' 或 'linux'
    """
    if sys.platform.startswith('win'):
        return 'windows'
    else:
        return 'linux'


def ensure_trailing_separator(path: str) -> str:
    """
    确保路径末尾有分隔符
    
    参数:
        path: 路径字符串
        
    返回:
        末尾有分隔符的路径
    """
    if not path.endswith(os.sep):
        path = path + os.sep
    return path


def get_file_list(path: str, extensions: list = None) -> list:
    """
    获取指定路径下的文件列表
    
    参数:
        path: 文件夹路径
        extensions: 可选的文件扩展名过滤列表（如 ['.pdf', '.jpg']）
        
    返回:
        文件路径列表
    """
    normalized_path = normalize_path(path)
    
    if not os.path.isdir(normalized_path):
        raise ValueError(f"路径不是文件夹: {path}")
    
    files = []
    for item in os.listdir(normalized_path):
        item_path = os.path.join(normalized_path, item)
        if os.path.isfile(item_path):
            # 如果指定了扩展名，进行过滤
            if extensions:
                _, ext = os.path.splitext(item)
                if ext.lower() in extensions:
                    files.append(item_path)
            else:
                files.append(item_path)
    
    return sorted(files)


def main():
    """
    命令行测试入口
    """
    if len(sys.argv) < 2:
        print("用法: python path_utils.py <路径>")
        sys.exit(1)
    
    input_path = sys.argv[1]
    
    try:
        normalized = normalize_path(input_path)
        print(f"输入路径: {input_path}")
        print(f"标准化路径: {normalized}")
        print(f"是否Windows路径: {is_windows_path(input_path)}")
        print(f"当前平台: {get_platform()}")
        
        if os.path.isdir(normalized):
            files = get_file_list(normalized)
            print(f"\n目录中的文件 ({len(files)} 个):")
            for f in files:
                print(f"  {f}")
        
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
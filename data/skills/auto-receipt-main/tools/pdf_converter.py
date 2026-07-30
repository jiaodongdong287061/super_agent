"""
PDF 转图片工具

功能：将 PDF 文件转换为 PIL Image 对象列表
"""

from typing import List
from pathlib import Path
from pdf2image import convert_from_path
from PIL import Image


def convert_pdf_to_images(pdf_path: str, dpi: int = 200) -> List[Image.Image]:
    """
    将 PDF 文件转换为图片列表

    Args:
        pdf_path: PDF 文件路径
        dpi: 转换 DPI，默认 200

    Returns:
        PIL Image 对象列表

    Raises:
        FileNotFoundError: PDF 文件不存在
        ValueError: PDF 文件无法转换
    """
    path = Path(pdf_path)

    if not path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    if not path.suffix.lower() == '.pdf':
        raise ValueError(f"不是 PDF 文件: {pdf_path}")

    try:
        images = convert_from_path(str(path), dpi=dpi)
        return images
    except Exception as e:
        raise ValueError(f"PDF 转换失败: {e}")


def save_images(images: List[Image.Image], output_dir: str, prefix: str = "page") -> List[str]:
    """
    保存图片到指定目录

    Args:
        images: PIL Image 对象列表
        output_dir: 输出目录
        prefix: 文件名前缀

    Returns:
        保存的文件路径列表
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    for i, image in enumerate(images):
        filename = f"{prefix}_{i+1}.png"
        filepath = output_path / filename
        image.save(filepath, "PNG")
        saved_paths.append(str(filepath))

    return saved_paths


if __name__ == "__main__":
    # 测试
    import sys

    if len(sys.argv) > 1:
        pdf_file = sys.argv[1]
        print(f"转换: {pdf_file}")

        images = convert_pdf_to_images(pdf_file)
        print(f"共 {len(images)} 页")

        # 保存到当前目录
        saved = save_images(images, "./output", "test")
        print(f"已保存: {saved}")
    else:
        print("用法: python pdf_converter.py <pdf文件路径>")

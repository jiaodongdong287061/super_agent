"""
文件管理工具

功能：发票文件重命名和归类（单城市往返）
"""

import logging
import shutil
import zipfile
from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path


def _format_date(date_str: str) -> str:
    if not date_str:
        return 'unknown'
    cleaned = date_str.replace('-', '').replace('/', '')
    if len(cleaned) == 8 and cleaned.isdigit():
        return cleaned
    return 'unknown'


def _format_amount(amount) -> str:
    try:
        return f"{float(amount):.2f}"
    except (ValueError, TypeError):
        return "0.00"


def _clean_name(name: str) -> str:
    if not name or name.lower() == 'unknown':
        return 'unknown'
    return name.replace('/', '-').replace('\\', '-')


def _rename_train_ticket_no_invoice(invoice_info: Dict[str, Any], user_name: str) -> str:
    """
    火车票命名（无发票号）

    格式：火车票-{用户姓名}-{金额}-{日期YYYYMMDD}-{出发站}-{到达站}.pdf
    """
    name = _clean_name(user_name) if user_name else 'unknown'
    amount = _format_amount(invoice_info.get('amount', 0))
    date = _format_date(invoice_info.get('travel_date') or invoice_info.get('invoice_date', ''))
    departure = _clean_name(invoice_info.get('departure', 'unknown'))
    arrival = _clean_name(invoice_info.get('arrival', 'unknown'))
    return f"火车票-{name}-{amount}-{date}-{departure}-{arrival}.pdf"


def _rename_train_ticket_with_invoice(invoice_info: Dict[str, Any], user_name: str, company_name: str) -> str:
    """
    火车票命名（有发票号）

    格式：火车票-{用户姓名}-{金额}-{发票号码}-{开票日期YYYYMMDD}-{中国铁路}-{购买方名称}.pdf
    """
    name = _clean_name(user_name) if user_name else 'unknown'
    amount = _format_amount(invoice_info.get('amount', 0))
    invoice_number = _clean_name(invoice_info.get('invoice_number', 'unknown'))
    date = _format_date(invoice_info.get('invoice_date', ''))
    buyer = _clean_name(invoice_info.get('buyer_name') or company_name)
    return f"火车票-{name}-{amount}-{invoice_number}-{date}-中国铁路-{buyer}.pdf"


def rename_train_ticket(invoice_info: Dict[str, Any], user_name: str, company_name: str = "") -> str:
    """
    火车票命名规则（自动判断有无发票号）

    无发票号：火车票-{用户姓名}-{金额}-{日期YYYYMMDD}-{出发站}-{到达站}.pdf
    有发票号：火车票-{用户姓名}-{金额}-{发票号码}-{开票日期YYYYMMDD}-{中国铁路}-{购买方名称}.pdf
    """
    invoice_number = invoice_info.get('invoice_number', '')
    if invoice_number and str(invoice_number).lower() != 'unknown':
        return _rename_train_ticket_with_invoice(invoice_info, user_name, company_name)
    return _rename_train_ticket_no_invoice(invoice_info, user_name)


def rename_invoice_file(
    invoice_info: Dict[str, Any],
    company_name: str,
    user_name: str = ""
) -> str:
    """
    根据规则重命名发票文件

    火车票(无发票号)：火车票-{用户姓名}-{金额}-{日期YYYYMMDD}-{出发站}-{到达站}.pdf
    火车票(有发票号)：火车票-{用户姓名}-{金额}-{发票号码}-{开票日期YYYYMMDD}-{中国铁路}-{购买方名称}.pdf
    打车费: 打车费-{金额}-{发票号码}-{开票日期YYYYMMDD}-{销售方名称}-{购买方名称}.pdf
    行程单：打车费-{金额}-行程单.pdf
    机票费：机票费-{金额}-{发票号码}-{开票日期YYYYMMDD}-{销售方名称}-{购买方名称}.pdf
    登机牌：机票费-{金额}-登机牌.pdf
    住宿费：住宿费-{金额}-{发票号码}-{开票日期YYYYMMDD}-{销售方名称}-{购买方名称}.pdf
    水单：住宿费-{金额}-水单.pdf

    Args:
        invoice_info: 发票信息字典
            - invoice_date: 开票日期 (YYYY-MM-DD 或 YYYYMMDD)
            - travel_date: 乘车日期 (火车票专用)
            - invoice_number: 发票号码
            - amount: 金额
            - seller_name: 销售方名称
            - buyer_name: 购买方名称
            - departure: 出发站 (火车票专用)
            - arrival: 到达站 (火车票专用)
            - file_type: 文件类型 (发票/行程单/登机牌/水单)
            - fee_type: 费用类型 (火车票/机票/打车费/住宿费等)
        company_name: 公司名称
        user_name: 用户姓名 (火车票需要)

    Returns:
        新的文件名
    """
    fee_type = invoice_info.get('fee_type', '')
    file_type = invoice_info.get('file_type', '发票')

    if fee_type == '火车票':
        return rename_train_ticket(invoice_info, user_name, company_name)

    if fee_type == '打车费' and file_type == '行程单':
        amount = _format_amount(invoice_info.get('amount', 0))
        return f"打车费-{amount}-行程单.pdf"

    if fee_type == '机票' and file_type == '登机牌':
        amount = _format_amount(invoice_info.get('amount', 0))
        return f"机票费-{amount}-登机牌.pdf"

    if fee_type == '住宿费' and file_type == '水单':
        amount = _format_amount(invoice_info.get('amount', 0))
        return f"住宿费-{amount}-水单.pdf"

    if fee_type == '打车费':
        amount = _format_amount(invoice_info.get('amount', 0))
        invoice_number = _clean_name(invoice_info.get('invoice_number', 'unknown'))
        date = _format_date(invoice_info.get('invoice_date', ''))
        seller = _clean_name(invoice_info.get('seller_name', 'unknown'))
        buyer = _clean_name(invoice_info.get('buyer_name') or company_name)
        return f"打车费-{amount}-{invoice_number}-{date}-{seller}-{buyer}.pdf"

    if fee_type == '机票':
        amount = _format_amount(invoice_info.get('amount', 0))
        invoice_number = _clean_name(invoice_info.get('invoice_number', 'unknown'))
        date = _format_date(invoice_info.get('invoice_date', ''))
        seller = _clean_name(invoice_info.get('seller_name', 'unknown'))
        buyer = _clean_name(invoice_info.get('buyer_name') or company_name)
        return f"机票费-{amount}-{invoice_number}-{date}-{seller}-{buyer}.pdf"

    if fee_type == '住宿费':
        amount = _format_amount(invoice_info.get('amount', 0))
        invoice_number = _clean_name(invoice_info.get('invoice_number', 'unknown'))
        date = _format_date(invoice_info.get('invoice_date', ''))
        seller = _clean_name(invoice_info.get('seller_name', 'unknown'))
        buyer = _clean_name(invoice_info.get('buyer_name') or company_name)
        return f"住宿费-{amount}-{invoice_number}-{date}-{seller}-{buyer}.pdf"

    amount = _format_amount(invoice_info.get('amount', 0))
    invoice_number = _clean_name(invoice_info.get('invoice_number', 'unknown'))
    date = _format_date(invoice_info.get('invoice_date', ''))
    seller = _clean_name(invoice_info.get('seller_name', 'unknown'))
    buyer = _clean_name(invoice_info.get('buyer_name') or company_name)
    return f"{fee_type or '其他'}-{amount}-{invoice_number}-{date}-{seller}-{buyer}.pdf"


def organize_files(
    files: List[Dict[str, Any]],
    output_dir: str,
    company_name: str,
    trip_folder: str = "",
    reimbursement_rows: List[Dict[str, Any]] = None
) -> List[str]:
    """
    重命名并归类文件到单个出差行程文件夹。

    Args:
        files: 文件信息列表，每项包含:
            - source_path: 源文件路径
            - invoice_info: 发票信息字典
            - fee_type: 费用类型
        output_dir: 输出根目录
        company_name: 公司名称
        trip_folder: 行程文件夹名（如 "成都-杭州"）
        reimbursement_rows: 报销明细数据行

    Returns:
        复制的文件路径列表
    """
    from .excel_writer import write_reimbursement_excel

    output_path = Path(output_dir)
    if trip_folder:
        target_dir = output_path / trip_folder
    else:
        target_dir = output_path
    target_dir.mkdir(parents=True, exist_ok=True)

    result = []
    for file_info in files:
        source_path = file_info.get('source_path')
        invoice_info = file_info.get('invoice_info', {})
        fee_type = file_info.get('fee_type', file_info.get('费用种类', '其他'))

        if not source_path:
            continue

        # 餐补费不需要保存文件
        if fee_type == '餐补费':
            continue

        new_filename = rename_invoice_file(invoice_info, company_name)
        target_path = target_dir / new_filename

        try:
            shutil.copy2(source_path, target_path)
            result.append(str(target_path))
        except Exception as e:
            logging.warning(f"复制文件失败: {source_path} -> {target_path}, 错误: {e}")

    # 生成报销明细 Excel
    if reimbursement_rows:
        excel_path = target_dir / "报销明细_输出.xlsx"
        try:
            write_reimbursement_excel(
                reimbursement_rows,
                str(excel_path),
                company_name
            )
        except Exception as e:
            logging.warning(f"生成 Excel 失败: {e}")

    return result


def create_output_structure(output_dir: str, trip_folder: str = "") -> Path:
    """
    创建输出目录结构。

    Args:
        output_dir: 根输出目录
        trip_folder: 行程文件夹名（如 "成都-杭州"）

    Returns:
        行程文件夹路径
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if trip_folder:
        target = output_path / trip_folder
    else:
        target = output_path
    target.mkdir(parents=True, exist_ok=True)

    return target


def pack_output_zip(
    output_dir: str,
    trip_folder: str = "",
    zip_filename: str = None
) -> str:
    """
    将 output 目录打包为 ZIP 文件。

    Args:
        output_dir: 输出根目录（包含行程子文件夹）
        trip_folder: 行程文件夹名（如 "成都-杭州"）
        zip_filename: 自定义 ZIP 文件名（不含路径），默认自动生成

    Returns:
        ZIP 文件的完整路径
    """
    output_path = Path(output_dir)

    if not zip_filename:
        today = datetime.now().strftime('%Y%m%d')
        trip_label = trip_folder if trip_folder else "output"
        zip_filename = f"报销_{trip_label}_{today}.zip"

    zip_path = output_path / zip_filename

    source_dir = output_path / trip_folder if trip_folder else output_path

    if not source_dir.exists():
        raise FileNotFoundError(f"源目录不存在: {source_dir}")

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(source_dir.rglob('*')):
            if file_path.is_file():
                arcname = file_path.relative_to(output_path)
                zf.write(file_path, arcname)

    return str(zip_path)


MINIO_ENDPOINT = "http://10.1.1.13:32543/"
MINIO_ACCESS_KEY = "BT5DdtsO3ISjd7crJtBE"
MINIO_SECRET_KEY = "ziwFkqT5iCbNeEDYHi9DV9qZhViEYAmHSngip1r5"
MINIO_BUCKET = "reimbursement"
MINIO_SECURE = False


def upload_to_minio(
    zip_path: str,
    endpoint: str = MINIO_ENDPOINT,
    access_key: str = MINIO_ACCESS_KEY,
    secret_key: str = MINIO_SECRET_KEY,
    bucket: str = MINIO_BUCKET,
    secure: bool = MINIO_SECURE,
    expires_in: int = 3600
) -> str:
    """
    将 ZIP 文件上传到 MinIO，返回预签名下载链接。

    Args:
        zip_path: ZIP 文件的完整路径
        endpoint: MinIO 端点（如 minio.example.com:9000）
        access_key: Access Key
        secret_key: Secret Key
        bucket: 存储桶名称
        secure: 是否使用 HTTPS，默认 True
        expires_in: 预签名链接有效期（秒），默认 3600（1 小时）

    Returns:
        预签名下载 URL，用户可在浏览器中直接下载
    """
    from minio import Minio
    from datetime import timedelta

    zip_file = Path(zip_path)
    if not zip_file.exists():
        raise FileNotFoundError(f"ZIP 文件不存在: {zip_path}")

    object_name = f"autoreceipt/{zip_file.name}"

    client = Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure
    )

    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)

    client.fput_object(
        bucket,
        object_name,
        str(zip_file),
        content_type="application/zip",
        metadata={"Content-Disposition": f"attachment; filename=\"{zip_file.name}\""}
    )

    download_url = client.presigned_get_object(
        bucket,
        object_name,
        expires=timedelta(seconds=expires_in),
        response_headers={
            "response-content-type": "application/zip",
            "response-content-disposition": f"attachment; filename=\"{zip_file.name}\""
        }
    )

    return download_url


if __name__ == "__main__":
    # 测试
    test_info = {
        'invoice_date': '2025-02-26',
        'invoice_number': '12345678',
        'amount': 640.00,
        'seller_name': '中国国际航空股份有限公司',
        'file_type': '发票'
    }

    filename = rename_invoice_file(test_info, 'XX科技有限公司')
    print(f"生成文件名: {filename}")

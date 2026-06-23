import pytest
from pathlib import Path
from openpyxl import Workbook

from super_agent.knowledge.loaders.excel import ExcelLoader


def _make_xlsx(tmp_path: Path, rows: list[list[str]], headers: list[str], sheet_name: str = "Sheet") -> Path:
    """辅助函数：用 openpyxl 创建一个简单 xlsx 文件并返回路径。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers)
    for row in rows:
        ws.append(row)
    p = tmp_path / "test.xlsx"
    wb.save(str(p))
    return p


class TestExcelLoaderXlsxSingleSheet:
    def test_load_returns_documents(self, tmp_path):
        headers = ["姓名", "部门", "级别"]
        rows = [
            ["张三", "SRE", "P7"],
            ["李四", "DBA", "P6"],
        ]
        p = _make_xlsx(tmp_path, rows, headers)
        loader = ExcelLoader()
        docs = loader.load(str(p))
        assert len(docs) >= 1

    def test_document_contains_header_and_data(self, tmp_path):
        headers = ["姓名", "部门"]
        rows = [["张三", "SRE"]]
        p = _make_xlsx(tmp_path, rows, headers)
        loader = ExcelLoader()
        docs = loader.load(str(p))
        assert len(docs) == 1
        text = docs[0].page_content
        assert "[表头]" in text
        assert "姓名: 姓名" in text
        assert "部门: 部门" in text
        assert "姓名: 张三" in text
        assert "部门: SRE" in text

    def test_metadata_fields(self, tmp_path):
        headers = ["姓名", "部门"]
        rows = [["张三", "SRE"]]
        p = _make_xlsx(tmp_path, rows, headers, sheet_name="员工表")
        loader = ExcelLoader()
        docs = loader.load(str(p))
        meta = docs[0].metadata
        assert meta["source"] == "test.xlsx"
        assert meta["sheet_name"] == "员工表"
        assert meta["headers"] == ["姓名", "部门"]
        assert "row_range" in meta

    def test_supported_extensions(self):
        loader = ExcelLoader()
        assert ".xlsx" in loader.supported_extensions()
        assert ".xls" in loader.supported_extensions()

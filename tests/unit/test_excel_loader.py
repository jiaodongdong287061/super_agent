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


class TestExcelLoaderMergedCells:
    def test_merged_cell_fill_down(self, tmp_path):
        """合并单元格：左上角的值应填充到所有被合并的行。"""
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws.title = "合并测试"
        ws.append(["系统", "指标", "值"])
        # 合并 A2:A4（系统列前 3 行数据）
        ws.append(["生产环境", "CPU", "80%"])
        ws.append([None, "内存", "60%"])
        ws.append([None, "磁盘", "50%"])
        ws.append(["测试环境", "CPU", "30%"])
        ws.merge_cells("A2:A4")

        p = tmp_path / "merged.xlsx"
        wb.save(str(p))

        loader = ExcelLoader()
        docs = loader.load(str(p))
        text = docs[0].page_content
        # 合并填充后，A2-A4 都应为 "生产环境"
        assert text.count("系统: 生产环境") == 3
        assert "系统: 测试环境" in text

    def test_no_merge_in_xls(self, tmp_path):
        """xls 不支持合并填充，空值保留为空。"""
        pass

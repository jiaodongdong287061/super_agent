import pytest
from pathlib import Path
from super_agent.knowledge.tags import parse_tags_yaml, match_file_tags


class TestParseTagsYaml:
    def test_parse_basic(self, tmp_path):
        tags_file = tmp_path / "tags.yaml"
        tags_file.write_text(
            '"网络/防火墙规则.docx": ["网络", "安全", "防火墙"]\n'
            '"运维/巡检手册.pdf": ["运维", "巡检"]\n',
            encoding="utf-8",
        )
        result = parse_tags_yaml(tags_file)
        assert result["网络/防火墙规则.docx"] == ["网络", "安全", "防火墙"]
        assert result["运维/巡检手册.pdf"] == ["运维", "巡检"]

    def test_parse_glob_pattern(self, tmp_path):
        tags_file = tmp_path / "tags.yaml"
        tags_file.write_text(
            '"*.pptx": ["演示文档"]\n',
            encoding="utf-8",
        )
        result = parse_tags_yaml(tags_file)
        assert result["*.pptx"] == ["演示文档"]

    def test_parse_nonexistent_returns_empty(self, tmp_path):
        result = parse_tags_yaml(tmp_path / "nonexistent.yaml")
        assert result == {}


class TestMatchFileTags:
    def test_exact_match(self):
        file_tags = {"raw_docs/test.docx": ["运维"], "*.pptx": ["演示文档"]}
        tags = match_file_tags("raw_docs/test.docx", file_tags)
        assert tags == ["运维"]

    def test_glob_match(self):
        file_tags = {"raw_docs/test.docx": ["运维"], "*.pptx": ["演示文档"]}
        tags = match_file_tags("presentations/intro.pptx", file_tags)
        assert tags == ["演示文档"]

    def test_no_match(self):
        file_tags = {"raw_docs/test.docx": ["运维"]}
        tags = match_file_tags("other/file.pdf", file_tags)
        assert tags == []

    def test_exact_match_priority_over_glob(self):
        file_tags = {"intro.pptx": ["重要"], "*.pptx": ["演示文档"]}
        tags = match_file_tags("intro.pptx", file_tags)
        assert tags == ["重要"]

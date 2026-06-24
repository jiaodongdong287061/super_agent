import pytest
from super_agent.knowledge.metadata import resolve_topic_tags, build_metadata


def test_manual_tags_take_priority():
    tags = resolve_topic_tags(
        file_path="raw_docs/SRE/mysql/runbook.md",
        manual_tags=["mysql", "backup"],
    )
    # 合并：manual_tags + 路径继承（去重）
    assert tags == ["mysql", "backup", "SRE"]


def test_directory_inheritance():
    tags = resolve_topic_tags(file_path="raw_docs/SRE/mysql/runbook.md")
    assert "SRE" in tags
    assert "mysql" in tags


def test_build_metadata_defaults():
    m = build_metadata(file_path="raw_docs/SRE/mysql/runbook.md")
    assert m["doc_source"] == "local_file"
    assert m["department"] == "SRE"
    assert "mysql" in m["topic_tags"]
    assert m["page_numbers"] == []


def test_build_metadata_overrides():
    m = build_metadata(
        file_path="raw_docs/runbook.md",
        doc_type="api_doc",
        department="DBA",
        manual_tags=["mysql"],
    )
    assert m["doc_type"] == "api_doc"
    assert m["department"] == "DBA"
    # manual_tags=["mysql"], 路径无中间目录可继承 → ["mysql"]
    assert m["topic_tags"] == ["mysql"]


def test_resolve_topic_tags_merge_manual_and_inherited():
    """manual_tags 和路径继承应互补合并，去重保序"""
    tags = resolve_topic_tags(
        file_path="raw_docs/SRE/mysql/runbook.md",
        manual_tags=["mysql", "backup"],
    )
    assert tags == ["mysql", "backup", "SRE"]


def test_resolve_topic_tags_no_manual():
    """无 manual_tags 时纯路径继承"""
    tags = resolve_topic_tags(file_path="raw_docs/SRE/mysql/runbook.md")
    assert tags == ["SRE", "mysql"]


def test_resolve_topic_tags_dedup():
    """manual_tags 中已包含路径标签时不重复"""
    tags = resolve_topic_tags(
        file_path="raw_docs/SRE/mysql/runbook.md",
        manual_tags=["SRE", "mysql"],
    )
    assert tags == ["SRE", "mysql"]


def test_resolve_topic_tags_empty_path():
    """路径无中间目录时只返回 manual_tags"""
    tags = resolve_topic_tags(
        file_path="runbook.md",
        manual_tags=["运维"],
    )
    assert tags == ["运维"]


def test_resolve_topic_tags_none_manual():
    """manual_tags=None 时纯路径继承"""
    tags = resolve_topic_tags(
        file_path="raw_docs/SRE/mysql/runbook.md",
        manual_tags=None,
    )
    assert tags == ["SRE", "mysql"]

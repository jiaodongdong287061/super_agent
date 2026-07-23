from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MetadataSchema:
    doc_source: str = "local_file"
    doc_type: str = "runbook"
    department: str = ""
    topic_tags: list[str] = field(default_factory=list)
    system_name: str = ""
    severity: str = "normal"
    created_at: str = ""
    updated_at: str = ""
    chunk_type: str = "text"
    parent_chunk_id: str = ""
    page_numbers: list[int] = field(default_factory=list)
    heading_path: str = ""
    doc_version: str = ""
    doc_level: str = "L1"
    allowed_roles: list[str] = field(default_factory=list)
    allowed_users: list[str] = field(default_factory=list)
    permission_scope: str = "public"
    expiry_date: str = ""
    doc_status: str = "active"


@dataclass
class Chunk:
    id: str
    content: str
    heading_chain: str
    full_text: str
    metadata: dict
    is_overlap: bool = False
    overlap_source_chunk_id: str | None = None
    overlap_ratio: float = 0.0
    sibling_chunk_ids: list[str] = field(default_factory=list)
    page_numbers: list[int] = field(default_factory=list)


@dataclass
class SearchResult:
    chunk: Chunk
    score: float


@dataclass
class Citation:
    chunk_id: str
    source_doc: str
    page_numbers: list[int]
    content_snippet: str


@dataclass
class GeneratedAnswer:
    answer_text: str
    citations: list[Citation]


@dataclass
class ProcessedQuery:
    original: str
    rewritten: str
    expansions: list[str]
    intent: str = ""
    language: str = "zh-CN"


@dataclass
class UserContext:
    user_id: str = ""
    roles: list[str] = field(default_factory=list)
    department: str = ""
    tenant_id: str = ""
    doc_level: str = "L2"

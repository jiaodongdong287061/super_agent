from __future__ import annotations

from pathlib import Path
from datetime import datetime


def resolve_topic_tags(
    file_path: str,
    manual_tags: list[str] | None = None,
) -> list[str]:
    parts = Path(file_path).parts
    inherited = []
    for part in parts[1:-1]:
        if part and part not in inherited:
            inherited.append(part)

    if manual_tags:
        result = list(manual_tags)
        seen = set(result)
        for tag in inherited:
            if tag not in seen:
                result.append(tag)
                seen.add(tag)
        return result

    return inherited


def build_metadata(
    file_path: str,
    doc_source: str = "local_file",
    doc_type: str = "runbook",
    department: str = "",
    manual_tags: list[str] | None = None,
    system_name: str = "",
    severity: str = "normal",
    chunk_type: str = "text",
    page_numbers: list[int] | None = None,
    heading_path: str = "",
    doc_version: str = "",
    allowed_roles: list[str] | None = None,
    allowed_users: list[str] | None = None,
    permission_scope: str = "public",
    expiry_date: str = "",
    doc_status: str = "active",
) -> dict:
    tags = resolve_topic_tags(file_path, manual_tags)

    if not department:
        parts = Path(file_path).parts
        if len(parts) > 2:
            department = parts[1]

    return {
        "file_path": file_path,
        "doc_source": doc_source,
        "doc_type": doc_type,
        "department": department,
        "topic_tags": tags,
        "system_name": system_name,
        "severity": severity,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
        "chunk_type": chunk_type,
        "parent_chunk_id": "",
        "page_numbers": page_numbers or [],
        "heading_path": heading_path,
        "doc_version": doc_version,
        "allowed_roles": allowed_roles or [],
        "allowed_users": allowed_users or [],
        "permission_scope": permission_scope,
        "expiry_date": expiry_date,
        "doc_status": doc_status,
    }

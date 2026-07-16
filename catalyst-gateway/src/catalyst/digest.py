from __future__ import annotations

import hashlib
from typing import Any

import rfc8785


QUERY_DIGEST_FIELDS = (
    "question",
    "target",
    "sql",
    "parameters",
    "expectedColumns",
)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def query_digest(query: dict[str, Any]) -> str:
    payload = {field: query[field] for field in QUERY_DIGEST_FIELDS}
    return canonical_sha256(payload)

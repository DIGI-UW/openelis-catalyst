"""Behavioural smoke tests for the ported deterministic query-parse layer."""

from __future__ import annotations

import json

import pytest

from src.catalyst.query_parse import (
    _candidate_matches_catalog,
    _canonical_target,
    _decode_exact_object,
    _parse_and_apply_patch,
    _parse_candidate,
    _semantic_binding_failures,
)
from src.catalyst.query_schemas import QueryContractError, QueryPatchError


def _extension() -> dict:
    return {
        "target": {
            "dataSource": "openelis",
            "catalogVersion": "analytics-catalog-v1",
            "dialect": "postgres",
        },
        "catalog": {
            "views": [
                {
                    "name": "analytics.lab_result_fact_v1",
                    "fields": [
                        {"name": "patient_id"},
                        {"name": "test_name"},
                        {"name": "result_value"},
                    ],
                }
            ]
        },
        "policy": {"maxRows": 100},
    }


def test_canonical_target_shape():
    assert _canonical_target(_extension()) == {
        "dataSource": "openelis",
        "catalogVersion": "analytics-catalog-v1",
        "dialect": "postgres",
        "approvedViews": ["analytics.lab_result_fact_v1"],
    }


def test_candidate_matches_catalog():
    ext = _extension()
    canonical = _canonical_target(ext)
    assert _candidate_matches_catalog({"status": "ready", "target": canonical}, canonical)
    assert not _candidate_matches_catalog({"status": "ready", "target": {}}, canonical)
    # Non-ready candidates never need to echo the catalog target.
    assert _candidate_matches_catalog({"status": "needs_clarification"}, canonical)


def test_decode_rejects_duplicate_keys_and_non_objects():
    with pytest.raises(QueryContractError, match="repeated JSON key"):
        _decode_exact_object('{"a": 1, "a": 2}', label="t")
    with pytest.raises(QueryContractError, match="not a JSON object"):
        _decode_exact_object("[1, 2]", label="t")
    with pytest.raises(QueryContractError, match="not valid JSON"):
        _decode_exact_object("{not json}", label="t")


def test_parse_candidate_clarification_branch():
    ext = _extension()
    content = json.dumps(
        {"status": "needs_clarification", "clarification": "Which date range?"}
    )
    normalized, binding_normalized = _parse_candidate(
        content, "how many tests?", ext, label="candidate"
    )
    assert normalized["status"] == "needs_clarification"
    assert normalized["clarification"] == "Which date range?"
    assert binding_normalized is False


def test_semantic_binding_failures_empty_without_named_analytes():
    ext = _extension()
    candidate = {"status": "ready", "sql": "SELECT patient_id FROM v", "parameters": []}
    # No semanticDimensions in the catalog => no named-analyte requirements.
    assert _semantic_binding_failures(candidate, "list patients", ext) == []


def test_parse_and_apply_patch_replaces_anchored_sql_text():
    base = {
        "status": "ready",
        "sql": "SELECT a FROM analytics.lab_result_fact_v1 WHERE x = 1",
        "parameters": [],
        "expectedColumns": [{"name": "a"}],
    }
    findings = [{"code": "sql.parse_error", "path": "sql"}]
    patch = json.dumps(
        {
            "patches": [
                {
                    "findingCode": "sql.parse_error",
                    "op": "replace_text",
                    "path": "/sql",
                    "oldValue": "x = 1",
                    "replacement": "x = 2",
                }
            ]
        }
    )
    patched = _parse_and_apply_patch(patch, base, findings, ["/sql"])
    assert patched["sql"].endswith("WHERE x = 2")


def test_parse_and_apply_patch_rejects_out_of_scope_path():
    base = {"status": "ready", "sql": "SELECT 1", "parameters": [], "expectedColumns": []}
    findings = [{"code": "sql.parse_error", "path": "sql"}]
    patch = json.dumps(
        {
            "patches": [
                {
                    "findingCode": "sql.parse_error",
                    "op": "replace_text",
                    "path": "/sql",
                    "oldValue": "SELECT 1",
                    "replacement": "SELECT 2",
                }
            ]
        }
    )
    # Anchor occurs, but the allowed-path set excludes /sql -> out of scope.
    with pytest.raises(QueryPatchError, match="outside the permitted scope"):
        _parse_and_apply_patch(patch, base, findings, [])

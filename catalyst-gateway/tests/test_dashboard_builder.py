from __future__ import annotations

import json
import uuid
import zipfile
from pathlib import Path

from src.catalyst.contracts import ContractRegistry
from src.catalyst.dashboard_builder import DashboardBuilder, compile_parameterized_sql


def _id() -> str:
    return str(uuid.uuid4())


class _Workbench:
    def __init__(self) -> None:
        self.session_id = _id()
        self.turn_id = _id()
        self.version_id = _id()
        self.execution_id = _id()
        self.query_digest = "a" * 64

    def get_session(self, session_id: str):
        if session_id != self.session_id:
            return None
        version = {
            "versionId": self.version_id,
            "ordinal": 1,
            "queryDigest": self.query_digest,
            "sql": "SELECT observed_at, result_value FROM analytics.lab_result_fact_v1 WHERE observed_at >= :since",
            "parameters": [
                {
                    "name": "since",
                    "type": "date",
                    "source": "human",
                    "value": "2026-01-01",
                }
            ],
        }
        return {
            "sessionId": self.session_id,
            "dataSourceId": "openelis",
            "catalogVersion": "analytics-v1",
            "currentVersion": version,
            "executions": [
                {
                    "executionId": self.execution_id,
                    "versionId": self.version_id,
                    "status": "succeeded",
                    "maxRows": 100,
                    "query": {
                        "sql": version["sql"],
                        "parameters": version["parameters"],
                    },
                    "result": {
                        "columns": [
                            {
                                "ordinal": 0,
                                "name": "observed_at",
                                "databaseType": "date",
                                "typeOid": 1082,
                                "logicalType": "date",
                            },
                            {
                                "ordinal": 1,
                                "name": "result_value",
                                "databaseType": "numeric",
                                "typeOid": 1700,
                                "logicalType": "decimal",
                            },
                        ],
                        "rows": [
                            [
                                {"type": "date", "value": "2026-01-01"},
                                {"type": "decimal", "value": "14.2"},
                            ]
                        ],
                        "rowCount": {
                            "returned": 1,
                            "truncated": False,
                            "truncationReason": None,
                        },
                        "warnings": [],
                    },
                }
            ],
        }

    def list_turns(self, session_id: str):
        assert session_id == self.session_id
        return {"currentTurnId": self.turn_id}


def test_compile_parameterized_sql_preserves_typed_literals() -> None:
    assert (
        compile_parameterized_sql(
            "SELECT :day::date, :text, :ids",
            [
                {"name": "day", "type": "date", "value": "2026-01-01"},
                {"name": "text", "type": "string", "value": "O'Brien"},
                {"name": "ids", "type": "integer-list", "value": [2, 3]},
            ],
        )
        == "SELECT DATE '2026-01-01'::date, 'O''Brien', (2, 3)"
    )


def test_saved_lineage_publishes_a_contract_valid_native_bundle(tmp_path: Path) -> None:
    workbench = _Workbench()
    builder = DashboardBuilder(
        tmp_path / "state.sqlite3", workbench=workbench, outbox=tmp_path / "outbox"
    )
    dataset = builder.save_dataset(
        session_id=workbench.session_id,
        execution_id=workbench.execution_id,
        title="Monthly result values",
    )
    widget = builder.save_widget(
        dataset_version_id=dataset["versionId"], title="Result trend"
    )
    dashboard = builder.save_dashboard(
        title="Lab operations", widget_version_ids=[widget["versionId"]]
    )
    publication = builder.publish(dashboard["versionId"])

    ContractRegistry.default().validate(
        "catalyst-superset-bundle-v1.schema.json", publication["manifest"]
    )
    ContractRegistry.default().validate(
        "catalyst-superset-outbox-current-v1.schema.json", publication["pointer"]
    )
    bundle = tmp_path / "outbox" / publication["pointer"]["bundle"]["fileName"]
    assert bundle.is_file()
    assert (
        json.loads((tmp_path / "outbox" / "current.json").read_text())
        == publication["pointer"]
    )
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
    assert any(name.endswith("/metadata.yaml") for name in names)
    assert any("/databases/" in name for name in names)
    assert any("/datasets/" in name for name in names)
    assert any("/charts/" in name for name in names)
    assert any("/dashboards/" in name for name in names)
    assert any(name.endswith("/catalyst/manifest.json") for name in names)

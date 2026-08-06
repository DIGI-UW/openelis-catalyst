from __future__ import annotations

import json
import uuid
import zipfile
from pathlib import Path

import pytest

from src.catalyst.contracts import ContractRegistry
from src.catalyst.dashboard_builder import (
    DashboardBuilder,
    DashboardBuilderError,
    compile_parameterized_sql,
)


def _id() -> str:
    return str(uuid.uuid4())


class _Workbench:
    def __init__(
        self,
        *,
        columns: list[dict[str, object]] | None = None,
        rows: list[list[dict[str, object]]] | None = None,
    ) -> None:
        self.session_id = _id()
        self.turn_id = _id()
        self.version_id = _id()
        self.execution_id = _id()
        self.query_digest = "a" * 64
        self.columns = columns or [
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
        ]
        self.rows = rows or [
            [
                {"type": "date", "value": "2026-01-01"},
                {"type": "decimal", "value": "14.2"},
            ]
        ]

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
                        "columns": self.columns,
                        "rows": self.rows,
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
    assert widget["configuration"]["presentationKind"] == "table"
    assert widget["configuration"]["suggestedKind"] == "time_series_line"
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
        database_member = next(name for name in names if "/databases/" in name)
        dashboard_member = next(name for name in names if "/dashboards/" in name)
        database = json.loads(archive.read(database_member))
        dashboard_asset = json.loads(archive.read(dashboard_member))
    assert "masked_encrypted_extra" not in database
    chart_meta = dashboard_asset["position"]["CHART-0"]["meta"]
    assert (
        chart_meta["uuid"]
        == publication["manifest"]["assetUuids"]["chartsByVersion"][widget["versionId"]]
    )
    assert chart_meta["chartId"] == 0
    assert any(name.endswith("/metadata.yaml") for name in names)
    assert any("/databases/" in name for name in names)
    assert any("/datasets/" in name for name in names)
    assert any("/charts/" in name for name in names)
    assert any("/dashboards/" in name for name in names)
    assert any(name.endswith("/catalyst/manifest.json") for name in names)


def test_unknown_chart_kind_cannot_be_silently_exported_as_a_table(
    tmp_path: Path,
) -> None:
    workbench = _Workbench()
    builder = DashboardBuilder(
        tmp_path / "state.sqlite3", workbench=workbench, outbox=tmp_path / "outbox"
    )
    dataset = builder.save_dataset(
        session_id=workbench.session_id,
        execution_id=workbench.execution_id,
        title="Monthly result values",
    )

    with pytest.raises(
        DashboardBuilderError,
        match="Unsupported presentation kind",
    ):
        builder.save_widget(
            dataset_version_id=dataset["versionId"],
            title="Result trend",
            presentation_kind="radar",
        )


def test_non_table_widgets_require_an_explicit_report_aggregation(
    tmp_path: Path,
) -> None:
    workbench = _Workbench()
    builder = DashboardBuilder(
        tmp_path / "state.sqlite3", workbench=workbench, outbox=tmp_path / "outbox"
    )
    dataset = builder.save_dataset(
        session_id=workbench.session_id,
        execution_id=workbench.execution_id,
        title="Monthly result values",
    )

    with pytest.raises(DashboardBuilderError, match="requires an explicit aggregation"):
        builder.save_widget(
            dataset_version_id=dataset["versionId"],
            title="Result trend",
            presentation_kind="time_series_line",
        )


def test_native_bundle_maps_explicit_aggregations_to_superset_metrics(
    tmp_path: Path,
) -> None:
    columns = [
        {
            "ordinal": 0,
            "name": "observed_at",
            "databaseType": "date",
            "typeOid": 1082,
            "logicalType": "date",
        },
        {
            "ordinal": 1,
            "name": "test_name",
            "databaseType": "text",
            "typeOid": 25,
            "logicalType": "string",
        },
        {
            "ordinal": 2,
            "name": "result_status",
            "databaseType": "text",
            "typeOid": 25,
            "logicalType": "string",
        },
        {
            "ordinal": 3,
            "name": "result_value",
            "databaseType": "numeric",
            "typeOid": 1700,
            "logicalType": "decimal",
        },
    ]
    workbench = _Workbench(
        columns=columns,
        rows=[
            [
                {"type": "date", "value": "2026-01-01"},
                {"type": "string", "value": "Viral Load"},
                {"type": "string", "value": "final"},
                {"type": "decimal", "value": "14.2"},
            ],
            [
                {"type": "date", "value": "2026-02-01"},
                {"type": "string", "value": "CD4"},
                {"type": "string", "value": "preliminary"},
                {"type": "decimal", "value": "17.3"},
            ],
        ],
    )
    builder = DashboardBuilder(
        tmp_path / "state.sqlite3", workbench=workbench, outbox=tmp_path / "outbox"
    )
    dataset = builder.save_dataset(
        session_id=workbench.session_id,
        execution_id=workbench.execution_id,
        title="Monthly laboratory values",
    )
    widgets = [
        builder.save_widget(
            dataset_version_id=dataset["versionId"],
            title="Average result by month",
            presentation_kind="time_series_line",
            aggregation="avg",
        ),
        builder.save_widget(
            dataset_version_id=dataset["versionId"],
            title="Maximum result by test",
            presentation_kind="grouped_bar",
            aggregation="max",
        ),
        builder.save_widget(
            dataset_version_id=dataset["versionId"],
            title="Result composition",
            presentation_kind="proportion_bar",
            aggregation="sum",
        ),
    ]
    dashboard = builder.save_dashboard(
        title="Lab operations",
        widget_version_ids=[widget["versionId"] for widget in widgets],
    )
    publication = builder.publish(dashboard["versionId"])
    bundle = tmp_path / "outbox" / publication["pointer"]["bundle"]["fileName"]

    with zipfile.ZipFile(bundle) as archive:
        charts = {
            json.loads(archive.read(name))["slice_name"]: json.loads(archive.read(name))
            for name in archive.namelist()
            if "/charts/" in name
        }

    line = charts["Average result by month"]
    assert line["viz_type"] == "echarts_timeseries_line"
    assert line["params"]["metrics"][0]["aggregate"] == "AVG"
    assert line["params"]["x_axis"] == "observed_at"
    grouped = charts["Maximum result by test"]
    assert grouped["viz_type"] == "echarts_timeseries_bar"
    assert grouped["params"]["metrics"][0]["aggregate"] == "MAX"
    assert grouped["params"]["x_axis"] == "test_name"
    proportion = charts["Result composition"]
    assert proportion["params"]["stack"] == "Stack"
    assert proportion["params"]["contributionMode"] == "row"
    assert [item["aggregation"] for item in publication["manifest"]["widgets"]] == [
        "avg",
        "max",
        "sum",
    ]

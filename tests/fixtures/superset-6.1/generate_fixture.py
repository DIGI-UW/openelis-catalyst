#!/usr/bin/env python3
"""Generate the reviewed Superset 6.1 five-family golden fixture."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


CATALYST_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(CATALYST_ROOT / "catalyst-gateway"))

from src.catalyst import dashboard_builder as builder_module  # noqa: E402
from src.catalyst.contracts import ContractRegistry  # noqa: E402
from src.catalyst.dashboard_builder import DashboardBuilder  # noqa: E402
from src.catalyst.digest import canonical_sha256  # noqa: E402


FIXED_TIME = "2026-08-06T00:00:00.000Z"
ANALYTICS_URI = (
    "postgresql+psycopg2://catalyst_readonly:demo-readonly-change-me@"
    "analytics-db:5432/catalyst_analytics"
)
ENTITY_IDS = (
    "edb62a6a-cb99-497c-8e58-b4dd2bd7d7ef",
    "89ec9f1b-bfb7-4540-8752-e1aa472ee5c8",
    "7322d1b3-9d88-47d3-86ee-b1ecc87d3fd0",
    "1f5f2a9e-089b-42e0-8b9b-4d471000a1f8",
    "de0a2d43-37b4-4dc5-b943-4ca97e578c8f",
    "3d32a47f-6212-4da1-b6d6-0fbebfed47f9",
    "d08cc7a8-d763-4688-9746-90f22dfe48bc",
    "a33c10c2-6a04-439a-89bb-05786aa09983",
    "4a660e80-f33e-49a5-bf32-3b13fedf3202",
    "74f1f700-7637-43a5-b140-a78df8857163",
    "8ce26426-d38e-432e-bab3-c0ee7e0589af",
    "b1d2ea31-8dc1-43bf-b659-54f7a71a0678",
    "45589634-1f48-44ac-9525-3db46ea6c0bb",
    "e7abfa99-db5e-408f-b1c3-e76475249c1c",
    "d2fd8f1a-7b12-4f0e-a4ec-211758614c3d",
    "4b72da28-af93-4fbe-a02f-7543782dceca",
    "99b2cf5b-1565-4d9c-bfc0-9fee9c20405b",
    "cb3368dd-684f-4dc6-8c9f-24961371ac82",
    "c71be74f-0212-470c-b3ce-cb140f0a879e",
    "e007025e-156b-493a-959d-4671ed95c006",
    "700ac9ec-7f40-4097-a83f-7ab1fa0d618f",
)


class FixtureWorkbench:
    def __init__(self) -> None:
        detailed = self._session(
            session_id="957a0897-dce3-4e58-8562-957ea1f47215",
            turn_id="67afaad3-eb45-4032-acc8-13e24a5f0e5c",
            version_id="44f3e4e2-41e0-424a-abda-5f10594c2ce7",
            execution_id="537d2aa8-3836-4770-b00f-6cd004ab1df0",
            query_digest="1" * 64,
            sql=(
                "SELECT patient_id, result_value, result_unit, observed_at FROM "
                "analytics.lab_result_fact_v1 ORDER BY observed_at DESC, "
                "patient_id ASC LIMIT 10"
            ),
            columns=[
                self._column(0, "patient_id", "text", 25, "string"),
                self._column(1, "result_value", "numeric", 1700, "decimal"),
                self._column(2, "result_unit", "text", 25, "string"),
                self._column(3, "observed_at", "timestamptz", 1184, "date-time"),
            ],
            rows=[
                [
                    {"type": "string", "value": "patient-a"},
                    {"type": "decimal", "value": "45"},
                    {"type": "string", "value": "copies/ml"},
                    {"type": "date-time", "value": "2026-04-27T13:00:00Z"},
                ],
                [
                    {"type": "string", "value": "patient-b"},
                    {"type": "decimal", "value": "9000"},
                    {"type": "string", "value": "copies/ml"},
                    {"type": "date-time", "value": "2026-04-27T12:00:00Z"},
                ],
            ],
        )
        count = self._session(
            session_id="61fb88b3-ba1d-4f58-9cc8-26a7dd8c623f",
            turn_id="5600c777-78c1-4973-bf2e-3d5223418c64",
            version_id="c110097e-12ad-4dae-ae8b-27740493ee8f",
            execution_id="410c04ec-7627-4b07-90f5-292442a0aa4a",
            query_digest="2" * 64,
            sql=(
                "SELECT COUNT(*)::bigint AS result_count FROM "
                "analytics.lab_result_fact_v1"
            ),
            columns=[self._column(0, "result_count", "int8", 20, "integer")],
            rows=[[{"type": "integer", "value": 1152}]],
        )
        self.sessions = {
            detailed["sessionId"]: detailed,
            count["sessionId"]: count,
        }

    @staticmethod
    def _column(
        ordinal: int,
        name: str,
        database_type: str,
        type_oid: int,
        logical_type: str,
    ) -> dict[str, object]:
        return {
            "ordinal": ordinal,
            "name": name,
            "databaseType": database_type,
            "typeOid": type_oid,
            "logicalType": logical_type,
        }

    @staticmethod
    def _session(
        *,
        session_id: str,
        turn_id: str,
        version_id: str,
        execution_id: str,
        query_digest: str,
        sql: str,
        columns: list[dict[str, object]],
        rows: list[list[dict[str, object]]],
    ) -> dict[str, Any]:
        version = {
            "versionId": version_id,
            "ordinal": 1,
            "queryDigest": query_digest,
            "sql": sql,
            "parameters": [],
        }
        return {
            "sessionId": session_id,
            "turnId": turn_id,
            "dataSourceId": "openelis",
            "catalogVersion": "analytics-catalog-v1+fixture",
            "currentVersion": version,
            "executions": [
                {
                    "executionId": execution_id,
                    "versionId": version_id,
                    "status": "succeeded",
                    "maxRows": 100,
                    "query": {"sql": sql, "parameters": []},
                    "result": {
                        "columns": columns,
                        "rows": rows,
                        "rowCount": {
                            "returned": len(rows),
                            "truncated": False,
                            "truncationReason": None,
                        },
                        "warnings": [],
                    },
                }
            ],
        }

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get(session_id)

    def list_turns(self, session_id: str) -> dict[str, str]:
        return {"currentTurnId": self.sessions[session_id]["turnId"]}


def _build(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    workbench = FixtureWorkbench()
    uuid_values = iter(ENTITY_IDS)
    original_uuid4 = builder_module._uuid4
    original_utc_now = builder_module._utc_now
    original_uri = os.environ.get("CATALYST_SUPERSET_ANALYTICS_URI")
    builder_module._uuid4 = lambda: next(uuid_values)
    builder_module._utc_now = lambda: FIXED_TIME
    os.environ["CATALYST_SUPERSET_ANALYTICS_URI"] = ANALYTICS_URI
    try:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            builder = DashboardBuilder(
                temporary_path / "state.sqlite3",
                workbench=workbench,
                outbox=temporary_path / "outbox",
            )
            detailed = builder.save_dataset(
                session_id="957a0897-dce3-4e58-8562-957ea1f47215",
                execution_id="537d2aa8-3836-4770-b00f-6cd004ab1df0",
                title="Detailed laboratory values",
            )
            count = builder.save_dataset(
                session_id="61fb88b3-ba1d-4f58-9cc8-26a7dd8c623f",
                execution_id="410c04ec-7627-4b07-90f5-292442a0aa4a",
                title="Laboratory result count",
            )
            definitions = (
                (detailed, "Latest laboratory results", "table"),
                (count, "Laboratory result count", "big_number"),
                (detailed, "Laboratory values over time", "time_series_line"),
                (detailed, "Laboratory value area", "time_series_area"),
                (detailed, "Values by patient", "grouped_bar"),
                (detailed, "Stacked laboratory values", "stacked_bar"),
                (detailed, "Result composition", "proportion_bar"),
            )
            widgets = [
                builder.save_widget(
                    dataset_version_id=dataset["versionId"],
                    title=title,
                    presentation_kind=kind,
                )
                for dataset, title, kind in definitions
            ]
            dashboard = builder.save_dashboard(
                title="Catalyst Superset 6.1 visualization fixture",
                widget_version_ids=[widget["versionId"] for widget in widgets],
            )
            publication = builder.publish(dashboard["versionId"])
            ContractRegistry.default().validate(
                "catalyst-superset-bundle-v1.schema.json",
                publication["manifest"],
            )
            ContractRegistry.default().validate(
                "catalyst-superset-outbox-current-v1.schema.json",
                publication["pointer"],
            )
            source = (
                temporary_path / "outbox" / publication["pointer"]["bundle"]["fileName"]
            )
            destination = output / "catalyst-dashboard-five-family.zip"
            with zipfile.ZipFile(source) as archive:
                generated_members = {
                    name: archive.read(name) for name in archive.namelist()
                }
            with zipfile.ZipFile(
                destination, mode="w", compression=zipfile.ZIP_STORED
            ) as archive:
                for name, contents in sorted(generated_members.items()):
                    info = zipfile.ZipInfo(name)
                    info.date_time = (2026, 1, 1, 0, 0, 0)
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    archive.writestr(info, contents)
            bundle_bytes = destination.read_bytes()
            with zipfile.ZipFile(destination) as archive:
                members = [
                    {
                        "path": name.split("/", 1)[1],
                        "bytes": len(archive.read(name)),
                        "sha256": hashlib.sha256(archive.read(name)).hexdigest(),
                    }
                    for name in archive.namelist()
                ]
            fixture_input = {
                "fixedTime": FIXED_TIME,
                "entityIds": list(ENTITY_IDS),
                "sessions": workbench.sessions,
                "presentationKinds": [kind for _, _, kind in definitions],
            }
            fixture = {
                "schemaVersion": "catalyst.superset.fixture.v1",
                "generatorRevision": "catalyst-dashboard-builder-mvp.v1",
                "canonicalInputDigest": canonical_sha256(fixture_input),
                "bundleRoot": publication["manifest"]["bundleRoot"],
                "bundleDigest": hashlib.sha256(bundle_bytes).hexdigest(),
                "bytes": len(bundle_bytes),
                "members": members,
                "presentationKinds": [kind for _, _, kind in definitions],
            }
            (output / "fixture.json").write_text(
                json.dumps(fixture, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            builder.close()
        if next(uuid_values, None) is not None:
            raise RuntimeError(
                "The fixture UUID allocation changed; review the fixture."
            )
    finally:
        builder_module._uuid4 = original_uuid4
        builder_module._utc_now = original_utc_now
        if original_uri is None:
            os.environ.pop("CATALYST_SUPERSET_ANALYTICS_URI", None)
        else:
            os.environ["CATALYST_SUPERSET_ANALYTICS_URI"] = original_uri


if __name__ == "__main__":
    destination = (
        Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).parent
    )
    _build(destination)
